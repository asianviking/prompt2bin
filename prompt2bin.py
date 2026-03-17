#!/usr/bin/env python3
"""
prompt2bin — from natural language to verified machine code.

Usage:
    python prompt2bin.py "I need an arena allocator with 4KB pages and 16-byte alignment"
    python prompt2bin.py --interactive
"""

import sys
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


def compile(intent: str, output_path: str | None = None) -> bool:
    """
    Full pipeline: intent → spec → verify → C code.
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

    # ── Summary ──
    print(f"\n{'═' * 60}")
    print(f"  ✓ {output_path} — verified arena allocator")
    print(f"    Compile with: gcc -O2 -o program your_main.c")
    print(f"    (it's a header-only library, just #include \"{output_path}\")")
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
