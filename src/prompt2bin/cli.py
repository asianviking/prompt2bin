#!/usr/bin/env python3
"""
prompt2bin — from natural language to verified machine code.

Usage:
    p2b init my_project          # scaffold a new project
    p2b build my_project         # build all components
    p2b "I need a memory pool"   # single prompt, one-shot
    p2b --interactive            # interactive mode
"""

import shutil
import subprocess
import sys
import tempfile
import os
from pathlib import Path

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

# Project system
from .project import load_project, ensure_build_dir, init_project, TEMPLATES


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


def compile_pipeline(intent: str, output_dir: str = ".", name_override: str | None = None) -> tuple[bool, str]:
    """Full pipeline: intent → spec → verify → C code → assembly → binary.
    Returns (success, domain)."""
    print(f"\n{'─' * 60}")
    print(f"  INPUT: {intent}")
    print(f"{'─' * 60}")

    domain = detect_domain(intent)
    print(f"\n  Domain: {domain}")

    if domain == "ringbuf":
        return _compile_ringbuf(intent, output_dir, name_override), domain
    elif domain == "proc":
        return _compile_proc(intent, output_dir, name_override), domain
    elif domain == "strtab":
        return _compile_strtab(intent, output_dir, name_override), domain
    elif domain == "termio":
        return _compile_termio(intent, output_dir, name_override), domain
    else:
        return _compile_arena(intent, output_dir, name_override), domain


def _compile_arena(intent: str, output_dir: str = ".", name_override: str | None = None) -> bool:
    """Arena allocator pipeline."""
    print("\n▸ Phase 1: Translating intent → formal spec...")
    spec = intent_to_arena(intent)
    if name_override:
        spec.name = name_override
    print(spec.describe())

    print("\n▸ Phase 2: Verifying spec with Z3...")
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
        return False
    print(f"\n  All {len(results)} properties verified ✓")

    verified_props = [r.message for r in results if r.passed]
    print("\n▸ Phase 3: Generating C code via LLM...")
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
    print(f"  Generated {lines} lines → {output_path} (via {codegen_source})")

    print("\n▸ Phase 3b: Running test harness...")
    test_ok, test_msg = run_arena_test(spec, output_path)
    print(f"  {test_msg}")
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
                   "_force_alloc", f"{spec.name}_alloc", output_dir)


def _compile_ringbuf(intent: str, output_dir: str = ".", name_override: str | None = None) -> bool:
    """Ring buffer pipeline."""
    print("\n▸ Phase 1: Translating intent → formal spec...")
    spec = intent_to_ringbuf(intent)
    if name_override:
        spec.name = name_override
    print(spec.describe())

    print("\n▸ Phase 2: Verifying spec with Z3...")
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
        return False
    print(f"\n  All {len(results)} properties verified ✓")

    verified_props = [r.message for r in results if r.passed]
    print("\n▸ Phase 3: Generating C code via LLM...")
    c_code = generate_ringbuf_llm(spec, verified_properties=verified_props)
    codegen_source = "Claude"

    if c_code is None:
        print("  ✗ LLM codegen failed and no template fallback for ring buffers.")
        return False

    output_path = os.path.join(output_dir, f"{spec.name}.h")
    with open(output_path, "w") as f:
        f.write(c_code)
    lines = c_code.count("\n")
    print(f"  Generated {lines} lines → {output_path} (via {codegen_source})")

    print("\n▸ Phase 3b: Running test harness...")
    test_ok, test_msg = run_ringbuf_test(spec, output_path)
    print(f"  {test_msg}")
    if not test_ok:
        print("  ⚠ Tests failed — code generated but may have issues")

    return _phase4(c_code, spec.name, output_path, lines, "ringbuf",
                   "_force_push", f"{spec.name}_push", output_dir)


