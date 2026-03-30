"""
Natural language → WasmSpec translation via LLM structured output.

One LLM call with JSON schema produces the complete WasmSpec including
functions, memory layout, globals, invariants, and test cases.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .wasm_spec import WasmSpec, WASM_SPEC_JSON_SCHEMA, spec_from_dict

if TYPE_CHECKING:
    from .nlah import NlahPrompt

SYSTEM_PROMPT = """\
You translate natural language descriptions into formal WebAssembly module specifications as JSON.

Rules:
- name must be snake_case, valid wasm export name
- All exported functions must have explicit param types and result types (i32, i64, f32, f64)
- For pure-compute modules (math, data structures, allocators): wasi_imports must be empty
- Generate concrete test cases that exercise the core functionality
- Set size_budget_bytes based on complexity: simple functions ~1024, moderate ~4096, complex ~65536
- memory.min_pages: use 1 for most modules, more for modules that need >64KB
- Use globals for mutable state (bump pointer offsets, counters, etc.)
- Write invariants using the constraint DSL: identifiers, numbers, comparison ops (<, <=, >, >=, ==, !=), arithmetic (+, -, *), and field access (.pages, .size, .offset, .length)
- Invariant examples: "alloc_offset <= memory.size", "count >= 0", "count <= max_entries"
- preconditions/postconditions are human-readable descriptions (not DSL)
- algorithm_notes should describe the implementation approach

For a function that adds two i32 numbers, you would produce:
{
  "name": "add",
  "description": "Adds two 32-bit integers",
  "functions": [{"name": "add", "params": [{"name": "a", "type": "i32"}, {"name": "b", "type": "i32"}], "results": ["i32"]}],
  "tests": [{"function": "add", "args": [{"type": "i32", "value": 3}, {"type": "i32", "value": 5}], "expected": {"type": "i32", "value": 8}}],
  "size_budget_bytes": 1024
}

For a bump allocator with 4KB capacity:
{
  "name": "bump_alloc",
  "description": "Bump allocator with 4KB capacity and 16-byte alignment",
  "functions": [
    {"name": "alloc", "params": [{"name": "size", "type": "i32"}], "results": ["i32"], "preconditions": ["size > 0"], "postconditions": ["returns aligned pointer or 0 on failure"], "side_effects": ["advances bump pointer"]},
    {"name": "reset", "params": [], "results": [], "side_effects": ["resets bump pointer to 0"]},
    {"name": "remaining", "params": [], "results": ["i32"], "postconditions": ["returns bytes remaining"]}
  ],
  "memory": {"min_pages": 1, "max_pages": 1, "regions": [{"name": "heap", "offset_expr": "0", "size_expr": "4096"}]},
  "globals": [{"name": "offset", "type": "i32", "mutable": true, "initial_value": 0}],
  "invariants": [
    {"name": "offset_bounded", "expression": "offset <= 4096", "kind": "runtime"},
    {"name": "offset_non_negative", "expression": "offset >= 0", "kind": "runtime"}
  ],
  "constants": {"capacity": 4096, "alignment": 16},
  "tests": [
    {"function": "alloc", "args": [{"type": "i32", "value": 32}], "expected": {"type": "i32", "value": 0}, "description": "first alloc returns offset 0"},
    {"function": "remaining", "args": [], "expected": {"type": "i32", "value": 4064}, "description": "remaining after 32-byte alloc"}
  ],
  "size_budget_bytes": 4096,
  "algorithm_notes": "Linear bump allocator. alloc rounds up size to alignment, advances offset global. Returns 0-based pointer into linear memory. reset sets offset back to 0."
}
"""

JSON_SCHEMA = json.dumps(WASM_SPEC_JSON_SCHEMA)


def intent_to_wasm_spec(intent: str, nlah: "NlahPrompt | None" = None) -> WasmSpec:
    """
    Translate natural language intent into a WasmSpec.

    Uses LLM structured output. No regex fallback — wasm specs are too
    complex for pattern matching.
    """
    from . import llm

    enriched_intent = intent
    if nlah and (nlah.contracts.preconditions or nlah.contracts.postconditions):
        lines: list[str] = [intent, "", "NLAH CONTRACTS:"]
        if nlah.contracts.preconditions:
            lines.append("Preconditions:")
            lines.extend(f"- {p}" for p in nlah.contracts.preconditions)
        if nlah.contracts.postconditions:
            lines.append("Postconditions:")
            lines.extend(f"- {p}" for p in nlah.contracts.postconditions)
        enriched_intent = "\n".join(lines).strip()

    if llm.is_debug() and enriched_intent != intent:
        print(
            f"[DEBUG] intent_to_wasm_spec enriched intent ({len(enriched_intent)} chars):\n{enriched_intent}",
            flush=True,
        )

    params = llm.structured(enriched_intent, SYSTEM_PROMPT, JSON_SCHEMA, timeout=120)
    if params and isinstance(params, dict):
        try:
            spec = spec_from_dict(params)
            if nlah:
                if nlah.size_budget is not None:
                    spec.size_budget_bytes = nlah.size_budget
                if nlah.wasi is not None:
                    spec.wasi_imports = list(nlah.wasi)
            print(f"  (translated by {llm.get_backend()})")
            return spec
        except (KeyError, TypeError, ValueError) as e:
            print(f"  Warning: LLM output parse error: {e}")
            print("  Retrying...")
            # One retry with explicit error feedback
            retry_prompt = (
                f"{enriched_intent}\n\n"
                f"Previous attempt had a parse error: {e}\n"
                f"Please fix and try again."
            )
            params = llm.structured(retry_prompt, SYSTEM_PROMPT, JSON_SCHEMA, timeout=120)
            if params and isinstance(params, dict):
                spec = spec_from_dict(params)
                if nlah:
                    if nlah.size_budget is not None:
                        spec.size_budget_bytes = nlah.size_budget
                    if nlah.wasi is not None:
                        spec.wasi_imports = list(nlah.wasi)
                print(f"  (translated by {llm.get_backend()} on retry)")
                return spec

    raise RuntimeError(
        "Failed to translate intent to WasmSpec. "
        "Ensure an LLM backend is available (claude CLI, ANTHROPIC_API_KEY, or OPENAI_API_KEY)."
    )
