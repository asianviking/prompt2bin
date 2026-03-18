"""
LLM-powered C code generator for terminal I/O.

Same pattern as other domains: spec → LLM → GCC check → retry.
"""

import os
import re
import shutil
import subprocess
import tempfile
from . import llm
from .spec import TermIOSpec, EditMode


SYSTEM_PROMPT = "You generate C header-only libraries. Output ONLY C code. No markdown, no explanation."


def _spec_to_prompt(spec: TermIOSpec, verified_properties: list[str] | None = None) -> str:
    props = verified_properties or []
    props_text = "\n".join(f"  - {p}" for p in props) if props else "  (none provided)"

    mode_desc = {
        EditMode.BASIC: "Simple line input using fgets (no cursor movement)",
        EditMode.READLINE: "Line editing with cursor movement and basic key handling",
    }

    return f"""\
Generate a complete C header-only terminal I/O / line editor implementation:

Name: {spec.name}
Max line length: {spec.max_line_len} bytes
History entries: {spec.history_size}
Max prompt length: {spec.prompt_max_len} bytes
Edit mode: {spec.edit_mode.value} ({mode_desc[spec.edit_mode]})

Verified properties (your code MUST maintain these):
{props_text}

Required types and functions:

- {spec.name}_t *{spec.name}_create(void)
  Allocate and initialize terminal I/O context.
  History is a ring buffer of char arrays.

- char *{spec.name}_readline({spec.name}_t *ctx, const char *prompt)
  Display prompt, read a line of input. Returns pointer to internal line buffer
  (caller must NOT free). Returns NULL on EOF.
  Adds non-empty lines to history automatically.

- void {spec.name}_history_add({spec.name}_t *ctx, const char *line)
  Manually add a line to history.

- const char *{spec.name}_history_get({spec.name}_t *ctx, int index)
  Get history entry by index (0 = most recent). Returns NULL if out of range.

- int {spec.name}_history_count({spec.name}_t *ctx)
  Return number of history entries.

- void {spec.name}_set_prompt({spec.name}_t *ctx, const char *prompt)
  Set default prompt string.

- void {spec.name}_destroy({spec.name}_t *ctx)
  Free the terminal I/O context and all buffers.

Implementation notes:
- Basic mode: use fgets for input, simple and portable
- History is a circular buffer: array of char[max_line_len], write_pos wraps
- readline returns pointer to internal buffer (valid until next readline call)
- Prompt is printed to stdout with fflush before reading
- Line has trailing newline stripped
- All buffers null-terminated
- Include guard: {spec.name.upper()}_H
- Must compile with -Wall -Werror on Linux
- Include: stdio.h, string.h, stdlib.h
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


def generate_termio_llm(
    spec: TermIOSpec,
    verified_properties: list[str] | None = None,
    max_retries: int = 1,
) -> str | None:
    """Generate terminal I/O C code via LLM backend. Returns None on failure."""
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
