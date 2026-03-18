"""
Test harness for terminal I/O implementations.

Generates, compiles, and runs C tests that verify:
- Create/destroy lifecycle
- History add and retrieval
- History count
- History ring buffer wraparound
- Set prompt
"""

import os
import shutil
import subprocess
import tempfile
from .spec import TermIOSpec


def generate_test_c(spec: TermIOSpec, header_path: str) -> str:
    """Generate a C test program for terminal I/O.

    Note: We can't easily test readline() in an automated harness
    (it reads from stdin interactively), so we test the history
    and lifecycle functions which are fully testable.
    """
    name = spec.name

    return f"""\
#include <stdio.h>
#include <string.h>
#include "{os.path.abspath(header_path)}"

int main(void) {{
    /* Test 1: create */
    {name}_t *ctx = {name}_create();
    if (!ctx) {{ fprintf(stderr, "FAIL: create returned NULL\\n"); return 1; }}

    /* Test 2: initial history count is 0 */
    if ({name}_history_count(ctx) != 0) {{
        fprintf(stderr, "FAIL: initial history count should be 0, got %d\\n",
                {name}_history_count(ctx));
        return 1;
    }}

    /* Test 3: add to history and retrieve */
    {name}_history_add(ctx, "first command");
    if ({name}_history_count(ctx) != 1) {{
        fprintf(stderr, "FAIL: history count should be 1 after add\\n");
        return 1;
    }}
    const char *h = {name}_history_get(ctx, 0);
    if (h == NULL || strcmp(h, "first command") != 0) {{
        fprintf(stderr, "FAIL: history_get(0) returned '%s'\\n", h ? h : "NULL");
        return 1;
    }}

    /* Test 4: add more and verify ordering (0 = most recent) */
    {name}_history_add(ctx, "second command");
    {name}_history_add(ctx, "third command");
    if ({name}_history_count(ctx) != 3) {{
        fprintf(stderr, "FAIL: history count should be 3, got %d\\n",
                {name}_history_count(ctx));
        return 1;
    }}
    h = {name}_history_get(ctx, 0);
    if (h == NULL || strcmp(h, "third command") != 0) {{
        fprintf(stderr, "FAIL: most recent should be 'third command', got '%s'\\n",
                h ? h : "NULL");
        return 1;
    }}
    h = {name}_history_get(ctx, 2);
    if (h == NULL || strcmp(h, "first command") != 0) {{
        fprintf(stderr, "FAIL: oldest should be 'first command', got '%s'\\n",
                h ? h : "NULL");
        return 1;
    }}

    /* Test 5: out-of-range returns NULL */
    if ({name}_history_get(ctx, -1) != NULL) {{
        fprintf(stderr, "FAIL: history_get(-1) should return NULL\\n");
        return 1;
    }}
    if ({name}_history_get(ctx, 999) != NULL) {{
        fprintf(stderr, "FAIL: history_get(999) should return NULL\\n");
        return 1;
    }}

    /* Test 6: history wraparound — fill beyond capacity */
    for (int i = 0; i < {spec.history_size} + 10; i++) {{
        char buf[128];
        snprintf(buf, sizeof(buf), "cmd_%d", i);
        {name}_history_add(ctx, buf);
    }}
    /* Count should be capped at history_size */
    int cnt = {name}_history_count(ctx);
    if (cnt > {spec.history_size}) {{
        fprintf(stderr, "FAIL: history count %d exceeds capacity {spec.history_size}\\n", cnt);
        return 1;
    }}
    /* Most recent should be the last one we added */
    {{
        char expected[128];
        snprintf(expected, sizeof(expected), "cmd_%d", {spec.history_size} + 10 - 1);
        h = {name}_history_get(ctx, 0);
        if (h == NULL || strcmp(h, expected) != 0) {{
            fprintf(stderr, "FAIL: after wraparound, most recent should be '%s', got '%s'\\n",
                    expected, h ? h : "NULL");
            return 1;
        }}
    }}

    /* Test 7: set_prompt doesn't crash */
    {name}_set_prompt(ctx, "test> ");

    /* Cleanup */
    {name}_destroy(ctx);
    fprintf(stderr, "PASS: all terminal I/O tests passed\\n");
    return 0;
}}
"""


def run_termio_test(spec: TermIOSpec, header_path: str) -> tuple[bool, str]:
    """Generate, compile, and run terminal I/O tests."""
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
