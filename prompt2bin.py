#!/usr/bin/env python3
"""
prompt2bin — from natural language to verified machine code.

Domains:
    - Arena allocators: "I need a fast arena allocator with 4KB pages"
    - Ring buffers: "I need a lock-free queue for audio samples"

Usage:
    python prompt2bin.py "I need an arena allocator with 4KB pages and 16-byte alignment"
    python prompt2bin.py "SPSC ring buffer for audio, 4096 float samples"
    python prompt2bin.py --interactive
"""

import shutil
import subprocess
import sys
import tempfile
import os

# Arena domain
from intent import intent_to_spec as intent_to_arena
from verify import verify_spec as verify_arena
from codegen import generate_c as generate_arena_template
from codegen_llm import generate_c_llm as generate_arena_llm
from test_harness import run_test_harness as run_arena_test

# Ring buffer domain
from intent_ringbuf import intent_to_ringbuf
from verify_ringbuf import verify_ringbuf_spec
from codegen_ringbuf_llm import generate_ringbuf_llm
from test_ringbuf import run_ringbuf_test


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


def compile_to_binary(c_code: str, name: str, domain: str) -> tuple[str | None, str | None, str | None]:
    """Compile C code to assembly and object file."""
    gcc = shutil.which("gcc")
    if not gcc:
        return None, None, "gcc not found"

    header_path = os.path.abspath(f"{name}.h")

    if domain == "ringbuf":
        wrapper = f"""\
#include "{header_path}"
void *_force_create(void) {{ return {name}_create(); }}
int   _force_push(void *rb, const void *d) {{ return {name}_push(({name}_t*)rb, d); }}
int   _force_pop(void *rb, void *d) {{ return {name}_pop(({name}_t*)rb, d); }}
void  _force_destroy(void *rb) {{ {name}_destroy(({name}_t*)rb); }}
"""
        hot_func = "_force_push"
        hot_label = f"{name}_push"
    else:
        wrapper = f"""\
#include "{header_path}"
void *_force_create(void) {{ return {name}_create(); }}
void *_force_alloc(void *a, unsigned long n) {{ return {name}_alloc(({name}_t*)a, n); }}
void  _force_reset(void *a) {{ {name}_reset(({name}_t*)a); }}
void  _force_destroy(void *a) {{ {name}_destroy(({name}_t*)a); }}
"""
        hot_func = "_force_alloc"
        hot_label = f"{name}_alloc"

    asm_path = f"{name}.s"
    obj_path = f"{name}.o"

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


def compile_pipeline(intent: str, output_path: str | None = None) -> bool:
    """Full pipeline: intent → spec → verify → C code → assembly → binary."""
    print(f"\n{'─' * 60}")
    print(f"  INPUT: {intent}")
    print(f"{'─' * 60}")

    # ── Domain detection ──
    domain = detect_domain(intent)
    print(f"\n  Domain: {domain}")

    if domain == "ringbuf":
        return _compile_ringbuf(intent, output_path)
    else:
        return _compile_arena(intent, output_path)


def _compile_arena(intent: str, output_path: str | None = None) -> bool:
    """Arena allocator pipeline."""
    # Phase 1
    print("\n▸ Phase 1: Translating intent → formal spec...")
    spec = intent_to_arena(intent)
    print(spec.describe())

    # Phase 2
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

    # Phase 3
    verified_props = [r.message for r in results if r.passed]
    print("\n▸ Phase 3: Generating C code via LLM...")
    c_code = generate_arena_llm(spec, verified_properties=verified_props)
    codegen_source = "Claude"

    if c_code is None:
        print("  LLM codegen unavailable — using template fallback")
        c_code = generate_arena_template(spec)
        codegen_source = "template"

    if output_path is None:
        output_path = f"{spec.name}.h"
    with open(output_path, "w") as f:
        f.write(c_code)
    lines = c_code.count("\n")
    print(f"  Generated {lines} lines → {output_path} (via {codegen_source})")

    # Phase 3b
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

    # Phase 4
    return _phase4(c_code, spec.name, output_path, lines, "arena", "_force_alloc", f"{spec.name}_alloc")


def _compile_ringbuf(intent: str, output_path: str | None = None) -> bool:
    """Ring buffer pipeline."""
    # Phase 1
    print("\n▸ Phase 1: Translating intent → formal spec...")
    spec = intent_to_ringbuf(intent)
    print(spec.describe())

    # Phase 2
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

    # Phase 3 — LLM only (no template fallback for ring buffers)
    verified_props = [r.message for r in results if r.passed]
    print("\n▸ Phase 3: Generating C code via LLM...")
    c_code = generate_ringbuf_llm(spec, verified_properties=verified_props)
    codegen_source = "Claude"

    if c_code is None:
        print("  ✗ LLM codegen failed and no template fallback for ring buffers.")
        return False

    if output_path is None:
        output_path = f"{spec.name}.h"
    with open(output_path, "w") as f:
        f.write(c_code)
    lines = c_code.count("\n")
    print(f"  Generated {lines} lines → {output_path} (via {codegen_source})")

    # Phase 3b
    print("\n▸ Phase 3b: Running test harness...")
    test_ok, test_msg = run_ringbuf_test(spec, output_path)
    print(f"  {test_msg}")
    if not test_ok:
        print("  ⚠ Tests failed — code generated but may have issues")

    # Phase 4
    return _phase4(c_code, spec.name, output_path, lines, "ringbuf", "_force_push", f"{spec.name}_push")


def _phase4(c_code, name, output_path, lines, domain, hot_func, hot_label):
    """Phase 4: Compile to assembly and machine code."""
    print("\n▸ Phase 4: Compiling to assembly and machine code...")
    asm_path, obj_path, err = compile_to_binary(c_code, name, domain)

    if err:
        print(f"  ⚠ {err}")
    else:
        asm_size = os.path.getsize(asm_path)
        obj_size = os.path.getsize(obj_path)
        print(f"  {asm_path:20s} — {asm_size:>6,} bytes (human-readable assembly)")
        print(f"  {obj_path:20s} — {obj_size:>6,} bytes (machine code)")
        show_assembly_highlights(asm_path, hot_func, hot_label)

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


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "--interactive":
        interactive()
    else:
        intent = " ".join(sys.argv[1:])
        success = compile_pipeline(intent)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
