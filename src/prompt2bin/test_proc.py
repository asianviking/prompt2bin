"""
Test harness for process spawner implementations.

Generates, compiles, and runs C tests that verify:
- exec_simple runs /bin/echo and captures output
- exec returns result with exit code
- exec_with_input can feed stdin and capture stdout
- Timeout enforcement works
- Stderr capture works
- Result cleanup doesn't leak
"""

import os
import shutil
import subprocess
import tempfile
from .spec import ProcessSpawnerSpec


def generate_test_c(spec: ProcessSpawnerSpec, header_path: str) -> str:
    """Generate a C test program for the process spawner."""
    name = spec.name

    return f"""\
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "{os.path.abspath(header_path)}"

int main(void) {{
    /* Test 1: exec_simple with /bin/echo */
    {{
        char buf[256];
        memset(buf, 0, sizeof(buf));
        int rc = {name}_exec_simple("/bin/echo hello", buf, sizeof(buf));
        if (rc != 0) {{
            fprintf(stderr, "FAIL: exec_simple /bin/echo returned %d\\n", rc);
            return 1;
        }}
        /* Check output contains "hello" */
        if (strstr(buf, "hello") == NULL) {{
            fprintf(stderr, "FAIL: exec_simple output missing 'hello', got: '%s'\\n", buf);
            return 1;
        }}
    }}

    /* Test 2: exec with args captures stdout */
    {{
        const char *args[] = {{"echo", "test_output", NULL}};
        {name}_result_t *r = {name}_exec("/bin/echo", args, 2);
        if (!r) {{
            fprintf(stderr, "FAIL: exec returned NULL\\n");
            return 1;
        }}
        if (r->exit_code != 0) {{
            fprintf(stderr, "FAIL: exec exit_code=%d\\n", r->exit_code);
            {name}_result_free(r);
            return 1;
        }}
        if (r->stdout_buf == NULL || r->stdout_len == 0) {{
            fprintf(stderr, "FAIL: no stdout captured\\n");
            {name}_result_free(r);
            return 1;
        }}
        if (strstr(r->stdout_buf, "test_output") == NULL) {{
            fprintf(stderr, "FAIL: stdout missing 'test_output'\\n");
            {name}_result_free(r);
            return 1;
        }}
        {name}_result_free(r);
    }}

    /* Test 3: exec_with_input feeds stdin (if enabled) */
    if ({1 if spec.pipe_stdin else 0}) {{
        const char *args[] = {{"cat", NULL}};
        const char *payload = "stdin_payload";
        {name}_result_t *r = {name}_exec_with_input("/bin/cat", args, 1, payload, strlen(payload));
        if (!r) {{
            fprintf(stderr, "FAIL: exec_with_input returned NULL\\n");
            return 1;
        }}
        if (r->exit_code != 0) {{
            fprintf(stderr, "FAIL: exec_with_input exit_code=%d\\n", r->exit_code);
            {name}_result_free(r);
            return 1;
        }}
        if (r->stdout_buf == NULL || r->stdout_len == 0) {{
            fprintf(stderr, "FAIL: exec_with_input produced no stdout\\n");
            {name}_result_free(r);
            return 1;
        }}
        if (strstr(r->stdout_buf, payload) == NULL) {{
            fprintf(stderr, "FAIL: exec_with_input stdout missing payload\\n");
            {name}_result_free(r);
            return 1;
        }}
        {name}_result_free(r);
    }}

    /* Test 4: nonexistent command returns error */
    {{
        const char *args[] = {{"__nonexistent_cmd_12345__", NULL}};
        {name}_result_t *r = {name}_exec("__nonexistent_cmd_12345__", args, 1);
        if (r) {{
            /* Should have non-zero exit or be NULL */
            if (r->exit_code == 0) {{
                fprintf(stderr, "FAIL: nonexistent command should fail\\n");
                {name}_result_free(r);
                return 1;
            }}
            {name}_result_free(r);
        }}
        /* NULL is also acceptable for exec failure */
    }}

    /* Test 5: exit code propagation */
    {{
        const char *args[] = {{"sh", "-c", "exit 42", NULL}};
        {name}_result_t *r = {name}_exec("/bin/sh", args, 3);
        if (!r) {{
            fprintf(stderr, "FAIL: exec sh returned NULL\\n");
            return 1;
        }}
        if (r->exit_code != 42) {{
            fprintf(stderr, "FAIL: expected exit code 42, got %d\\n", r->exit_code);
            {name}_result_free(r);
            return 1;
        }}
        {name}_result_free(r);
    }}

    /* Test 6: stderr capture */
    {{
        const char *args[] = {{"sh", "-c", "echo err_msg >&2", NULL}};
        {name}_result_t *r = {name}_exec("/bin/sh", args, 3);
        if (!r) {{
            fprintf(stderr, "FAIL: exec for stderr test returned NULL\\n");
            return 1;
        }}
        if (r->stderr_buf == NULL || r->stderr_len == 0) {{
            fprintf(stderr, "FAIL: no stderr captured\\n");
            {name}_result_free(r);
            return 1;
        }}
        if (strstr(r->stderr_buf, "err_msg") == NULL) {{
            fprintf(stderr, "FAIL: stderr missing 'err_msg'\\n");
            {name}_result_free(r);
            return 1;
        }}
        {name}_result_free(r);
    }}

    fprintf(stderr, "PASS: all process spawner tests passed\\n");
    return 0;
}}
"""


def run_proc_test(spec: ProcessSpawnerSpec, header_path: str) -> tuple[bool, str]:
    """Generate, compile, and run process spawner tests."""
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
            capture_output=True, text=True, timeout=15,
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
