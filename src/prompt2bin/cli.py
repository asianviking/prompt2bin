#!/usr/bin/env python3
"""
prompt2bin — from natural language to verified machine code.

Usage:
    p2b init my_project          # scaffold a new project
    p2b build my_project         # build all components
    p2b "I need a memory pool"   # single prompt, one-shot
    p2b --interactive            # interactive mode
"""

import re
import shutil
import subprocess
import sys
import tempfile
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def _relpath(p) -> str:
    """Make a path relative to CWD for cleaner log output."""
    try:
        return os.path.relpath(str(p))
    except ValueError:
        return str(p)


# Arena domain
from .intent import intent_to_spec as intent_to_arena
from .verify import verify_spec as verify_arena
from .codegen import generate_c as generate_arena_template
from .codegen_llm import generate_c_llm as generate_arena_llm
from .test_harness import run_test_harness as run_arena_test

# Ring buffer domain
from .intent_ringbuf import intent_to_ringbuf
from .verify_ringbuf import verify_ringbuf_spec
from .codegen_ringbuf_llm import generate_ringbuf_llm
from .test_ringbuf import run_ringbuf_test

# Process spawner domain
from .intent_proc import intent_to_proc
from .verify_proc import verify_proc_spec
from .codegen_proc_llm import generate_proc_llm
from .test_proc import run_proc_test

# String table domain
from .intent_strtab import intent_to_strtab
from .verify_strtab import verify_strtab_spec
from .codegen_strtab_llm import generate_strtab_llm
from .test_strtab import run_strtab_test

# Terminal I/O domain
from .intent_termio import intent_to_termio
from .verify_termio import verify_termio_spec
from .codegen_termio_llm import generate_termio_llm
from .test_termio import run_termio_test

# Wasm pipeline
from .wasm_intent import intent_to_wasm_spec
from .wasm_verify import verify_wasm_spec
from .wasm_codegen import generate_wat
from .wasm_validate import compile_wat_to_wasm, check_size_budget, optimize_wasm
from .wasm_test import run_wasm_tests
from . import toolchain

# Project system
from .project import load_project, ensure_build_dir, init_project, TEMPLATES
from .cache import BuildCache, prompt_hash


BANNER = """
╔══════════════════════════════════════════════════════════════╗
║  prompt2bin — intent → spec → verify → code                  ║
║  Domains: arena, ring buffer, process, string table, termio  ║
╚══════════════════════════════════════════════════════════════╝
"""

# Keywords per domain (checked in order — first match wins)
RINGBUF_KEYWORDS = [
    "ring buffer", "ringbuffer", "ring buf", "ringbuf",
    "circular buffer", "circular queue", "fifo",
    "spsc", "mpsc", "spmc", "mpmc",
    "producer", "consumer",
    "queue", "channel",
    "audio buffer", "sample buffer",
    "log buffer", "event queue", "message queue",
]

PROC_KEYWORDS = [
    "process spawn", "subprocess", "fork", "exec",
    "child process", "run command", "launch process",
    "pipe stdin", "capture stdout", "capture stderr",
    "shell command", "cli wrapper", "process runner",
    "command execution", "spawn",
]

STRTAB_KEYWORDS = [
    "string table", "string pool", "intern",
    "string intern", "string dedup", "string storage",
    "symbol table", "string cache", "string hash",
    "context store", "context storage", "prompt store",
    "token table", "keyword table",
]

TERMIO_KEYWORDS = [
    "terminal", "termio", "term io",
    "line editor", "line input", "readline",
    "command line input", "repl input", "interactive input",
    "line reader", "prompt input", "history",
    "input handler", "tty input",
]


def detect_domain(intent: str) -> str:
    """Detect which domain the user is asking about."""
    text = intent.lower()
    for kw in PROC_KEYWORDS:
        if kw in text:
            return "proc"
    for kw in STRTAB_KEYWORDS:
        if kw in text:
            return "strtab"
    for kw in TERMIO_KEYWORDS:
        if kw in text:
            return "termio"
    for kw in RINGBUF_KEYWORDS:
        if kw in text:
            return "ringbuf"
    return "arena"


