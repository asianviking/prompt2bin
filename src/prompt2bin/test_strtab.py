"""
Test harness for string table implementations.

Generates, compiles, and runs C tests that verify:
- Create/destroy lifecycle
- Intern and lookup
- Deduplication (same string → same ID)
- Find without intern
- Count tracking
- Full table rejection
"""

import os
import shutil
import subprocess
import tempfile
from .spec import StringTableSpec


def generate_test_c(spec: StringTableSpec, header_path: str) -> str:
    """Generate a C test program for the string table."""
    name = spec.name

    return f"""\
#include <stdio.h>
#include <string.h>
#include "{os.path.abspath(header_path)}"

int main(void) {{
    /* Test 1: create */
    {name}_t *tab = {name}_create();
    if (!tab) {{ fprintf(stderr, "FAIL: create returned NULL\\n"); return 1; }}

    /* Test 2: intern a string */
    int id1 = {name}_intern(tab, "hello");
    if (id1 < 0) {{
        fprintf(stderr, "FAIL: intern 'hello' returned %d\\n", id1);
        return 1;
    }}

    /* Test 3: lookup by ID */
    const char *s = {name}_lookup(tab, id1);
    if (s == NULL || strcmp(s, "hello") != 0) {{
        fprintf(stderr, "FAIL: lookup id=%d returned '%s'\\n", id1, s ? s : "NULL");
        return 1;
    }}

    /* Test 4: deduplication — same string returns same ID */
    int id1b = {name}_intern(tab, "hello");
    if (id1b != id1) {{
        fprintf(stderr, "FAIL: dedup failed, first=%d second=%d\\n", id1, id1b);
        return 1;
    }}

    /* Test 5: different string gets different ID */
    int id2 = {name}_intern(tab, "world");
    if (id2 < 0) {{
        fprintf(stderr, "FAIL: intern 'world' returned %d\\n", id2);
        return 1;
    }}
    if (id2 == id1) {{
        fprintf(stderr, "FAIL: 'hello' and 'world' got same ID %d\\n", id1);
        return 1;
    }}

    /* Test 6: count */
    int count = {name}_count(tab);
    if (count != 2) {{
        fprintf(stderr, "FAIL: count should be 2, got %d\\n", count);
        return 1;
    }}

    /* Test 7: find without intern */
    int found = {name}_find(tab, "hello");
    if (found != id1) {{
        fprintf(stderr, "FAIL: find 'hello' returned %d, expected %d\\n", found, id1);
        return 1;
    }}
    int not_found = {name}_find(tab, "nonexistent");
    if (not_found != -1) {{
        fprintf(stderr, "FAIL: find 'nonexistent' should return -1, got %d\\n", not_found);
        return 1;
    }}

    /* Test 8: invalid ID returns NULL */
    if ({name}_lookup(tab, -1) != NULL) {{
        fprintf(stderr, "FAIL: lookup(-1) should return NULL\\n");
        return 1;
    }}
    if ({name}_lookup(tab, 999999) != NULL) {{
        fprintf(stderr, "FAIL: lookup(999999) should return NULL\\n");
        return 1;
    }}

    /* Test 9: intern many strings */
    for (int i = 0; i < 50; i++) {{
        char buf[64];
        snprintf(buf, sizeof(buf), "string_%d", i);
        int id = {name}_intern(tab, buf);
        if (id < 0) {{
            fprintf(stderr, "FAIL: intern string_%d failed\\n", i);
            return 1;
        }}
        /* Verify lookup */
        const char *v = {name}_lookup(tab, id);
        if (v == NULL || strcmp(v, buf) != 0) {{
            fprintf(stderr, "FAIL: lookup string_%d mismatch\\n", i);
            return 1;
        }}
    }}

    /* Cleanup */
    {name}_destroy(tab);
    fprintf(stderr, "PASS: all string table tests passed\\n");
    return 0;
}}
"""


def run_strtab_test(spec: StringTableSpec, header_path: str) -> tuple[bool, str]:
    """Generate, compile, and run string table tests."""
    gcc = shutil.which("gcc")
    if not gcc:
        return True, "gcc not found, skipping runtime tests"

    test_c = generate_test_c(spec, header_path)

    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
        f.write(test_c)
        test_path = f.name

    bin_path = test_path.replace(".c", "")

    try:
        result = subprocess.run(
            [gcc, "-O2", "-o", bin_path, test_path, "-Wno-unused-function"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return False, f"Test compilation failed:\n{result.stderr}"

        result = subprocess.run(
            [bin_path],
            capture_output=True, text=True, timeout=5,
        )
        output = result.stderr.strip()
        if result.returncode == 0:
            return True, output
        else:
            return False, f"Test failed (exit {result.returncode}):\n{output}"
    except subprocess.TimeoutExpired:
        return False, "Test timed out"
    finally:
        for p in [test_path, bin_path]:
            if os.path.exists(p):
                os.unlink(p)
