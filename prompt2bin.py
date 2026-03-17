#!/usr/bin/env python3
"""
prompt2bin — from natural language to verified machine code.

Usage:
    python prompt2bin.py "I need an arena allocator with 4KB pages and 16-byte alignment"
    python prompt2bin.py --interactive
"""

import shutil
import subprocess
import sys
import tempfile
import os
from intent import intent_to_spec
from verify import verify_spec
from codegen import generate_c


BANNER = """
╔══════════════════════════════════════════════════════════╗
║  prompt2bin — intent → spec → verify → code             ║
║  Prototype: arena allocator domain                      ║
╚══════════════════════════════════════════════════════════╝
"""


def compile_to_binary(c_code: str, name: str) -> tuple[str | None, str | None, str | None]:
    """
    Compile C code down to assembly and object file.

    Returns (asm_path, obj_path, error_msg).
    Generates a thin wrapper .c that includes the header and
    instantiates each function so GCC has something to compile.
    """
    gcc = shutil.which("gcc")
    if not gcc:
        return None, None, "gcc not found"

    header_path = os.path.abspath(f"{name}.h")

    # Wrapper .c that forces the compiler to emit code for all functions
    wrapper = f"""\
#include "{header_path}"

/* Force emission of all functions so they appear in assembly */
void *_force_create(void) {{ return {name}_create(); }}
void *_force_alloc(void *a, unsigned long n) {{ return {name}_alloc(({name}_t*)a, n); }}
void  _force_reset(void *a) {{ {name}_reset(({name}_t*)a); }}
void  _force_destroy(void *a) {{ {name}_destroy(({name}_t*)a); }}
"""

    asm_path = f"{name}.s"
    obj_path = f"{name}.o"

    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
        f.write(wrapper)
        wrapper_path = f.name

    try:
        # Compile to assembly (human-readable)
        result = subprocess.run(
            [gcc, "-O2", "-S", "-masm=intel", "-fno-asynchronous-unwind-tables",
             "-fno-exceptions", "-o", asm_path, wrapper_path],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None, None, f"Assembly generation failed:\n{result.stderr}"

        # Compile to object file (machine code)
        result = subprocess.run(
            [gcc, "-O2", "-c", "-o", obj_path, wrapper_path],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return asm_path, None, f"Object compilation failed:\n{result.stderr}"

        return asm_path, obj_path, None

    finally:
        os.unlink(wrapper_path)


def show_assembly_highlights(asm_path: str, name: str):
    """Print the key assembly functions with annotation."""
    with open(asm_path) as f:
        asm = f.read()

    # Extract the alloc function — that's the interesting one
    lines = asm.split("\n")
    in_func = False
    func_lines = []
    target = f"_force_alloc"

    for line in lines:
        if target in line and ":" in line:
            in_func = True
            func_lines = [line]
            continue
        if in_func:
            if line.strip().startswith(".cfi_endproc") or (
                line.strip().startswith(".") and "size" in line and target in line
            ):
                break
            func_lines.append(line)

    if func_lines:
        # Filter out directives, keep only instructions
        instructions = [
            l for l in func_lines
            if l.strip() and not l.strip().startswith(".")
        ]
        print(f"\n  {name}_alloc assembly ({len(instructions)} instructions):\n")
        for line in instructions[:25]:  # Cap at 25 lines
            print(f"    {line}")
        if len(instructions) > 25:
            print(f"    ... ({len(instructions) - 25} more)")


def compile(intent: str, output_path: str | None = None) -> bool:
    """
    Full pipeline: intent → spec → verify → C code → assembly → binary.
    Returns True if verification passed and code was generated.
    """
    print(f"\n{'─' * 60}")
    print(f"  INPUT: {intent}")
    print(f"{'─' * 60}")

    # ── Phase 1: Intent → Spec ──
    print("\n▸ Phase 1: Translating intent → formal spec...")
    spec = intent_to_spec(intent)
    print(spec.describe())

    # ── Phase 2: Verify ──
    print("\n▸ Phase 2: Verifying spec with Z3...")
    results = verify_spec(spec)

    all_passed = True
    for r in results:
        print(r)
        if not r.passed:
            all_passed = False

    if not all_passed:
        print("\n✗ Verification FAILED. Code generation aborted.")
        print("  Fix the spec and retry. The following properties failed:")
        for r in results:
            if not r.passed:
                print(f"    - {r.property_name}: {r.message}")
        return False

    print(f"\n  All {len(results)} properties verified ✓")

    # ── Phase 3: Code Generation ──
    print("\n▸ Phase 3: Generating verified C code...")
    c_code = generate_c(spec)

    if output_path is None:
        output_path = f"{spec.name}.h"

    with open(output_path, "w") as f:
        f.write(c_code)

    lines = c_code.count("\n")
    print(f"  Generated {lines} lines → {output_path}")

    # ── Phase 4: Compile to assembly + machine code ──
    print("\n▸ Phase 4: Compiling to assembly and machine code...")
    asm_path, obj_path, err = compile_to_binary(c_code, spec.name)

    if err:
        print(f"  ⚠ {err}")
        print(f"  (C code is still valid — compile manually with gcc)")
    else:
        asm_size = os.path.getsize(asm_path)
        obj_size = os.path.getsize(obj_path)
        print(f"  {asm_path:20s} — {asm_size:>6,} bytes (human-readable assembly)")
        print(f"  {obj_path:20s} — {obj_size:>6,} bytes (machine code)")
        show_assembly_highlights(asm_path, spec.name)

    # ── Summary ──
    print(f"\n{'═' * 60}")
    print(f"  ✓ Complete pipeline: English → verified machine code")
    print(f"")
    print(f"    {output_path:20s}  C code ({lines} lines)")
    if asm_path:
        print(f"    {asm_path:20s}  x86-64 assembly")
    if obj_path:
        print(f"    {obj_path:20s}  machine code (linkable)")
    print(f"")
    print(f"    Link into your program:")
    print(f"      #include \"{output_path}\"")
    print(f"{'═' * 60}\n")

    return True


def interactive():
    """Interactive mode — keep prompting for intents."""
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

        compile(intent)


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "--interactive":
        interactive()
    else:
        intent = " ".join(sys.argv[1:])
        success = compile(intent)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