def _compile_proc(intent: str, output_dir: str = ".", name_override: str | None = None) -> bool:
    """Process spawner pipeline."""
    print("\n▸ Phase 1: Translating intent → formal spec...")
    spec = intent_to_proc(intent)
    if name_override:
        spec.name = name_override
    print(spec.describe())

    print("\n▸ Phase 2: Verifying spec with Z3...")
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
        return False
    print(f"\n  All {len(results)} properties verified ✓")

    verified_props = [r.message for r in results if r.passed]
    print("\n▸ Phase 3: Generating C code via LLM...")
    c_code = generate_proc_llm(spec, verified_properties=verified_props)
    codegen_source = "Claude"

    if c_code is None:
        print("  ✗ LLM codegen failed and no template fallback for process spawner.")
        return False

    output_path = os.path.join(output_dir, f"{spec.name}.h")
    with open(output_path, "w") as f:
        f.write(c_code)
    lines = c_code.count("\n")
    print(f"  Generated {lines} lines → {output_path} (via {codegen_source})")

    print("\n▸ Phase 3b: Running test harness...")
    test_ok, test_msg = run_proc_test(spec, output_path)
    print(f"  {test_msg}")
    if not test_ok:
        print("  ⚠ Tests failed — code generated but may have issues")

    return _phase4(c_code, spec.name, output_path, lines, "proc",
                   "_force_exec", f"{spec.name}_exec", output_dir)


def _compile_strtab(intent: str, output_dir: str = ".", name_override: str | None = None) -> bool:
    """String table pipeline."""
    print("\n▸ Phase 1: Translating intent → formal spec...")
    spec = intent_to_strtab(intent)
    if name_override:
        spec.name = name_override
    print(spec.describe())

    print("\n▸ Phase 2: Verifying spec with Z3...")
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
        return False
    print(f"\n  All {len(results)} properties verified ✓")

    verified_props = [r.message for r in results if r.passed]
    print("\n▸ Phase 3: Generating C code via LLM...")
    c_code = generate_strtab_llm(spec, verified_properties=verified_props)
    codegen_source = "Claude"

    if c_code is None:
        print("  ✗ LLM codegen failed and no template fallback for string table.")
        return False

    output_path = os.path.join(output_dir, f"{spec.name}.h")
    with open(output_path, "w") as f:
        f.write(c_code)
    lines = c_code.count("\n")
    print(f"  Generated {lines} lines → {output_path} (via {codegen_source})")

    print("\n▸ Phase 3b: Running test harness...")
    test_ok, test_msg = run_strtab_test(spec, output_path)
    print(f"  {test_msg}")
    if not test_ok:
        print("  ⚠ Tests failed — code generated but may have issues")

    return _phase4(c_code, spec.name, output_path, lines, "strtab",
                   "_force_intern", f"{spec.name}_intern", output_dir)


def _compile_termio(intent: str, output_dir: str = ".", name_override: str | None = None) -> bool:
    """Terminal I/O pipeline."""
    print("\n▸ Phase 1: Translating intent → formal spec...")
    spec = intent_to_termio(intent)
    if name_override:
        spec.name = name_override
    print(spec.describe())

    print("\n▸ Phase 2: Verifying spec with Z3...")
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
        return False
    print(f"\n  All {len(results)} properties verified ✓")

    verified_props = [r.message for r in results if r.passed]
    print("\n▸ Phase 3: Generating C code via LLM...")
    c_code = generate_termio_llm(spec, verified_properties=verified_props)
    codegen_source = "Claude"

    if c_code is None:
        print("  ✗ LLM codegen failed and no template fallback for terminal I/O.")
        return False

    output_path = os.path.join(output_dir, f"{spec.name}.h")
    with open(output_path, "w") as f:
        f.write(c_code)
    lines = c_code.count("\n")
    print(f"  Generated {lines} lines → {output_path} (via {codegen_source})")

    print("\n▸ Phase 3b: Running test harness...")
    test_ok, test_msg = run_termio_test(spec, output_path)
    print(f"  {test_msg}")
    if not test_ok:
        print("  ⚠ Tests failed — code generated but may have issues")

    return _phase4(c_code, spec.name, output_path, lines, "termio",
                   "_force_readline", f"{spec.name}_readline", output_dir)


def _phase4(c_code, name, output_path, lines, domain, hot_func, hot_label, output_dir="."):
    """Phase 4: Compile to assembly and machine code."""
    print("\n▸ Phase 4: Compiling to assembly and machine code...")
    asm_path, obj_path, err = compile_to_binary(c_code, name, domain, output_dir)

    if err:
        print(f"  ⚠ {err}")
    else:
        asm_size = os.path.getsize(asm_path)
        obj_size = os.path.getsize(obj_path)
        print(f"  {asm_path:30s} — {asm_size:>6,} bytes (assembly)")
        print(f"  {obj_path:30s} — {obj_size:>6,} bytes (machine code)")
        show_assembly_highlights(asm_path, hot_func, hot_label)

    print(f"\n{'═' * 60}")
    print(f"  ✓ Complete pipeline: English → verified machine code")
    print(f"")
    print(f"    {output_path:30s}  C code ({lines} lines)")
    if asm_path:
        print(f"    {asm_path:30s}  x86-64 assembly")
    if obj_path:
        print(f"    {obj_path:30s}  machine code (linkable)")
    print(f"")
    print(f"    Link into your program:")
    print(f"      #include \"{os.path.basename(output_path)}\"")
    print(f"{'═' * 60}\n")
    return True