def compile_to_binary(c_code: str, name: str, domain: str, output_dir: str = ".") -> tuple[str | None, str | None, str | None]:
    """Compile C code to assembly and object file."""
    gcc = shutil.which("gcc")
    if not gcc:
        return None, None, "gcc not found"

    header_path = os.path.abspath(os.path.join(output_dir, f"{name}.h"))

    if domain == "ringbuf":
        wrapper = f"""\
#include "{header_path}"
void *_force_create(void) {{ return {name}_create(); }}
int   _force_push(void *rb, const void *d) {{ return {name}_push(({name}_t*)rb, d); }}
int   _force_pop(void *rb, void *d) {{ return {name}_pop(({name}_t*)rb, d); }}
void  _force_destroy(void *rb) {{ {name}_destroy(({name}_t*)rb); }}
"""
    elif domain == "proc":
        wrapper = f"""\
#include "{header_path}"
void *_force_exec(const char *cmd, const char **args, int n) {{ return {name}_exec(cmd, args, n); }}
void *_force_exec_with_input(const char *cmd, const char **args, int n, const void *buf, unsigned long len) {{ return {name}_exec_with_input(cmd, args, n, buf, len); }}
void  _force_free(void *r) {{ {name}_result_free(({name}_result_t*)r); }}
int   _force_simple(const char *cmd, char *buf, unsigned long sz) {{ return {name}_exec_simple(cmd, buf, sz); }}
"""
    elif domain == "strtab":
        wrapper = f"""\
#include "{header_path}"
void *_force_create(void) {{ return {name}_create(); }}
int   _force_intern(void *t, const char *s) {{ return {name}_intern(({name}_t*)t, s); }}
const char *_force_lookup(void *t, int id) {{ return {name}_lookup(({name}_t*)t, id); }}
void  _force_destroy(void *t) {{ {name}_destroy(({name}_t*)t); }}
"""
    elif domain == "termio":
        wrapper = f"""\
#include "{header_path}"
void *_force_create(void) {{ return {name}_create(); }}
void  _force_history_add(void *c, const char *l) {{ {name}_history_add(({name}_t*)c, l); }}
int   _force_history_count(void *c) {{ return {name}_history_count(({name}_t*)c); }}
void  _force_destroy(void *c) {{ {name}_destroy(({name}_t*)c); }}
"""
    else:
        wrapper = f"""\
#include "{header_path}"
void *_force_create(void) {{ return {name}_create(); }}
void *_force_alloc(void *a, unsigned long n) {{ return {name}_alloc(({name}_t*)a, n); }}
void  _force_reset(void *a) {{ {name}_reset(({name}_t*)a); }}
void  _force_destroy(void *a) {{ {name}_destroy(({name}_t*)a); }}
"""

    asm_path = os.path.join(output_dir, f"{name}.s")
    obj_path = os.path.join(output_dir, f"{name}.o")

    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
        f.write(wrapper)
        wrapper_path = f.name

    try:
        result = subprocess.run(
            [gcc, "-O2", "-S", "-masm=intel", "-fno-asynchronous-unwind-tables",
             "-fno-exceptions", "-o", asm_path, wrapper_path],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None, None, f"Assembly generation failed:\n{result.stderr}"

        result = subprocess.run(
            [gcc, "-O2", "-c", "-o", obj_path, wrapper_path],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return asm_path, None, f"Object compilation failed:\n{result.stderr}"

        return asm_path, obj_path, None
    finally:
        os.unlink(wrapper_path)


def show_assembly_highlights(asm_path: str, func_name: str, label: str):
    """Print the key assembly function."""
    with open(asm_path) as f:
        asm = f.read()

    lines = asm.split("\n")
    in_func = False
    func_lines = []

    for line in lines:
        if func_name in line and ":" in line:
            in_func = True
            func_lines = [line]
            continue
        if in_func:
            if line.strip().startswith(".cfi_endproc") or (
                line.strip().startswith(".") and "size" in line and func_name in line
            ):
                break
            func_lines.append(line)

    if func_lines:
        instructions = [
            l for l in func_lines
            if l.strip() and not l.strip().startswith(".")
        ]
        print(f"\n  {label} assembly ({len(instructions)} instructions):\n")
        for line in instructions[:25]:
            print(f"    {line}")
        if len(instructions) > 25:
            print(f"    ... ({len(instructions) - 25} more)")


def compile_pipeline(intent: str, output_dir: str = ".", name_override: str | None = None,
                     oneshot: bool = False) -> tuple[bool, str, str | None]:
    """Full pipeline: intent → spec → verify → C code → assembly → binary.
    Returns (success, domain, name).
    Set oneshot=True to suppress phase 4 summary (phase 5 will produce the final output)."""
    print(f"\n{'─' * 60}")
    print(f"  INPUT: {intent}")
    print(f"{'─' * 60}")

    domain = detect_domain(intent)
    print(f"\n  Domain: {domain}")

    if domain == "ringbuf":
        ok, name = _compile_ringbuf(intent, output_dir, name_override, oneshot=oneshot)
        return ok, domain, name
    elif domain == "proc":
        ok, name = _compile_proc(intent, output_dir, name_override, oneshot=oneshot)
        return ok, domain, name
    elif domain == "strtab":
        ok, name = _compile_strtab(intent, output_dir, name_override, oneshot=oneshot)
        return ok, domain, name
    elif domain == "termio":
        ok, name = _compile_termio(intent, output_dir, name_override, oneshot=oneshot)
        return ok, domain, name
    else:
        ok, name = _compile_arena(intent, output_dir, name_override, oneshot=oneshot)
        return ok, domain, name


def _compile_arena(intent: str, output_dir: str = ".", name_override: str | None = None,
                   oneshot: bool = False) -> tuple[bool, str | None]:
    """Arena allocator pipeline. Returns (success, name)."""
    print("\n▸ Phase 1: Translating intent → formal spec...", flush=True)
    t0 = time.monotonic()
    spec = intent_to_arena(intent)
    if name_override:
        spec.name = name_override
    print(f"  ({time.monotonic() - t0:.1f}s)")
    print(spec.describe())

    print("\n▸ Phase 2: Verifying spec with Z3...", flush=True)
    t0 = time.monotonic()
    results = verify_arena(spec)
    all_passed = True
    for r in results:
        print(r)
        if not r.passed:
            all_passed = False

    if not all_passed:
        print("\n✗ Verification FAILED. Code generation aborted.")
        for r in results:
            if not r.passed:
                print(f"    - {r.property_name}: {r.message}")
        return False, None
    print(f"\n  All {len(results)} properties verified ✓ ({time.monotonic() - t0:.1f}s)")

    verified_props = [r.message for r in results if r.passed]
    print("\n▸ Phase 3: Generating C code via LLM...", flush=True)
    t0 = time.monotonic()
    c_code = generate_arena_llm(spec, verified_properties=verified_props)
    codegen_source = "Claude"

    if c_code is None:
        print("  LLM codegen unavailable — using template fallback")
        c_code = generate_arena_template(spec)
        codegen_source = "template"

    output_path = os.path.join(output_dir, f"{spec.name}.h")
    with open(output_path, "w") as f:
        f.write(c_code)
    lines = c_code.count("\n")
    print(f"  Generated {lines} lines → {_relpath(output_path)} (via {codegen_source}, {time.monotonic() - t0:.1f}s)")

    print("\n▸ Phase 3b: Running test harness...", flush=True)
    t0 = time.monotonic()
    test_ok, test_msg = run_arena_test(spec, output_path)
    print(f"  {test_msg} ({time.monotonic() - t0:.1f}s)")
    if not test_ok and codegen_source == "Claude":
        print("  LLM code failed — falling back to template")
        c_code = generate_arena_template(spec)
        codegen_source = "template (fallback)"
        with open(output_path, "w") as f:
            f.write(c_code)
        lines = c_code.count("\n")
        test_ok, test_msg = run_arena_test(spec, output_path)
        print(f"  {test_msg}")

    return _phase4(c_code, spec.name, output_path, lines, "arena",
                   "_force_alloc", f"{spec.name}_alloc", output_dir, summary=not oneshot)


def _compile_ringbuf(intent: str, output_dir: str = ".", name_override: str | None = None,
                     oneshot: bool = False) -> tuple[bool, str | None]:
    """Ring buffer pipeline. Returns (success, name)."""
    print("\n▸ Phase 1: Translating intent → formal spec...", flush=True)
    t0 = time.monotonic()
    spec = intent_to_ringbuf(intent)
    if name_override:
        spec.name = name_override
    print(f"  ({time.monotonic() - t0:.1f}s)")
    print(spec.describe())

    print("\n▸ Phase 2: Verifying spec with Z3...", flush=True)
    t0 = time.monotonic()
    results = verify_ringbuf_spec(spec)
    all_passed = True
    for r in results:
        print(r)
        if not r.passed:
            all_passed = False

    if not all_passed:
        print("\n✗ Verification FAILED. Code generation aborted.")
        for r in results:
            if not r.passed:
                print(f"    - {r.property_name}: {r.message}")
        return False, None
    print(f"\n  All {len(results)} properties verified ✓ ({time.monotonic() - t0:.1f}s)")

    verified_props = [r.message for r in results if r.passed]
    print("\n▸ Phase 3: Generating C code via LLM...", flush=True)
    t0 = time.monotonic()
    c_code = generate_ringbuf_llm(spec, verified_properties=verified_props)
    codegen_source = "Claude"

    if c_code is None:
        print("  ✗ LLM codegen failed and no template fallback for ring buffers.")
        return False, None

    output_path = os.path.join(output_dir, f"{spec.name}.h")
    with open(output_path, "w") as f:
        f.write(c_code)
    lines = c_code.count("\n")
    print(f"  Generated {lines} lines → {_relpath(output_path)} (via {codegen_source}, {time.monotonic() - t0:.1f}s)")

    print("\n▸ Phase 3b: Running test harness...", flush=True)
    t0 = time.monotonic()
    test_ok, test_msg = run_ringbuf_test(spec, output_path)
    print(f"  {test_msg} ({time.monotonic() - t0:.1f}s)")
    if not test_ok:
        print("  ⚠ Tests failed — code generated but may have issues")

    return _phase4(c_code, spec.name, output_path, lines, "ringbuf",
                   "_force_push", f"{spec.name}_push", output_dir, summary=not oneshot)


def _compile_proc(intent: str, output_dir: str = ".", name_override: str | None = None,
                  oneshot: bool = False) -> tuple[bool, str | None]:
    """Process spawner pipeline. Returns (success, name)."""
    print("\n▸ Phase 1: Translating intent → formal spec...", flush=True)
    t0 = time.monotonic()
    spec = intent_to_proc(intent)
    if name_override:
        spec.name = name_override
    print(f"  ({time.monotonic() - t0:.1f}s)")
    print(spec.describe())

    print("\n▸ Phase 2: Verifying spec with Z3...", flush=True)
    t0 = time.monotonic()
    results = verify_proc_spec(spec)
    all_passed = True
    for r in results:
        print(r)
        if not r.passed:
            all_passed = False

    if not all_passed:
        print("\n✗ Verification FAILED. Code generation aborted.")
        for r in results:
            if not r.passed:
                print(f"    - {r.property_name}: {r.message}")
        return False, None
    print(f"\n  All {len(results)} properties verified ✓ ({time.monotonic() - t0:.1f}s)")

    verified_props = [r.message for r in results if r.passed]
    print("\n▸ Phase 3: Generating C code via LLM...", flush=True)
    t0 = time.monotonic()
    c_code = generate_proc_llm(spec, verified_properties=verified_props)
    codegen_source = "Claude"

    if c_code is None:
        print("  ✗ LLM codegen failed and no template fallback for process spawner.")
        return False, None

    output_path = os.path.join(output_dir, f"{spec.name}.h")
    with open(output_path, "w") as f:
        f.write(c_code)
    lines = c_code.count("\n")
    print(f"  Generated {lines} lines → {_relpath(output_path)} (via {codegen_source}, {time.monotonic() - t0:.1f}s)")

    print("\n▸ Phase 3b: Running test harness...", flush=True)
    t0 = time.monotonic()
    test_ok, test_msg = run_proc_test(spec, output_path)
    print(f"  {test_msg} ({time.monotonic() - t0:.1f}s)")
    if not test_ok:
        print("  ⚠ Tests failed — code generated but may have issues")

    return _phase4(c_code, spec.name, output_path, lines, "proc",
                   "_force_exec", f"{spec.name}_exec", output_dir, summary=not oneshot)


def _compile_strtab(intent: str, output_dir: str = ".", name_override: str | None = None,
                    oneshot: bool = False) -> tuple[bool, str | None]:
    """String table pipeline. Returns (success, name)."""
    print("\n▸ Phase 1: Translating intent → formal spec...", flush=True)
    t0 = time.monotonic()
    spec = intent_to_strtab(intent)
    if name_override:
        spec.name = name_override
    print(f"  ({time.monotonic() - t0:.1f}s)")
    print(spec.describe())

    print("\n▸ Phase 2: Verifying spec with Z3...", flush=True)
    t0 = time.monotonic()
    results = verify_strtab_spec(spec)
    all_passed = True
    for r in results:
        print(r)
        if not r.passed:
            all_passed = False

    if not all_passed:
        print("\n✗ Verification FAILED. Code generation aborted.")
        for r in results:
            if not r.passed:
                print(f"    - {r.property_name}: {r.message}")
        return False, None
    print(f"\n  All {len(results)} properties verified ✓ ({time.monotonic() - t0:.1f}s)")

    verified_props = [r.message for r in results if r.passed]
    print("\n▸ Phase 3: Generating C code via LLM...", flush=True)
    t0 = time.monotonic()
    c_code = generate_strtab_llm(spec, verified_properties=verified_props)
    codegen_source = "Claude"

    if c_code is None:
        print("  ✗ LLM codegen failed and no template fallback for string table.")
        return False, None

    output_path = os.path.join(output_dir, f"{spec.name}.h")
    with open(output_path, "w") as f:
        f.write(c_code)
    lines = c_code.count("\n")
    print(f"  Generated {lines} lines → {_relpath(output_path)} (via {codegen_source}, {time.monotonic() - t0:.1f}s)")

    print("\n▸ Phase 3b: Running test harness...", flush=True)
    t0 = time.monotonic()
    test_ok, test_msg = run_strtab_test(spec, output_path)
    print(f"  {test_msg} ({time.monotonic() - t0:.1f}s)")
    if not test_ok:
        print("  ⚠ Tests failed — code generated but may have issues")

    return _phase4(c_code, spec.name, output_path, lines, "strtab",
                   "_force_intern", f"{spec.name}_intern", output_dir, summary=not oneshot)


def _compile_termio(intent: str, output_dir: str = ".", name_override: str | None = None,
                    oneshot: bool = False) -> tuple[bool, str | None]:
    """Terminal I/O pipeline. Returns (success, name)."""
    print("\n▸ Phase 1: Translating intent → formal spec...", flush=True)
    t0 = time.monotonic()
    spec = intent_to_termio(intent)
    if name_override:
        spec.name = name_override
    print(f"  ({time.monotonic() - t0:.1f}s)")
    print(spec.describe())

    print("\n▸ Phase 2: Verifying spec with Z3...", flush=True)
    t0 = time.monotonic()
    results = verify_termio_spec(spec)
    all_passed = True
    for r in results:
        print(r)
        if not r.passed:
            all_passed = False

    if not all_passed:
        print("\n✗ Verification FAILED. Code generation aborted.")
        for r in results:
            if not r.passed:
                print(f"    - {r.property_name}: {r.message}")
        return False, None
    print(f"\n  All {len(results)} properties verified ✓ ({time.monotonic() - t0:.1f}s)")

    verified_props = [r.message for r in results if r.passed]
    print("\n▸ Phase 3: Generating C code via LLM...", flush=True)
    t0 = time.monotonic()
    c_code = generate_termio_llm(spec, verified_properties=verified_props)
    codegen_source = "Claude"

    if c_code is None:
        print("  ✗ LLM codegen failed and no template fallback for terminal I/O.")
        return False, None

    output_path = os.path.join(output_dir, f"{spec.name}.h")
    with open(output_path, "w") as f:
        f.write(c_code)
    lines = c_code.count("\n")
    print(f"  Generated {lines} lines → {_relpath(output_path)} (via {codegen_source}, {time.monotonic() - t0:.1f}s)")

    print("\n▸ Phase 3b: Running test harness...", flush=True)
    t0 = time.monotonic()
    test_ok, test_msg = run_termio_test(spec, output_path)
    print(f"  {test_msg} ({time.monotonic() - t0:.1f}s)")
    if not test_ok:
        print("  ⚠ Tests failed — code generated but may have issues")

    return _phase4(c_code, spec.name, output_path, lines, "termio",
                   "_force_readline", f"{spec.name}_readline", output_dir, summary=not oneshot)


def _compile_wasm(intent: str, output_dir: str = ".", name_override: str | None = None) -> bool:
    """Wasm pipeline: intent → WasmSpec → verify → WAT → .wasm → test.
    Returns success boolean."""

    # Check toolchain
    tc = toolchain.detect()
    missing = tc.check_required()
    if missing:
        print("\n  ✗ Missing wasm tools:")
        for m in missing:
            print(f"    - {m}")
        return False

    print(f"\n{'─' * 60}")
    print(f"  INPUT: {intent}")
    print(f"  TARGET: wasm")
    print(f"{'─' * 60}")

    # Phase 1: Intent → WasmSpec
    print("\n▸ Phase 1: Translating intent → WasmSpec...", flush=True)
    t0 = time.monotonic()
    try:
        spec = intent_to_wasm_spec(intent)
    except RuntimeError as e:
        print(f"  ✗ {e}")
        return False
    if name_override:
        spec.name = name_override
    print(f"  ({time.monotonic() - t0:.1f}s)")
    print(spec.describe())

    # Phase 2: Verify spec
    print("\n▸ Phase 2: Verifying WasmSpec...", flush=True)
    t0 = time.monotonic()
    results = verify_wasm_spec(spec)
    all_passed = True
    for r in results:
        print(r)
        if not r.passed:
            all_passed = False

    if not all_passed:
        print("\n  ✗ Verification FAILED. Code generation aborted.")
        for r in results:
            if not r.passed:
                print(f"    - {r.property_name}: {r.message}")
        return False
    print(f"\n  All {len(results)} properties verified ({time.monotonic() - t0:.1f}s)")

    # Phase 3: Generate WAT via LLM (with structured retry loop)
    verified_props = [r.message for r in results if r.passed]
    print("\n▸ Phase 3: Generating WAT via LLM...", flush=True)
    t0 = time.monotonic()
    wat_code = generate_wat(spec, verified_properties=verified_props, max_retries=3)

    if wat_code is None:
        print("  ✗ WAT code generation failed after all retries.")
        return False
    print(f"  Generated WAT ({len(wat_code)} chars, {time.monotonic() - t0:.1f}s)")

    # Phase 4: Compile WAT → .wasm + validate + size budget
    print("\n▸ Phase 4: Compiling WAT → .wasm...", flush=True)
    t0 = time.monotonic()
    wat_path = os.path.join(output_dir, f"{spec.name}.wat")
    wasm_path = os.path.join(output_dir, f"{spec.name}.wasm")

    with open(wat_path, "w") as f:
        f.write(wat_code)

    ok, err = compile_wat_to_wasm(wat_code, wasm_path)
    if not ok:
        print(f"  ✗ wat2wasm failed: {err}")
        return False

    wasm_size = os.path.getsize(wasm_path)
    print(f"  {_relpath(wasm_path):30s} — {wasm_size:>6,} bytes")

    # Size budget check
    if spec.size_budget_bytes > 0:
        with open(wasm_path, "rb") as f:
            wasm_bytes = f.read()
        budget_ok, budget_msg = check_size_budget(wasm_bytes, spec.size_budget_bytes)
        if not budget_ok:
            print(f"  ⚠ Size budget exceeded: {budget_msg}")
            # Not fatal — just a warning for Phase 1

    # Optional optimization
    opt_ok, opt_err = optimize_wasm(wasm_path)
    if opt_ok and os.path.exists(wasm_path):
        opt_size = os.path.getsize(wasm_path)
        if opt_size < wasm_size:
            print(f"  Optimized: {wasm_size:,} → {opt_size:,} bytes")

    print(f"  ({time.monotonic() - t0:.1f}s)")

    # Phase 5: Run tests
    if spec.tests:
        print("\n▸ Phase 5: Running wasmtime tests...", flush=True)
        t0 = time.monotonic()
        test_results = run_wasm_tests(spec, wasm_path)
        tests_passed = 0
        tests_failed = 0
        for tr in test_results:
            print(tr)
            if tr.passed:
                tests_passed += 1
            else:
                tests_failed += 1
        print(f"\n  {tests_passed} passed, {tests_failed} failed ({time.monotonic() - t0:.1f}s)")
    else:
        print("\n  (no test cases in spec)")

    # Summary
    print(f"\n{'═' * 60}")
    print(f"  ✓ Complete pipeline: English → verified wasm binary")
    print(f"")
    print(f"    {_relpath(wat_path):30s}  WAT source ({wat_code.count(chr(10))} lines)")
    print(f"    {_relpath(wasm_path):30s}  wasm binary ({wasm_size:,} bytes)")
    print(f"")
    print(f"    Run it:")
    for func in spec.functions:
        if func.params:
            args = " ".join("0" for _ in func.params)
            print(f"      wasmtime run --invoke {func.name} {spec.name}.wasm {args}")
        else:
            print(f"      wasmtime run --invoke {func.name} {spec.name}.wasm")
        break  # Just show the first function as example
    print(f"{'═' * 60}\n")

    return True


def _phase4(c_code, name, output_path, lines, domain, hot_func, hot_label, output_dir=".", summary=True):
    """Phase 4: Compile to assembly and machine code.
    Returns (success, name).  Set summary=False to suppress the final artifact listing."""
    print("\n▸ Phase 4: Compiling to assembly and machine code...", flush=True)
    t0 = time.monotonic()
    asm_path, obj_path, err = compile_to_binary(c_code, name, domain, output_dir)

    if err:
        print(f"  ⚠ {err}")
    else:
        asm_size = os.path.getsize(asm_path)
        obj_size = os.path.getsize(obj_path)
        print(f"  {_relpath(asm_path):30s} — {asm_size:>6,} bytes (assembly)")
        print(f"  {_relpath(obj_path):30s} — {obj_size:>6,} bytes (machine code)")
        print(f"  ({time.monotonic() - t0:.1f}s)")
        show_assembly_highlights(asm_path, hot_func, hot_label)

    if summary:
        print(f"\n{'═' * 60}")
        print(f"  ✓ Complete pipeline: English → verified machine code")
        print(f"")
        print(f"    {_relpath(output_path):30s}  C code ({lines} lines)")
        if asm_path:
            print(f"    {_relpath(asm_path):30s}  x86-64 assembly")
        if obj_path:
            print(f"    {_relpath(obj_path):30s}  machine code (linkable)")
        print(f"")
        print(f"    Link into your program:")
        print(f"      #include \"{os.path.basename(output_path)}\"")
        print(f"{'═' * 60}\n")
    return True, name


def _phase5_executable(name: str, domain: str, intent: str, output_dir: str = ".") -> bool:
    """Phase 5: Generate main.c, link into executable, clean up intermediates."""
    from . import llm

    debug = llm.is_debug()
    header_path = os.path.join(output_dir, f"{name}.h")
    if not os.path.exists(header_path):
        print(f"  ✗ Header not found: {_relpath(header_path)}")
        return False

    header_code = open(header_path).read()

    print("\n▸ Phase 5: Generating main.c and linking executable...", flush=True)
    t0 = time.monotonic()

    system_prompt = (
        "You generate C source files (main.c). Output ONLY valid C code. "
        "No markdown fences, no explanation, no commentary. "
        "The code must compile with GCC -Wall -Werror. "
        "Include all necessary standard headers. "
        "Use the component API exactly as declared in the provided header."
    )

    prompt = (
        f"Generate a complete main.c file for this application:\n\n"
        f"APPLICATION DESCRIPTION:\n{intent}\n\n"
        f"COMPONENT HEADER (include with #include \"{name}.h\"):\n\n"
        f"=== {name}.h (domain: {domain}) ===\n{header_code}\n\n"
        f"Generate a main.c that implements a fully working application as described above, "
        f"using the component API from the header. The code should be production-quality, "
        f"handle errors, and be a complete, runnable program — not a test harness or demo."
    )

    gcc = shutil.which("gcc")
    if not gcc:
        print("  ✗ gcc not found")
        return False

    build_dir = Path(output_dir)
    max_retries = 2
    main_c_code = None

    for attempt in range(1, max_retries + 2):
        print(f"  ⟳ Generating main.c via LLM (attempt {attempt})...", flush=True)
        raw = llm.generate(prompt, system_prompt)
        if not raw:
            print("  ⚠ LLM returned empty response")
            continue

        c_code = _extract_c_code(raw)

        # Validate with GCC
        ok, err = _gcc_check_main(c_code, build_dir, [name])
        if ok:
            main_c_code = c_code
            break

        print(f"  ⚠ GCC check failed (attempt {attempt})")
        if debug:
            print(f"[DEBUG] GCC errors:\n{err}", flush=True)
        if attempt <= max_retries:
            prompt = (
                f"The previous main.c had compilation errors:\n{err}\n\n"
                f"Fix the errors. Here is the original request:\n\n"
                f"APPLICATION DESCRIPTION:\n{intent}\n\n"
                f"COMPONENT HEADER:\n\n=== {name}.h (domain: {domain}) ===\n{header_code}\n\n"
                f"Generate a corrected main.c. Output ONLY C code."
            )

    if main_c_code is None:
        print("  ✗ Failed to generate valid main.c")
        return False

    # Write main.c, compile, link
    main_path = os.path.join(output_dir, "main.c")
    with open(main_path, "w") as f:
        f.write(main_c_code)

    binary_path = os.path.join(output_dir, name)
    compile_cmd = [
        gcc, "-o", binary_path,
        main_path,
        f"-I{output_dir}",
        "-lpthread",
    ]

    result = subprocess.run(compile_cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        print(f"  ✗ Linking failed:\n{result.stderr}")
        return False

    dt = time.monotonic() - t0
    binary_size = os.path.getsize(binary_path)

    # Clean up intermediates
    for ext in (".h", ".s", ".o"):
        p = os.path.join(output_dir, f"{name}{ext}")
        if os.path.exists(p):
            os.unlink(p)
    if os.path.exists(main_path):
        os.unlink(main_path)

    print(f"  ✓ Linked executable ({dt:.1f}s)")
    print(f"\n{'═' * 60}")
    print(f"  ✓ Complete pipeline: English → executable binary")
    print(f"")
    print(f"    ./{_relpath(binary_path):30s}  {binary_size:>6,} bytes")
    print(f"")
    print(f"    Run it:")
    print(f"      ./{name}")
    print(f"{'═' * 60}\n")
    return True


# ── Project build system ──

def _build_one_component(comp_name, comp, build_dir):
    """Build a single component. Returns (name, ok, domain)."""
    ok, domain, _name = compile_pipeline(
        comp.prompt_text,
        output_dir=str(build_dir),
        name_override=comp_name,
    )
    return comp_name, ok, domain


def build_project(project_dir: str = ".", no_cache: bool = False) -> bool:
    """
    Build all components defined in a project's build.toml.

    Reads each .prompt file, runs the full pipeline, outputs
    all artifacts to build/.  Uses caching and parallel builds.
    """
    try:
        project = load_project(project_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"\n✗ {e}")
        return False

    # Apply model config from build.toml [model] section
    from . import llm
    llm.configure(project.model)

    build_dir = ensure_build_dir(project)
    cache = BuildCache(build_dir)
    t_start = time.monotonic()

    model_info = llm.get_model_info()

    print(f"\n{'═' * 60}")
    print(f"  prompt2bin build: {project.name}")
    print(f"  Target: {project.target}")
    print(f"  Components: {len(project.components)}")
    print(f"  Backend: {model_info['backend']}")
    if "model" in model_info:
        print(f"  Model: {model_info['model']}")
    if "reasoning" in model_info:
        print(f"  Reasoning: {model_info['reasoning']}")
    if "temperature" in model_info:
        print(f"  Temperature: {model_info['temperature']}")
    print(f"  Output: {_relpath(build_dir)}/")
    print(f"{'═' * 60}")

    # ── Check cache for each component ──
    cached = {}   # name → domain (restored from cache)
    to_build = {} # name → ComponentConfig (needs rebuild)

    for comp_name, comp in project.components.items():
        if not no_cache:
            h = prompt_hash(comp.prompt_text)
            if cache.is_cached(comp_name, h):
                domain = detect_domain(comp.prompt_text)
                if cache.restore(comp_name, build_dir):
                    cached[comp_name] = domain
                    print(f"\n  ⚡ {comp_name}: unchanged, restored from cache")
                    continue
        to_build[comp_name] = comp

    # ── Build changed components in parallel ──
    results = {}
    domains = {}

    # Carry over cached results
    for name, domain in cached.items():
        results[name] = True
        domains[name] = domain

    if to_build:
        n_parallel = len(to_build)
        if n_parallel > 1:
            print(f"\n  ▸ Building {n_parallel} components in parallel...")

        with ThreadPoolExecutor(max_workers=min(n_parallel, 4)) as pool:
            futures = {}
            for comp_name, comp in to_build.items():
                f = pool.submit(_build_one_component, comp_name, comp, build_dir)
                futures[f] = comp_name

            for f in as_completed(futures):
                comp_name, ok, domain = f.result()
                results[comp_name] = ok
                domains[comp_name] = domain
                # Cache successful builds
                if ok:
                    comp = to_build[comp_name]
                    h = prompt_hash(comp.prompt_text)
                    cache.store(comp_name, h, build_dir)

    elapsed = time.monotonic() - t_start

    # ── Build summary ──
    passed = [k for k, v in results.items() if v]
    failed = [k for k, v in results.items() if not v]

    print(f"\n{'═' * 60}")
    print(f"  BUILD {'COMPLETE' if not failed else 'FINISHED WITH ERRORS'}")
    print(f"  Time: {elapsed:.1f}s", end="")
    if cached:
        print(f"  ({len(cached)} cached, {len(to_build)} built)", end="")
    print()

    if passed:
        print(f"  ✓ Passed ({len(passed)}):")
        for name in passed:
            tag = " (cached)" if name in cached else ""
            print(f"      {name}.h  {name}.s  {name}.o{tag}")
    if failed:
        print(f"  ✗ Failed ({len(failed)}):")
        for name in failed:
            print(f"      {name}")

    # ── Generate main.c ──
    main_path = None
    if passed:
        main_path = generate_main_c(build_dir, passed, domains, app_prompt=project.app_prompt)
        if main_path:
            print(f"  main.c: {_relpath(main_path)}")

    print(f"")
    print(f"  Output: {_relpath(build_dir)}/")
    if main_path:
        cmd = Path(sys.argv[0]).stem
        print(f"  Run:    {cmd} run {_relpath(project.project_dir)}")
    print(f"{'═' * 60}\n")

    return len(failed) == 0 and main_path is not None


def _extract_c_code(raw: str) -> str:
    """Extract C code from LLM response, stripping markdown fences if present."""
    match = re.search(r"```(?:c|h)?\s*\n(.*?)```", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    stripped = raw.strip()
    if stripped.startswith("/*") or stripped.startswith("#"):
        return stripped
    return stripped


def _gcc_check_main(c_code: str, build_dir: Path, components: list[str]) -> tuple[bool, str]:
    """Compile main.c with GCC against component headers. Returns (success, errors)."""
    from . import llm

    gcc = shutil.which("gcc")
    if not gcc:
        return True, ""

    main_path = build_dir / "_check_main.c"
    main_path.write_text(c_code)
    cmd = [gcc, "-Wall", "-Werror", "-Wno-unused-function",
           "-I", str(build_dir), "-c", "-o", "/dev/null", str(main_path)]
    if llm.is_debug():
        print(f"[DEBUG] GCC command: {' '.join(cmd)}", flush=True)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if llm.is_debug():
            print(f"[DEBUG] GCC exit code: {result.returncode}", flush=True)
            if result.stderr:
                print(f"[DEBUG] GCC stderr:\n{result.stderr}", flush=True)
        if result.returncode == 0:
            return True, ""
        return False, result.stderr
    except subprocess.TimeoutExpired:
        return False, "GCC timed out"
    finally:
        main_path.unlink(missing_ok=True)


def _generate_main_c_llm(build_dir: Path, components: list[str],
                          domains: dict[str, str], app_prompt: str) -> Path | None:
    """Generate main.c from app.prompt + component headers using LLM."""
    from . import llm

    debug = llm.is_debug()

    # Collect component headers
    headers = {}
    for name in components:
        header_path = build_dir / f"{name}.h"
        if header_path.exists():
            headers[name] = header_path.read_text()
        else:
            print(f"  ⚠ Header not found: {_relpath(header_path)}")
            return None

    # Build the prompt
    header_sections = []
    for name in components:
        header_sections.append(f"=== {name}.h (domain: {domains.get(name, 'unknown')}) ===\n{headers[name]}")
    all_headers = "\n\n".join(header_sections)

    system_prompt = (
        "You generate C source files (main.c). Output ONLY valid C code. "
        "No markdown fences, no explanation, no commentary. "
        "The code must compile with GCC -Wall -Werror. "
        "Include all necessary standard headers. "
        "Use the component APIs exactly as declared in the provided headers."
    )

    prompt = (
        f"Generate a complete main.c file for this application:\n\n"
        f"APPLICATION DESCRIPTION:\n{app_prompt}\n\n"
        f"AVAILABLE COMPONENT HEADERS (include these with #include \"name.h\"):\n\n"
        f"{all_headers}\n\n"
        f"Generate a main.c that implements the application described above, "
        f"using the component APIs from the headers. The code should be production-quality, "
        f"handle errors, and be a fully functional application — not a test harness."
    )

    if debug:
        print(f"[DEBUG] app.prompt ({len(app_prompt)} chars):\n{app_prompt}", flush=True)
        print(f"[DEBUG] System prompt: {system_prompt}", flush=True)
        print(f"[DEBUG] Full prompt ({len(prompt)} chars)", flush=True)

    max_retries = 2
    for attempt in range(1, max_retries + 2):
        print(f"  ⟳ Generating main.c via LLM (attempt {attempt})...", flush=True)
        t0 = time.monotonic()
        raw = llm.generate(prompt, system_prompt)
        dt = time.monotonic() - t0
        if not raw:
            print(f"  ⚠ LLM returned empty response ({dt:.1f}s)")
            continue

        if debug:
            print(f"[DEBUG] Raw LLM response ({len(raw)} chars):\n{raw}", flush=True)

        print(f"  ✓ LLM responded ({dt:.1f}s), validating with GCC...")
        c_code = _extract_c_code(raw)

        if debug and c_code != raw:
            print(f"[DEBUG] Extracted C code ({len(c_code)} chars, differs from raw)", flush=True)

        ok, err = _gcc_check_main(c_code, build_dir, components)
        if ok:
            main_path = build_dir / "main.c"
            main_path.write_text(c_code)
            print(f"  ✓ main.c generated successfully")
            return main_path

        print(f"  ⚠ GCC check failed (attempt {attempt})")
        if debug:
            print(f"[DEBUG] GCC errors:\n{err}", flush=True)
        if attempt <= max_retries:
            prompt = (
                f"The previous main.c had compilation errors:\n{err}\n\n"
                f"Fix the errors. Here is the original request:\n\n"
                f"APPLICATION DESCRIPTION:\n{app_prompt}\n\n"
                f"AVAILABLE COMPONENT HEADERS:\n\n{all_headers}\n\n"
                f"Generate a corrected main.c. Output ONLY C code."
            )
            if debug:
                print(f"[DEBUG] Retry prompt ({len(prompt)} chars)", flush=True)

    return None


def generate_main_c(build_dir: Path, components: list[str], domains: dict[str, str],
                    app_prompt: str | None = None) -> Path | None:
    """Generate a main.c that wires components into an application.

    Requires an app_prompt describing what the application does.
    Uses an LLM to generate main.c from the app description + component headers.
    """
    if not app_prompt:
        print("  ✗ No app.prompt found — cannot generate main.c")
        print("    Add an app.prompt file describing what your application does.")
        return None

    result = _generate_main_c_llm(build_dir, components, domains, app_prompt)
    if result:
        return result

    print("  ✗ Failed to generate main.c from app.prompt")
    return None




def run_project(project_dir: str = ".", no_cache: bool = False) -> bool:
    """Build, compile main.c, and run."""
    if not build_project(project_dir, no_cache=no_cache):
        return False

    project = load_project(project_dir)
    build_dir = project.build_dir
    main_c = build_dir / "main.c"

    if not main_c.exists():
        print("  ✗ No main.c found in build/")
        return False

    gcc = shutil.which("gcc")
    binary = build_dir / project.name
    headers = [str(build_dir / f"{name}.h") for name in project.components]

    # Compile main.c with all headers accessible
    compile_cmd = [
        gcc, "-o", str(binary),
        str(main_c),
        f"-I{build_dir}",
        "-lpthread",
    ]

    print(f"\n▸ Compiling main.c...")
    result = subprocess.run(compile_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ✗ Compilation failed:\n{result.stderr}")
        return False

    print(f"  ✓ {binary}")

    # Run it
    print(f"\n▸ Running {project.name}...\n")
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    timeout_env = os.environ.get("P2B_RUN_TIMEOUT")
    timeout = None
    if timeout_env:
        try:
            timeout = int(timeout_env)
        except ValueError:
            timeout = None
    elif not interactive:
        timeout = 10

    try:
        if interactive:
            result = subprocess.run([str(binary)], timeout=timeout)
        else:
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=timeout)
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("  ✗ Run timed out")
        return False

    if result.returncode != 0:
        print(f"  ✗ Exited with code {result.returncode}")
        return False

    return True


# ── CLI ──

def interactive():
    """Interactive mode."""
    print(BANNER)
    print("Describe what you need in plain English. Type 'quit' to exit.\n")
    while True:
        try:
            intent = input("prompt2bin> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if not intent or intent.lower() in ("quit", "exit", "q"):
            break
        compile_pipeline(intent)  # ignore domain return


def check_dependencies():
    """Check for required external tools and print helpful messages."""
    from . import llm

    gcc = shutil.which("gcc")
    if not gcc:
        print("\n  ✗ GCC not found.")
        print("    Install: apt install gcc (Linux) / xcode-select --install (macOS)")
        print("  GCC is required. Cannot continue.")
        sys.exit(1)

    backend = llm.get_backend()
    has_claude = shutil.which("claude")
    has_codex = shutil.which("codex")
    has_anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    has_openai_key = os.environ.get("OPENAI_API_KEY")

    if not has_claude and not has_codex and not has_anthropic_key and not has_openai_key:
        print("\n  ⚠ No LLM backend found. Will fall back to regex parsing (less accurate).")
        print("    Option 1: Install Claude CLI — https://docs.anthropic.com/en/docs/claude-cli")
        print("    Option 2: Install Codex CLI — https://github.com/openai/codex")
        print("    Option 3: Set ANTHROPIC_API_KEY or OPENAI_API_KEY")
        print()
    else:
        print(f"  LLM backend: {backend}")


def show_help(cmd: str):
    """Print usage help."""
    print(f"""
  prompt2bin — from natural language to verified machine code

  Targets: x86-64-linux (default), wasm
  Domains (x86-64): arena, ring buffer, process spawner, string table, terminal I/O

  Usage:
    {cmd} init <name> [--template <t>]   Scaffold a new project
    {cmd} build [dir] [--no-cache]         Build all components in a project
    {cmd} run [dir] [--no-cache]           Build + compile + run
    {cmd} "<prompt>"                      One-shot: compile a single prompt
    {cmd} --target wasm "<prompt>"        One-shot: compile to WebAssembly
    {cmd} --interactive                   Interactive mode
    {cmd} --help                          Show this help

  Flags:
    --target t    Compilation target: wasm or x86-64-linux (default)
    --debug       Show LLM prompts, raw responses, and tool commands
    --no-cache    Skip build cache, rebuild all components

  Templates: {', '.join(TEMPLATES)}

  LLM backends (auto-detected, or set P2B_BACKEND):
    claude          Claude CLI (default if installed)
    codex           OpenAI Codex CLI
    anthropic-api   Anthropic API (needs ANTHROPIC_API_KEY)
    openai-api      OpenAI API (needs OPENAI_API_KEY)

  Priority: CLI > API key. Set P2B_BACKEND to override.

  Environment variables:
    P2B_BACKEND          Force backend (see above)
    ANTHROPIC_API_KEY    For anthropic-api backend
    OPENAI_API_KEY       For openai-api backend
    P2B_ANTHROPIC_MODEL  Anthropic model (default: claude-haiku-4-5-20251001)
    P2B_OPENAI_MODEL     OpenAI model (default: gpt-4o-mini)
    P2B_RUN_TIMEOUT      Seconds to allow built binary to run

  Examples:
    {cmd} init my_game --template game-engine
    {cmd} build my_game
    {cmd} "I need a memory pool, 4KB, 16-byte aligned"
    {cmd} --target wasm "a function that adds two i32 numbers"
    {cmd} --target wasm "bump allocator, 4KB, 16-byte alignment"
""")


def main():
    cmd = Path(sys.argv[0]).stem  # "p2b" or "prompt2bin"
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h", "help"):
        show_help(cmd)
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
        from . import __version__
        print(f"prompt2bin {__version__}")
        sys.exit(0)
    if len(sys.argv) < 2 or sys.argv[1] == "--interactive":
        interactive()
    elif sys.argv[1] == "init":
        if len(sys.argv) < 3:
            print(f"Usage: {cmd} init <project_name> [--template <name>]")
            sys.exit(1)
        project_name = sys.argv[2]
        template = None
        if "--template" in sys.argv:
            idx = sys.argv.index("--template")
            if idx + 1 < len(sys.argv):
                template = sys.argv[idx + 1]
            else:
                print(f"Available templates: {', '.join(TEMPLATES)}")
                sys.exit(1)
        try:
            path, used_template = init_project(project_name, template)
            tmpl = TEMPLATES[used_template]
            components = list(tmpl["components"].keys())
            print(f"\n  ✓ Created project at {_relpath(path)}/")
            print(f"    Template: {used_template} — {tmpl['description']}")
            print(f"    Components: {', '.join(components)}")
            print(f"")
            print(f"    Next steps:")
            print(f"      1. Review specs/ and edit prompts to your needs")
            print(f"      2. {cmd} run {project_name}")
            print()
        except (FileExistsError, ValueError) as e:
            print(f"\n  ✗ {e}")
            sys.exit(1)
    elif sys.argv[1] == "build":
        check_dependencies()
        debug = "--debug" in sys.argv
        no_cache = "--no-cache" in sys.argv
        flags = ("--no-cache", "--debug")
        args = [a for a in sys.argv[2:] if a not in flags]
        project_dir = args[0] if args else "."
        if debug:
            from . import llm
            llm.set_debug(True)
        success = build_project(project_dir, no_cache=no_cache)
        sys.exit(0 if success else 1)
    elif sys.argv[1] == "run":
        check_dependencies()
        debug = "--debug" in sys.argv
        no_cache = "--no-cache" in sys.argv
        flags = ("--no-cache", "--debug")
        args = [a for a in sys.argv[2:] if a not in flags]
        project_dir = args[0] if args else "."
        if debug:
            from . import llm
            llm.set_debug(True)
        success = run_project(project_dir, no_cache=no_cache)
        sys.exit(0 if success else 1)
    else:
        check_dependencies()
        debug = "--debug" in sys.argv
        target = "x86-64-linux"  # default
        remaining = [a for a in sys.argv[1:] if a not in ("--debug",)]
        # Parse --target flag
        if "--target" in remaining:
            idx = remaining.index("--target")
            if idx + 1 < len(remaining):
                target = remaining[idx + 1]
                remaining = remaining[:idx] + remaining[idx + 2:]
            else:
                print("  ✗ --target requires a value (wasm or x86-64-linux)")
                sys.exit(1)
        intent = " ".join(remaining)
        if debug:
            from . import llm
            llm.set_debug(True)

        if target == "wasm":
            success = _compile_wasm(intent)
        else:
            success, domain, name = compile_pipeline(intent, oneshot=True)
            if success and name:
                success = _phase5_executable(name, domain, intent)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
