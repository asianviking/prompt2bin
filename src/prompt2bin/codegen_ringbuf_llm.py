"""
LLM-powered C code generator for ring buffers.

Same pattern as arena: spec → Claude CLI → GCC check → retry.
"""

import os
import re
import shutil
import subprocess
import tempfile
from . import llm
from .spec import RingBufferSpec, RingBufferMode


SYSTEM_PROMPT = "You generate C header-only libraries. Output ONLY C code. No markdown, no explanation."


def _spec_to_prompt(spec: RingBufferSpec, verified_properties: list[str] | None = None) -> str:
    props = verified_properties or []
    props_text = "\n".join(f"  - {p}" for p in props) if props else "  (none provided)"

    mode_desc = {
        RingBufferMode.SPSC: "Single producer, single consumer — use relaxed atomics",
        RingBufferMode.MPSC: "Multiple producers, single consumer — CAS loop for push, simple atomic for pop",
        RingBufferMode.SPMC: "Single producer, multiple consumers — simple atomic for push, CAS loop for pop",
        RingBufferMode.MPMC: "Multiple producers, multiple consumers — CAS loops for both push and pop",
    }

    return f"""\
Generate a complete C header-only ring buffer implementation:

Name: {spec.name}
Mode: {spec.mode.value} ({mode_desc[spec.mode]})
Element size: {spec.element_size} bytes
Capacity: {spec.capacity} slots (power of two, mask=0x{spec.index_mask:X})
Buffer size: {spec.buffer_size_bytes} bytes
Cache line padding: {spec.cache_line_pad}
Blocking: {spec.blocking}
No data loss: {spec.no_data_loss} (reject push when full)

Verified properties (your code MUST maintain these):
{props_text}

Required functions:
- {spec.name}_t *{spec.name}_create(void)
- int {spec.name}_push({spec.name}_t *rb, const void *data)  — returns 0 success, -1 full
- int {spec.name}_pop({spec.name}_t *rb, void *out)           — returns 0 success, -1 empty
- void {spec.name}_destroy({spec.name}_t *rb)

Include guard: {spec.name.upper()}_H
"""


def _extract_c_code(raw: str) -> str:
    match = re.search(r"```(?:c|h)?\s*\n(.*?)```", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    stripped = raw.strip()
    if stripped.startswith("/*") or stripped.startswith("#"):
        return stripped
    return stripped


def _gcc_check(c_code: str) -> tuple[bool, str]:
    gcc = shutil.which("gcc")
    if not gcc:
        return True, ""

    with tempfile.NamedTemporaryFile(suffix=".h", mode="w", delete=False) as f:
        f.write(c_code)
        header_path = f.name

    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
        f.write(f'#include "{header_path}"\nint main(void) {{ return 0; }}\n')
        c_path = f.name

    try:
        result = subprocess.run(
            [gcc, "-Wall", "-Werror", "-Wno-unused-function", "-c", "-o", "/dev/null", c_path],
            capture_output=True, text=True, timeout=10,
        )
        return (result.returncode == 0), result.stderr
    except subprocess.TimeoutExpired:
        return False, "GCC timed out"
    finally:
        os.unlink(header_path)
        os.unlink(c_path)


def generate_ringbuf_llm(
    spec: RingBufferSpec,
    verified_properties: list[str] | None = None,
    max_retries: int = 1,
) -> str | None:
    """Generate ring buffer C code via LLM backend. Returns None on failure."""
    prompt = _spec_to_prompt(spec, verified_properties)

    for attempt in range(1, max_retries + 2):
        raw = llm.generate(prompt, SYSTEM_PROMPT, timeout=180)
        if not raw:
            print(f"  LLM returned no output")
            return None

        c_code = _extract_c_code(raw)
        if not c_code or len(c_code) < 50:
            print(f"  LLM returned insufficient code ({len(c_code) if c_code else 0} chars)")
            continue

        ok, err = _gcc_check(c_code)
        if ok:
            return c_code

        if attempt <= max_retries:
            print(f"  GCC rejected attempt {attempt}, retrying...")
            prompt = (
                f"Your previous C code had compilation errors:\n\n{err}\n\n"
                f"Fix the errors and regenerate. {_spec_to_prompt(spec, verified_properties)}"
            )
        else:
            print(f"  GCC rejected all attempts: {err[:200]}")

    return None