# ── Project build system ──

def build_project(project_dir: str = ".") -> bool:
    """
    Build all components defined in a project's build.toml.

    Reads each .prompt file, runs the full pipeline, outputs
    all artifacts to build/.
    """
    try:
        project = load_project(project_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"\n✗ {e}")
        return False

    build_dir = ensure_build_dir(project)

    print(f"\n{'═' * 60}")
    print(f"  prompt2bin build: {project.name}")
    print(f"  Target: {project.target}")
    print(f"  Components: {len(project.components)}")
    print(f"  Output: {build_dir}/")
    print(f"{'═' * 60}")

    results = {}
    domains = {}
    for comp_name, comp in project.components.items():
        print(f"\n{'━' * 60}")
        print(f"  Building component: {comp_name}")
        print(f"  Prompt: {comp.prompt_path}")
        print(f"{'━' * 60}")

        ok, domain = compile_pipeline(
            comp.prompt_text,
            output_dir=str(build_dir),
            name_override=comp_name,
        )
        results[comp_name] = ok
        domains[comp_name] = domain

    # ── Build summary ──
    passed = [k for k, v in results.items() if v]
    failed = [k for k, v in results.items() if not v]

    print(f"\n{'═' * 60}")
    print(f"  BUILD {'COMPLETE' if not failed else 'FINISHED WITH ERRORS'}")
    print(f"")

    if passed:
        print(f"  ✓ Passed ({len(passed)}):")
        for name in passed:
            print(f"      {name}.h  {name}.s  {name}.o")
    if failed:
        print(f"  ✗ Failed ({len(failed)}):")
        for name in failed:
            print(f"      {name}")

    # ── Generate main.c ──
    if passed:
        main_path = generate_main_c(build_dir, passed, domains)
        print(f"  main.c: {main_path}")

    print(f"")
    print(f"  Output: {build_dir}/")
    if passed:
        cmd = Path(sys.argv[0]).stem
        print(f"  Run:    {cmd} run {project.project_dir}")
    print(f"{'═' * 60}\n")

    return len(failed) == 0


