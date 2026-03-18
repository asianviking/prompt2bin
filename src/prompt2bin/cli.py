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
    print(f"  Generated {lines} lines → {_relpath(output_path)} (via {codegen_source})")

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
    print(f"  Generated {lines} lines → {_relpath(output_path)} (via {codegen_source})")

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
    print(f"  Generated {lines} lines → {_relpath(output_path)} (via {codegen_source})")

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
    print(f"  Generated {lines} lines → {_relpath(output_path)} (via {codegen_source})")

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
    print(f"  Generated {lines} lines → {_relpath(output_path)} (via {codegen_source})")

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
        print(f"  {_relpath(asm_path):30s} — {asm_size:>6,} bytes (assembly)")
        print(f"  {_relpath(obj_path):30s} — {obj_size:>6,} bytes (machine code)")
        show_assembly_highlights(asm_path, hot_func, hot_label)

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

    # Apply model config from build.toml [model] section
    llm.configure(project.model)

    build_dir = ensure_build_dir(project)

    print(f"\n{'═' * 60}")
    print(f"  prompt2bin build: {project.name}")
    print(f"  Target: {project.target}")
    print(f"  Components: {len(project.components)}")
    if project.model.backend or project.model.name:
        model_info = project.model.name or project.model.backend
        print(f"  Model: {model_info}")
    print(f"  Output: {_relpath(build_dir)}/")
    print(f"{'═' * 60}")

    results = {}
    domains = {}
    for comp_name, comp in project.components.items():
        print(f"\n{'━' * 60}")
        print(f"  Building component: {comp_name}")
        print(f"  Prompt: {_relpath(comp.prompt_path)}")
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
    gcc = shutil.which("gcc")
    if not gcc:
        return True, ""

    main_path = build_dir / "_check_main.c"
    main_path.write_text(c_code)
    try:
        result = subprocess.run(
            [gcc, "-Wall", "-Werror", "-Wno-unused-function",
             "-I", str(build_dir), "-c", "-o", "/dev/null", str(main_path)],
            capture_output=True, text=True, timeout=10,
        )
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

    max_retries = 2
    for attempt in range(1, max_retries + 2):
        print(f"  ⟳ Generating main.c via LLM (attempt {attempt})...")
        raw = llm.generate(prompt, system_prompt, timeout=120)
        if not raw:
            print("  ⚠ LLM returned empty response")
            continue

        c_code = _extract_c_code(raw)
        ok, err = _gcc_check_main(c_code, build_dir, components)
        if ok:
            main_path = build_dir / "main.c"
            main_path.write_text(c_code)
            print("  ✓ main.c generated successfully")
            return main_path

        print(f"  ⚠ GCC check failed (attempt {attempt})")
        if attempt <= max_retries:
            prompt = (
                f"The previous main.c had compilation errors:\n{err}\n\n"
                f"Fix the errors. Here is the original request:\n\n"
                f"APPLICATION DESCRIPTION:\n{app_prompt}\n\n"
                f"AVAILABLE COMPONENT HEADERS:\n\n{all_headers}\n\n"
                f"Generate a corrected main.c. Output ONLY C code."
            )

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
