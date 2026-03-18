#!/usr/bin/env python3
"""
prompt2bin — from natural language to verified machine code.

Domains:
    - Arena allocators: "I need a fast arena allocator with 4KB pages"
    - Ring buffers: "I need a lock-free queue for audio samples"

Usage:
    # Single prompt
    python prompt2bin.py "I need an arena allocator with 4KB pages and 16-byte alignment"

    # Project build (reads build.toml)
    python prompt2bin.py build [project_dir]

    # Interactive
    python prompt2bin.py --interactive
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

# Project system
from .project import load_project, ensure_build_dir, init_project, TEMPLATES


BANNER = """
╔══════════════════════════════════════════════════════════╗
║  prompt2bin — intent → spec → verify → code             ║
║  Domains: arena allocator, ring buffer                  ║
╚══════════════════════════════════════════════════════════╝
"""

# Keywords that indicate ring buffer domain
RINGBUF_KEYWORDS = [
    "ring buffer", "ringbuffer", "ring buf", "ringbuf",
    "circular buffer", "circular queue", "fifo",
    "spsc", "mpsc", "spmc", "mpmc",
    "producer", "consumer",
    "queue", "channel",
    "audio buffer", "sample buffer",
    "log buffer", "event queue", "message queue",
]


def detect_domain(intent: str) -> str:
    """Detect which domain the user is asking about."""
    text = intent.lower()
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


def compile_pipeline(intent: str, output_dir: str = ".", name_override: str | None = None) -> bool:
    """Full pipeline: intent → spec → verify → C code → assembly → binary."""
    print(f"\n{'─' * 60}")
    print(f"  INPUT: {intent}")
    print(f"{'─' * 60}")

    domain = detect_domain(intent)
    print(f"\n  Domain: {domain}")

    if domain == "ringbuf":
        return _compile_ringbuf(intent, output_dir, name_override)
    else:
        return _compile_arena(intent, output_dir, name_override)


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
    for comp_name, comp in project.components.items():
        print(f"\n{'━' * 60}")
        print(f"  Building component: {comp_name}")
        print(f"  Prompt: {comp.prompt_path}")
        print(f"{'━' * 60}")

        ok = compile_pipeline(
            comp.prompt_text,
            output_dir=str(build_dir),
            name_override=comp_name,
        )
        results[comp_name] = ok

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

    print(f"")
    print(f"  Output: {build_dir}/")
    print(f"  Include: -I{build_dir}")
    print(f"{'═' * 60}\n")

    return len(failed) == 0


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
        compile_pipeline(intent)


def check_dependencies():
    """Check for required external tools and print helpful messages."""
    missing = []
    gcc = shutil.which("gcc")
    claude = shutil.which("claude")

    if not gcc:
        missing.append(("gcc", "Install GCC: apt install gcc (Linux) / xcode-select --install (macOS)"))
    if not claude:
        missing.append(("claude", "Install Claude CLI: https://docs.anthropic.com/en/docs/claude-cli"))

    if missing:
        print("\n  Missing dependencies:\n")
        for name, hint in missing:
            print(f"    ✗ {name} — {hint}")
        print()
        if not gcc:
            print("  GCC is required. Cannot continue.")
            sys.exit(1)
        if not claude:
            print("  ⚠ Claude CLI not found. Will fall back to regex parsing (less accurate).\n")


def show_help(cmd: str):
    """Print usage help."""
    print(f"""
  prompt2bin — from natural language to verified machine code

  Usage:
    {cmd} init <name> [--template <t>]   Scaffold a new project
    {cmd} build [dir]                     Build all components in a project
    {cmd} "<prompt>"                      One-shot: compile a single prompt
    {cmd} --interactive                   Interactive mode
    {cmd} --help                          Show this help

  Templates: {', '.join(TEMPLATES)}

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
            print(f"      2. {cmd} build {project_name}")
            print()
        except (FileExistsError, ValueError) as e:
            print(f"\n  ✗ {e}")
            sys.exit(1)
    elif sys.argv[1] == "build":
        check_dependencies()
        project_dir = sys.argv[2] if len(sys.argv) > 2 else "."
        success = build_project(project_dir)
        sys.exit(0 if success else 1)
    else:
        check_dependencies()
        intent = " ".join(sys.argv[1:])
        success = compile_pipeline(intent)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