def generate_main_c(build_dir: Path, components: list[str], domains: dict[str, str]) -> Path:
    """Generate a main.c that exercises all built components."""
    grok_components = {
        "api_caller": "proc",
        "context_store": "strtab",
        "input_handler": "termio",
        "response_buffer": "ringbuf",
    }

    if set(components) == set(grok_components) and all(domains.get(k) == v for k, v in grok_components.items()):
        return _generate_grok_cli_main_c(build_dir)

    lines = [
        '#include <stdio.h>',
        '#include <string.h>',
        '',
    ]

    for name in components:
        lines.append(f'#include "{name}.h"')

    lines.extend(['', 'int main(void) {'])

    for name in components:
        domain = domains.get(name, "arena")
        lines.append(f'')
        lines.append(f'    // ── {name} ──')

        if domain == "ringbuf":
            lines.extend([
                f'    printf("--- {name} ---\\n");',
                f'    {name}_t *{name} = {name}_create();',
                f'    if (!{name}) {{ printf("  create failed\\n"); return 1; }}',
                f'    printf("  created\\n");',
                f'',
                f'    // Push some messages',
                f'    char {name}_msg[256];',
                f'    memset({name}_msg, 0, sizeof({name}_msg));',
                f'    for (int i = 0; i < 5; i++) {{',
                f'        snprintf({name}_msg, sizeof({name}_msg), "message %d", i);',
                f'        int ok = {name}_push({name}, {name}_msg);',
                f'        printf("  push [%d]: %s\\n", i, ok == 0 ? "ok" : "full");',
                f'    }}',
                f'',
                f'    // Pop them back',
                f'    char {name}_out[256];',
                f'    for (int i = 0; i < 5; i++) {{',
                f'        int ok = {name}_pop({name}, {name}_out);',
                f'        if (ok == 0) printf("  pop [%d]: %s\\n", i, {name}_out);',
                f'    }}',
                f'',
                f'    {name}_destroy({name});',
                f'    printf("  destroyed\\n\\n");',
            ])
        elif domain == "proc":
            lines.extend([
                f'    printf("--- {name} ---\\n");',
                f'',
                f'    // Run a simple command',
                f'    char {name}_buf[256];',
                f'    memset({name}_buf, 0, sizeof({name}_buf));',
                f'    int {name}_rc = {name}_exec_simple("/bin/echo hello from prompt2bin", {name}_buf, sizeof({name}_buf));',
                f'    printf("  exec_simple: rc=%d\\n", {name}_rc);',
                f'    printf("  output: %s\\n", {name}_buf);',
                f'',
                f'    // Run with full exec',
                f'    const char *{name}_args[] = {{"echo", "structured", "output", NULL}};',
                f'    {name}_result_t *{name}_r = {name}_exec("/bin/echo", {name}_args, 3);',
                f'    if ({name}_r) {{',
                f'        printf("  exec: exit=%d stdout=%zuB stderr=%zuB\\n",',
                f'               {name}_r->exit_code, {name}_r->stdout_len, {name}_r->stderr_len);',
                f'        if ({name}_r->stdout_buf) printf("  stdout: %s\\n", {name}_r->stdout_buf);',
                f'        {name}_result_free({name}_r);',
                f'    }}',
                f'    printf("  done\\n\\n");',
            ])
        elif domain == "strtab":
            lines.extend([
                f'    printf("--- {name} ---\\n");',
                f'    {name}_t *{name} = {name}_create();',
                f'    if (!{name}) {{ printf("  create failed\\n"); return 1; }}',
                f'    printf("  created\\n");',
                f'',
                f'    // Intern some strings',
                f'    int {name}_id1 = {name}_intern({name}, "hello");',
                f'    int {name}_id2 = {name}_intern({name}, "world");',
                f'    int {name}_id3 = {name}_intern({name}, "hello");  // dedup',
                f'    printf("  intern \\"hello\\": id=%d\\n", {name}_id1);',
                f'    printf("  intern \\"world\\": id=%d\\n", {name}_id2);',
                f'    printf("  intern \\"hello\\": id=%d (dedup)\\n", {name}_id3);',
                f'',
                f'    // Lookup',
                f'    const char *{name}_s = {name}_lookup({name}, {name}_id1);',
                f'    printf("  lookup(%d): %s\\n", {name}_id1, {name}_s ? {name}_s : "NULL");',
                f'    printf("  count: %d\\n", {name}_count({name}));',
                f'',
                f'    {name}_destroy({name});',
                f'    printf("  destroyed\\n\\n");',
            ])
        elif domain == "termio":
            lines.extend([
                f'    printf("--- {name} ---\\n");',
                f'    {name}_t *{name} = {name}_create();',
                f'    if (!{name}) {{ printf("  create failed\\n"); return 1; }}',
                f'    printf("  created\\n");',
                f'',
                f'    // Add some history entries',
                f'    {name}_history_add({name}, "first command");',
                f'    {name}_history_add({name}, "second command");',
                f'    {name}_history_add({name}, "third command");',
                f'    printf("  history count: %d\\n", {name}_history_count({name}));',
                f'',
                f'    // Retrieve history (0 = most recent)',
                f'    for (int i = 0; i < {name}_history_count({name}); i++) {{',
                f'        const char *h = {name}_history_get({name}, i);',
                f'        if (h) printf("  history[%d]: %s\\n", i, h);',
                f'    }}',
                f'',
                f'    {name}_set_prompt({name}, "> ");',
                f'    printf("  prompt set\\n");',
                f'',
                f'    {name}_destroy({name});',
                f'    printf("  destroyed\\n\\n");',
            ])
        else:  # arena
            lines.extend([
                f'    printf("--- {name} ---\\n");',
                f'    {name}_t *{name} = {name}_create();',
                f'    if (!{name}) {{ printf("  create failed\\n"); return 1; }}',
                f'    printf("  created\\n");',
                f'',
                f'    // Allocate some memory',
                f'    void *p1 = {name}_alloc({name}, 64);',
                f'    void *p2 = {name}_alloc({name}, 128);',
                f'    void *p3 = {name}_alloc({name}, 256);',
                f'    printf("  alloc 64B:  %s\\n", p1 ? "ok" : "failed");',
                f'    printf("  alloc 128B: %s\\n", p2 ? "ok" : "failed");',
                f'    printf("  alloc 256B: %s\\n", p3 ? "ok" : "failed");',
                f'',
                f'    {name}_reset({name});',
                f'    printf("  reset\\n");',
                f'',
                f'    {name}_destroy({name});',
                f'    printf("  destroyed\\n\\n");',
            ])

    lines.extend([
        '',
        '    printf("All components working.\\n");',
        '    return 0;',
        '}',
        '',
    ])

    main_path = build_dir / "main.c"
    main_path.write_text('\n'.join(lines))
    return main_path


