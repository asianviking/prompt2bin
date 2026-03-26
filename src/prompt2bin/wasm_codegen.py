"""
WasmSpec → WAT code generation via LLM.

Generates WebAssembly Text Format (WAT) from a verified WasmSpec.
Includes a structured retry loop: on wat2wasm failure, parse the error
and re-prompt the LLM with targeted feedback.
"""

from __future__ import annotations

import re

from .wasm_spec import WasmSpec, WasmType


SYSTEM_PROMPT = """\
You are a WebAssembly Text Format (WAT) code generator for prompt2bin.

You receive a formally verified WasmSpec and produce a complete WAT module.

RULES:
- Output ONLY valid WAT code. No markdown fences, no explanation outside the code.
- The module must be a single (module ...) s-expression.
- Export ALL functions listed in the spec.
- Use the exact function names, param types, and result types from the spec.
- If memory is needed, declare (memory (export "memory") <min_pages> <max_pages>).
- Declare globals as specified (mutable with (mut <type>)).
- Use global.get/global.set for mutable state.
- For alignment: use i32.and with bitmask ~(align-1) after adding (align-1).
- For bounds checking: use i32.le_u / i32.ge_u comparisons.
- Keep the code minimal and correct. Prefer simple stack-based operations.
- Do NOT use WASI imports unless the spec explicitly lists them in wasi_imports.
- All numeric literals must match the declared types (i32.const for i32, etc.).
- Use local.get/local.set for function parameters and locals.
- Do NOT include start functions unless the spec requires initialization.
"""


def spec_to_prompt(spec: WasmSpec, error_context: str = "") -> str:
    """Convert a WasmSpec into a generation prompt."""
    lines = [f"Generate a complete WAT module implementing this specification:\n"]

    lines.append(f"Module name: {spec.name}")
    lines.append(f"Description: {spec.description}")

    if spec.algorithm_notes:
        lines.append(f"\nImplementation notes: {spec.algorithm_notes}")

    # Memory
    mem = spec.memory
    if mem.min_pages > 0:
        lines.append(f"\nMemory: {mem.min_pages} min pages, {mem.max_pages} max pages (export as \"memory\")")
        for region in mem.regions:
            lines.append(f"  Region '{region.name}': offset={region.offset_expr}, size={region.size_expr}")

    # Globals
    if spec.globals:
        lines.append("\nGlobals:")
        for g in spec.globals:
            mut = "mutable" if g.mutable else "immutable"
            lines.append(f"  {g.name}: {g.type.value} ({mut}, init={g.initial_value})")

    # Constants
    if spec.constants:
        lines.append("\nConstants:")
        for name, val in spec.constants.items():
            lines.append(f"  {name} = {val}")

    # Functions
    lines.append("\nFunctions (ALL must be exported):")
    for func in spec.functions:
        params = ", ".join(f"{p.name}: {p.type.value}" for p in func.params)
        results = ", ".join(r.value for r in func.results)
        lines.append(f"  (export \"{func.name}\") ({params}) -> ({results})")
        if func.preconditions:
            for pre in func.preconditions:
                lines.append(f"    PRE: {pre}")
        if func.postconditions:
            for post in func.postconditions:
                lines.append(f"    POST: {post}")
        if func.side_effects:
            for effect in func.side_effects:
                lines.append(f"    EFFECT: {effect}")

    # Invariants
    if spec.invariants:
        lines.append("\nInvariants (code must maintain these):")
        for inv in spec.invariants:
            lines.append(f"  {inv.name}: {inv.expression}")

    if error_context:
        lines.append(f"\n--- PREVIOUS ATTEMPT FAILED ---\n{error_context}\nFix the error and regenerate the complete WAT module.")

    return "\n".join(lines)


def extract_wat(raw: str) -> str:
    """Extract WAT code from LLM response, stripping markdown fences."""
    # Try markdown code block
    match = re.search(r"```(?:wat|wasm|lisp|scheme)?\s*\n(.*?)```", raw, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Look for (module ...)
    match = re.search(r"(\(module\b.*\))\s*$", raw, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Fallback: if it starts with (module, use as-is
    stripped = raw.strip()
    if stripped.startswith("(module"):
        return stripped

    return stripped


def generate_wat(
    spec: WasmSpec,
    verified_properties: list[str] | None = None,
    max_retries: int = 3,
) -> str | None:
    """
    Generate WAT code from a verified WasmSpec.

    Uses LLM to generate WAT, validates with wat2wasm in the retry loop.
    Returns WAT string on success, None if all attempts fail.
    """
    from . import llm as _llm_mod
    from .wasm_validate import validate_wat

    debug = _llm_mod.is_debug()
    prompt = spec_to_prompt(spec)

    collected_errors: list[str] = []

    for attempt in range(1, max_retries + 1):
        if debug:
            print(f"[DEBUG] wasm_codegen attempt {attempt}/{max_retries}", flush=True)

        raw = _llm_mod.generate(prompt, SYSTEM_PROMPT)
        if raw is None:
            print(f"  LLM returned no output on attempt {attempt}")
            collected_errors.append(f"Attempt {attempt}: LLM returned no output")
            continue

        if debug:
            print(f"[DEBUG] Raw LLM response ({len(raw)} chars):\n{raw[:500]}", flush=True)

        wat_code = extract_wat(raw)

        # Validate with wat2wasm
        ok, wasm_bytes, error_msg = validate_wat(wat_code)
        if ok:
            return wat_code

        # Build structured error feedback
        error_feedback = _build_error_feedback(wat_code, error_msg or "Unknown error")
        collected_errors.append(f"Attempt {attempt}: {error_msg}")

        if attempt < max_retries:
            print(f"  wat2wasm rejected attempt {attempt}, retrying with error context...")
            prompt = spec_to_prompt(spec, error_context=error_feedback)
        else:
            print(f"  wat2wasm rejected all {max_retries} attempts:")
            for err in collected_errors:
                print(f"    {err[:200]}")

    # Check size budget after successful validation
    return None


def _build_error_feedback(wat_code: str, error_msg: str) -> str:
    """Build structured error feedback for retry prompts."""
    lines = wat_code.split("\n")

    # Try to extract line number from error
    line_match = re.search(r":(\d+):", error_msg)
    line_num = int(line_match.group(1)) if line_match else None

    # Extract error type
    error_type = "unknown"
    if "type mismatch" in error_msg:
        error_type = "type_mismatch"
    elif "unknown" in error_msg.lower():
        error_type = "unknown_instruction"
    elif "unexpected token" in error_msg or "expected" in error_msg:
        error_type = "malformed_sexp"
    elif "undeclared" in error_msg:
        error_type = "undeclared_identifier"

    feedback = [
        f"Error type: {error_type}",
        f"Error message: {error_msg}",
    ]

    if line_num and 0 < line_num <= len(lines):
        start = max(0, line_num - 3)
        end = min(len(lines), line_num + 2)
        context = "\n".join(f"  {i + 1}: {lines[i]}" for i in range(start, end))
        feedback.append(f"Line: {line_num}")
        feedback.append(f"Context:\n{context}")

    feedback.append(f"\nFull WAT that failed:\n{wat_code}")

    return "\n".join(feedback)
