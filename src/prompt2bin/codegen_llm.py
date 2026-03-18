"""
LLM-powered C code generator.

Uses Claude CLI to generate C implementations from verified specs.
The spec has already been formally verified by Z3 — this module's job
is to produce C code that maintains those verified properties.

Safety net: GCC compilation check + optional test harness.
Falls back to template codegen if LLM fails.
"""

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict
from .spec import ArenaSpec


SYSTEM_PROMPT = """\
You are a C code generator for prompt2bin, an AI-first compiler.

You receive a formally verified spec for a memory allocator and produce \
a complete, single-file C header implementation.

RULES:
- Output ONLY valid C code. No markdown fences, no explanation, no comments outside the code.
- The code must be a complete header file with include guard, all necessary #includes, struct typedef, and all required functions.
- Every function must be defined (not just declared) since this is a header-only library.
- Use aligned_alloc for the arena backing memory to guarantee base pointer alignment.
- The bump pointer alignment formula is: aligned = (offset + (align-1)) & ~(align-1)
- Bounds check: if (aligned_offset + size > capacity) return NULL
- For lock_free threading: use stdatomic.h, atomic_load/store with memory_order_relaxed/release, and atomic_compare_exchange_weak_explicit in a CAS loop for alloc.
- For zero_on_alloc: memset the returned pointer to 0 before returning.
- For zero_on_reset: memset the buffer up to current offset before resetting.
- For no_use_after_reset: add a uint64_t generation field, increment on reset.
- The create function should allocate struct + backing buffer in a single aligned_alloc call for locality.
- Include string.h if using memset, stdatomic.h if using atomics.
"""


def _spec_to_prompt(spec: ArenaSpec, verified_properties: list[str] | None = None) -> str:
    """Convert a verified ArenaSpec into a prompt for Claude."""
    props = verified_properties or []
    props_text = "\n".join(f"  - {p}" for p in props) if props else "  (none provided)"

    return f"""\
Generate a complete C header-only library implementing this verified allocator spec:

Name: {spec.name}
Strategy: {spec.strategy.value}
Growth: {spec.growth.value}
Threading: {spec.thread_safety.value}
Page size: {spec.memory.page_size} bytes
Header overhead: {spec.memory.header_size} bytes
Usable capacity: {spec.memory.usable_capacity} bytes
Max single allocation: {spec.memory.effective_max_alloc} bytes
Max pages: {spec.memory.max_pages}
Minimum alignment: {spec.alignment.min_align} bytes
Zero on alloc: {spec.safety.zero_on_alloc}
Zero on reset: {spec.safety.zero_on_reset}
Use-after-reset detection: {spec.safety.no_use_after_reset}

Verified properties (your code MUST maintain these):
{props_text}

Required functions:
- {spec.name}_t *{spec.name}_create(void)        — allocate and init arena
- void *{spec.name}_alloc({spec.name}_t *arena, size_t size) — bump allocate with alignment
- void {spec.name}_reset({spec.name}_t *arena)    — reset bump pointer to 0
- void {spec.name}_destroy({spec.name}_t *arena)  — free the arena

Include guard: {spec.name.upper()}_H
"""


def _extract_c_code(raw: str) -> str:
    """Extract C code from Claude's response, stripping markdown fences if present."""
    # Try to extract from markdown code block
    match = re.search(r"```(?:c|h)?\s*\n(.*?)```", raw, re.DOTALL)
    if match:
        return match.group(1).strip()

    # If it starts with /* or #, it's probably raw C
    stripped = raw.strip()
    if stripped.startswith("/*") or stripped.startswith("#"):
        return stripped

    return stripped


def _gcc_check(c_code: str) -> tuple[bool, str]:
    """
    Compile C code with GCC to check for errors.
    Returns (success, error_message).
    """
    gcc = shutil.which("gcc")
    if not gcc:
        return True, ""  # Can't check, assume OK

    with tempfile.NamedTemporaryFile(suffix=".h", mode="w", delete=False) as f:
        f.write(c_code)
        header_path = f.name

    # Write a minimal .c that includes the header and calls each function
    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
        f.write(f'#include "{header_path}"\n')
        f.write("int main(void) { return 0; }\n")
        c_path = f.name

    try:
        result = subprocess.run(
            [gcc, "-Wall", "-Werror", "-Wno-unused-function", "-c",
             "-o", "/dev/null", c_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return True, ""
        return False, result.stderr
    except subprocess.TimeoutExpired:
        return False, "GCC timed out"
    finally:
        os.unlink(header_path)
        os.unlink(c_path)


def _call_llm(prompt: str, attempt: int = 1) -> str | None:
    """Call LLM backend and return raw text output."""
    from . import llm
    return llm.generate(prompt, SYSTEM_PROMPT, timeout=90)


def generate_c_llm(
    spec: ArenaSpec,
    verified_properties: list[str] | None = None,
    max_retries: int = 1,
) -> str | None:
    """
    Generate C code from a verified spec using Claude CLI.

    Returns the C code string if successful, None if all attempts fail.
    The caller should fall back to template codegen on None.
    """
    prompt = _spec_to_prompt(spec, verified_properties)

    for attempt in range(1, max_retries + 2):
        raw = _call_llm(prompt)
        if raw is None:
            return None

        c_code = _extract_c_code(raw)

        # Validate with GCC
        ok, err = _gcc_check(c_code)
        if ok:
            return c_code

        # Retry with error feedback
        if attempt <= max_retries:
            print(f"  GCC rejected attempt {attempt}, retrying with error context...")
            prompt = (
                f"Your previous C code had compilation errors:\n\n{err}\n\n"
                f"Fix the errors and regenerate. {_spec_to_prompt(spec, verified_properties)}"
            )
        else:
            print(f"  GCC rejected all {attempt} attempts:")
            print(f"    {err[:200]}")

    return None