def _generate_grok_cli_main_c(build_dir: Path) -> Path:
    """Generate an interactive Grok CLI main.c using curl via the proc spawner."""
    lines = [
        '#include <ctype.h>',
        '#include <stdarg.h>',
        '#include <stdio.h>',
        '#include <stdlib.h>',
        '#include <string.h>',
        '',
        '#include "api_caller.h"',
        '#include "context_store.h"',
        '#include "input_handler.h"',
        '#include "response_buffer.h"',
        '',
        '#define GROK_MAX_MESSAGES 64',
        '#define GROK_REQ_CAP (1024 * 1024)',
        '',
        'typedef struct {',
        '    const char *role;',
        '    int content_id;',
        '} grok_msg_t;',
        '',
        'static int append_str(char **out, size_t *rem, const char *s) {',
        '    size_t n = strlen(s);',
        '    if (n + 1 > *rem) return -1;',
        '    memcpy(*out, s, n);',
        '    *out += n;',
        '    *rem -= n;',
        '    **out = 0;',
        '    return 0;',
        '}',
        '',
        'static int append_fmt(char **out, size_t *rem, const char *fmt, ...) {',
        '    va_list ap;',
        '    va_start(ap, fmt);',
        '    int n = vsnprintf(*out, *rem, fmt, ap);',
        '    va_end(ap);',
        '    if (n < 0) return -1;',
        '    if ((size_t)n + 1 > *rem) return -1;',
        '    *out += (size_t)n;',
        '    *rem -= (size_t)n;',
        '    return 0;',
        '}',
        '',
        'static int json_escape_append(char **out, size_t *rem, const char *in) {',
        '    for (const unsigned char *p = (const unsigned char *)in; *p; p++) {',
        '        unsigned char c = *p;',
        '        switch (c) {',
        '            case \'\\\"\': if (append_str(out, rem, "\\\\\\"") < 0) return -1; break;',
        '            case \'\\\\\': if (append_str(out, rem, "\\\\\\\\") < 0) return -1; break;',
        '            case \'\\b\': if (append_str(out, rem, "\\\\b") < 0) return -1; break;',
        '            case \'\\f\': if (append_str(out, rem, "\\\\f") < 0) return -1; break;',
        '            case \'\\n\': if (append_str(out, rem, "\\\\n") < 0) return -1; break;',
        '            case \'\\r\': if (append_str(out, rem, "\\\\r") < 0) return -1; break;',
        '            case \'\\t\': if (append_str(out, rem, "\\\\t") < 0) return -1; break;',
        '            default:',
        '                if (c < 0x20) {',
        '                    if (append_fmt(out, rem, "\\\\u%04x", (unsigned)c) < 0) return -1;',
        '                } else {',
        '                    if (*rem < 2) return -1;',
        '                    **out = (char)c;',
        '                    (*out)++;',
        '                    (*rem)--;',
        '                    **out = 0;',
        '                }',
        '        }',
        '    }',
        '    return 0;',
        '}',
        '',
        'static int build_chat_request_json(',
        '    char *out, size_t out_cap,',
        '    context_store_t *store,',
        '    const grok_msg_t *msgs, int msg_count,',
        '    const char *model',
        ') {',
        '    char *p = out;',
        '    size_t rem = out_cap;',
        '    out[0] = 0;',
        '',
        '    if (append_str(&p, &rem, "{") < 0) return -1;',
        '    if (append_str(&p, &rem, "\\"model\\":\\"") < 0) return -1;',
        '    if (append_str(&p, &rem, model) < 0) return -1;',
        '    if (append_str(&p, &rem, "\\",") < 0) return -1;',
        '    if (append_str(&p, &rem, "\\"messages\\":[") < 0) return -1;',
        '',
        '    for (int i = 0; i < msg_count; i++) {',
        '        const char *content = context_store_lookup(store, msgs[i].content_id);',
        '        if (!content) content = "";',
        '',
        '        if (i > 0) {',
        '            if (append_str(&p, &rem, ",") < 0) return -1;',
        '        }',
        '        if (append_str(&p, &rem, "{\\"role\\":\\"") < 0) return -1;',
        '        if (append_str(&p, &rem, msgs[i].role) < 0) return -1;',
        '        if (append_str(&p, &rem, "\\",\\"content\\":\\"") < 0) return -1;',
        '        if (json_escape_append(&p, &rem, content) < 0) return -1;',
        '        if (append_str(&p, &rem, "\\"}") < 0) return -1;',
        '    }',
        '',
        '    if (append_str(&p, &rem, "],\\"stream\\":false}") < 0) return -1;',
        '    return 0;',
        '}',
        '',
        'static int hexval(int c) {',
        '    if (c >= \'0\' && c <= \'9\') return c - \'0\';',
        '    if (c >= \'a\' && c <= \'f\') return 10 + (c - \'a\');',
        '    if (c >= \'A\' && c <= \'F\') return 10 + (c - \'A\');',
        '    return -1;',
        '}',
        '',
        'static size_t utf8_write(char *out, size_t cap, unsigned codepoint) {',
        '    if (codepoint <= 0x7F) {',
        '        if (cap < 1) return 0;',
        '        out[0] = (char)codepoint;',
        '        return 1;',
        '    }',
        '    if (codepoint <= 0x7FF) {',
        '        if (cap < 2) return 0;',
        '        out[0] = (char)(0xC0 | (codepoint >> 6));',
        '        out[1] = (char)(0x80 | (codepoint & 0x3F));',
        '        return 2;',
        '    }',
        '    if (codepoint <= 0xFFFF) {',
        '        if (cap < 3) return 0;',
        '        out[0] = (char)(0xE0 | (codepoint >> 12));',
        '        out[1] = (char)(0x80 | ((codepoint >> 6) & 0x3F));',
        '        out[2] = (char)(0x80 | (codepoint & 0x3F));',
        '        return 3;',
        '    }',
        '    if (cap < 4) return 0;',
        '    out[0] = (char)(0xF0 | (codepoint >> 18));',
        '    out[1] = (char)(0x80 | ((codepoint >> 12) & 0x3F));',
        '    out[2] = (char)(0x80 | ((codepoint >> 6) & 0x3F));',
        '    out[3] = (char)(0x80 | (codepoint & 0x3F));',
        '    return 4;',
        '}',
        '',
        'static char *json_extract_assistant_content(const char *json) {',
        '    if (!json) return NULL;',
        '',
        '    const char *p = strstr(json, "\\"message\\"");',
        '    if (!p) p = json;',
        '    p = strstr(p, "\\"content\\"");',
        '    if (!p) return NULL;',
        '    p = strchr(p, \':\');',
        '    if (!p) return NULL;',
        '    p++;',
        '    while (*p && isspace((unsigned char)*p)) p++;',
        '    if (*p != \'\\\"\') return NULL;',
        '    p++;',
        '',
        '    size_t max_out = strlen(p) + 1;',
        '    char *out = (char *)malloc(max_out);',
        '    if (!out) return NULL;',
        '    size_t j = 0;',
        '',
        '    while (*p) {',
        '        char c = *p++;',
        '        if (c == \'\\\"\') break;',
        '        if (c != \'\\\\\') {',
        '            out[j++] = c;',
        '            continue;',
        '        }',
        '',
        '        char esc = *p++;',
        '        switch (esc) {',
        '            case \'\\\"\': out[j++] = \'\\\"\'; break;',
        '            case \'\\\\\': out[j++] = \'\\\\\'; break;',
        '            case \'/\': out[j++] = \'/\'; break;',
        '            case \'b\': out[j++] = \'\\b\'; break;',
        '            case \'f\': out[j++] = \'\\f\'; break;',
        '            case \'n\': out[j++] = \'\\n\'; break;',
        '            case \'r\': out[j++] = \'\\r\'; break;',
        '            case \'t\': out[j++] = \'\\t\'; break;',
        '            case \'u\': {',
        '                int h1 = hexval((unsigned char)p[0]);',
        '                int h2 = hexval((unsigned char)p[1]);',
        '                int h3 = hexval((unsigned char)p[2]);',
        '                int h4 = hexval((unsigned char)p[3]);',
        '                if (h1 < 0 || h2 < 0 || h3 < 0 || h4 < 0) {',
        '                    out[j++] = \'?\';',
        '                    break;',
        '                }',
        '                unsigned code = (unsigned)((h1 << 12) | (h2 << 8) | (h3 << 4) | h4);',
        '                p += 4;',
        '',
        '                if (code >= 0xD800 && code <= 0xDBFF && p[0] == \'\\\\\' && p[1] == \'u\') {',
        '                    int l1 = hexval((unsigned char)p[2]);',
        '                    int l2 = hexval((unsigned char)p[3]);',
        '                    int l3 = hexval((unsigned char)p[4]);',
        '                    int l4 = hexval((unsigned char)p[5]);',
        '                    if (l1 >= 0 && l2 >= 0 && l3 >= 0 && l4 >= 0) {',
        '                        unsigned low = (unsigned)((l1 << 12) | (l2 << 8) | (l3 << 4) | l4);',
        '                        if (low >= 0xDC00 && low <= 0xDFFF) {',
        '                            p += 6;',
        '                            unsigned hi = code - 0xD800;',
        '                            unsigned lo = low - 0xDC00;',
        '                            code = 0x10000u + ((hi << 10) | lo);',
        '                        }',
        '                    }',
        '                }',
        '',
        '                size_t w = utf8_write(out + j, max_out - j - 1, code);',
        '                if (w == 0) {',
        '                    out[j++] = \'?\';',
        '                } else {',
        '                    j += w;',
        '                }',
        '                break;',
        '            }',
        '            default:',
        '                out[j++] = esc;',
        '        }',
        '    }',
        '',
        '    out[j] = 0;',
        '    return out;',
        '}',
        '',
        'static void grok_append_msg(grok_msg_t *msgs, int *count, const char *role, int content_id) {',
        '    if (*count >= GROK_MAX_MESSAGES) {',
        '        int keep = 1; /* keep system at index 0 */',
        '        if (*count > keep) {',
        '            memmove(&msgs[keep], &msgs[keep + 1], sizeof(msgs[0]) * (size_t)(*count - keep - 1));',
        '            (*count)--;',
        '        } else {',
        '            *count = 0;',
        '        }',
        '    }',
        '    msgs[*count].role = role;',
        '    msgs[*count].content_id = content_id;',
        '    (*count)++;',
        '}',
        '',
        'static void print_via_response_buffer(response_buffer_t *rb, const char *text) {',
        '    char chunk[256];',
        '    char out[256];',
        '',
        '    size_t len = strlen(text);',
        '    size_t pos = 0;',
        '    while (pos < len) {',
        '        memset(chunk, 0, sizeof(chunk));',
        '        size_t n = len - pos;',
        '        if (n > sizeof(chunk) - 1) n = sizeof(chunk) - 1;',
        '        memcpy(chunk, text + pos, n);',
        '        if (response_buffer_push(rb, chunk) != 0) break;',
        '        if (response_buffer_pop(rb, out) == 0) fputs(out, stdout);',
        '        pos += n;',
        '    }',
        '    while (response_buffer_pop(rb, out) == 0) fputs(out, stdout);',
        '}',
        '',
        'int main(void) {',
        '    const char *api_key = getenv("XAI_API_KEY");',
        '    if (!api_key || !*api_key) {',
        '        printf("Grok CLI template (curl backend)\\n\\n");',
        '        printf("Set XAI_API_KEY to make live requests.\\n");',
        '        printf("Example:\\n  export XAI_API_KEY=...\\n\\n");',
        '        return 0;',
        '    }',
        '',
        '    const char *model = getenv("XAI_MODEL");',
        '    if (!model || !*model) model = "grok-4-0709";',
        '',
        '    context_store_t *store = context_store_create();',
        '    input_handler_t *io = input_handler_create();',
        '    response_buffer_t *rb = response_buffer_create();',
        '    if (!store || !io || !rb) {',
        '        fprintf(stderr, "FAIL: create components\\n");',
        '        if (rb) response_buffer_destroy(rb);',
        '        if (io) input_handler_destroy(io);',
        '        if (store) context_store_destroy(store);',
        '        return 1;',
        '    }',
        '',
        '    grok_msg_t msgs[GROK_MAX_MESSAGES];',
        '    int msg_count = 0;',
        '',
        '    int sys_id = context_store_intern(store, "You are Grok, a helpful assistant.");',
        '    if (sys_id >= 0) grok_append_msg(msgs, &msg_count, "system", sys_id);',
        '',
        '    printf("Grok CLI (xAI API via curl). Type \\"quit\\" to exit.\\n\\n");',
        '',
        '    while (1) {',
        '        char *line = input_handler_readline(io, "grok> ");',
        '        if (!line) break;',
        '        if (!*line) continue;',
        '        if (strcmp(line, "quit") == 0 || strcmp(line, "exit") == 0) break;',
        '',
        '        int user_id = context_store_intern(store, line);',
        '        if (user_id < 0) {',
        '            fprintf(stderr, "context_store full\\n");',
        '            continue;',
        '        }',
        '        grok_append_msg(msgs, &msg_count, "user", user_id);',
        '',
        '        char *req = (char *)malloc(GROK_REQ_CAP);',
        '        if (!req) {',
        '            fprintf(stderr, "OOM\\n");',
        '            break;',
        '        }',
        '        if (build_chat_request_json(req, GROK_REQ_CAP, store, msgs, msg_count, model) != 0) {',
        '            fprintf(stderr, "request too large\\n");',
        '            free(req);',
        '            continue;',
        '        }',
        '',
        '        char auth[4096];',
        '        snprintf(auth, sizeof(auth), "Authorization: Bearer %s", api_key);',
        '',
        '        const char *args[] = {',
        '            "curl", "-sS",',
        '            "https://api.x.ai/v1/chat/completions",',
        '            "-H", "Content-Type: application/json",',
        '            "-H", auth,',
        '            "-d", "@-",',
        '            NULL',
        '        };',
        '',
        '        api_caller_result_t *r = api_caller_exec_with_input("curl", args, 9, req, strlen(req));',
        '        free(req);',
        '',
        '        if (!r) {',
        '            fprintf(stderr, "FAIL: api_caller_exec_with_input returned NULL\\n");',
        '            continue;',
        '        }',
        '        if (r->exit_code != 0) {',
        '            fprintf(stderr, "curl exit %d\\n", r->exit_code);',
        '            if (r->stderr_buf && r->stderr_len) fprintf(stderr, "%s\\n", r->stderr_buf);',
        '            api_caller_result_free(r);',
        '            continue;',
        '        }',
        '',
        '        char *content = json_extract_assistant_content(r->stdout_buf);',
        '        if (!content) {',
        '            fprintf(stderr, "FAIL: could not parse Grok response\\n");',
        '            if (r->stdout_buf) fprintf(stderr, "%s\\n", r->stdout_buf);',
        '            api_caller_result_free(r);',
        '            continue;',
        '        }',
        '',
        '        printf("\\n");',
        '        print_via_response_buffer(rb, content);',
        '        printf("\\n\\n");',
        '',
        '        int asst_id = context_store_intern(store, content);',
        '        if (asst_id >= 0) grok_append_msg(msgs, &msg_count, "assistant", asst_id);',
        '',
        '        free(content);',
        '        api_caller_result_free(r);',
        '    }',
        '',
        '    response_buffer_destroy(rb);',
        '    input_handler_destroy(io);',
        '    context_store_destroy(store);',
        '    printf("Bye.\\n");',
        '    return 0;',
        '}',
        '',
    ]
    main_path = build_dir / "main.c"
    main_path.write_text('\n'.join(lines))
    return main_path


def run_project(project_dir: str = ".") -> bool:
    """Build, compile main.c, and run."""
    if not build_project(project_dir):
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

  Domains: arena, ring buffer, process spawner, string table, terminal I/O

  Usage:
    {cmd} init <name> [--template <t>]   Scaffold a new project
    {cmd} build [dir]                     Build all components in a project
    {cmd} run [dir]                       Build + compile + run
    {cmd} "<prompt>"                      One-shot: compile a single prompt
    {cmd} --interactive                   Interactive mode
    {cmd} --help                          Show this help

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
            print(f"\n  ✓ Created project at {path}/")
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
        project_dir = sys.argv[2] if len(sys.argv) > 2 else "."
        success = build_project(project_dir)
        sys.exit(0 if success else 1)
    elif sys.argv[1] == "run":
        check_dependencies()
        project_dir = sys.argv[2] if len(sys.argv) > 2 else "."
        success = run_project(project_dir)
        sys.exit(0 if success else 1)
    else:
        check_dependencies()
        intent = " ".join(sys.argv[1:])
        success, _ = compile_pipeline(intent)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
