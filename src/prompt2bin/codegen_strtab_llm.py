"""
LLM-powered C code generator for string tables.

Same pattern as other domains: spec → LLM → GCC check → retry.
"""

import os
import re
import shutil
import subprocess
import tempfile
from . import llm
from .spec import StringTableSpec, HashFunction


SYSTEM_PROMPT = "You generate C header-only libraries. Output ONLY C code. No markdown, no explanation."


def _spec_to_prompt(spec: StringTableSpec, verified_properties: list[str] | None = None) -> str:
    props = verified_properties or []
    props_text = "\n".join(f"  - {p}" for p in props) if props else "  (none provided)"

    hash_desc = {
        HashFunction.FNV1A: "FNV-1a (offset=2166136261, prime=16777619 for 32-bit)",
        HashFunction.DJBX33A: "DJB x33a (hash = hash * 33 + c, seed=5381)",
    }

    return f"""\
Generate a complete C header-only string table (intern pool) implementation:

Name: {spec.name}
Max strings: {spec.max_strings}
Total string storage: {spec.max_total_bytes} bytes
Max single string: {spec.max_string_len} bytes
Hash function: {hash_desc[spec.hash_func]}
Hash table: {spec.hash_table_size} buckets ({spec.hash_bits} bits, mask=0x{spec.hash_table_size - 1:X})
Null terminated: {spec.null_terminated}

Verified properties (your code MUST maintain these):
{props_text}

Required types and functions:

- {spec.name}_t *{spec.name}_create(void)
  Allocate and initialize the string table.

- int {spec.name}_intern({spec.name}_t *tab, const char *str)
  Intern a string. Returns a non-negative ID (0-based index).
  If the string already exists, returns the existing ID (deduplication).
  Returns -1 if table is full or string too long.

- const char *{spec.name}_lookup({spec.name}_t *tab, int id)
  Look up a string by ID. Returns NULL if ID is invalid.

- int {spec.name}_find({spec.name}_t *tab, const char *str)
  Find a string without interning. Returns ID or -1 if not found.

- int {spec.name}_count({spec.name}_t *tab)
  Return number of interned strings.

- void {spec.name}_destroy({spec.name}_t *tab)
  Free the string table and all storage.

Implementation notes:
- Hash table with open addressing (linear probing) or chaining
- String storage in a flat char buffer with offset tracking
- Each entry stores: hash, offset into string buffer, length
- Deduplication: on intern, first check if string exists via hash + strcmp
- All strings null-terminated in storage
- Include guard: {spec.name.upper()}_H
- Must compile with -Wall -Werror on Linux
- Include: string.h, stdlib.h, stdint.h
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


def generate_strtab_llm(
    spec: StringTableSpec,
    verified_properties: list[str] | None = None,
    max_retries: int = 1,
) -> str | None:
    """Generate string table C code via LLM backend. Returns None on failure."""
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
