"""
Test harness for ring buffer implementations.

Generates, compiles, and runs C tests that verify:
- Create/destroy lifecycle
- Push/pop correctness
- FIFO ordering
- Full buffer rejection
- Empty buffer rejection
- Wraparound behavior
"""

import os
import shutil
import subprocess
import tempfile
from .spec import RingBufferSpec


def generate_test_c(spec: RingBufferSpec, header_path: str) -> str:
    """Generate a C test program for the ring buffer."""
    name = spec.name
    cap = spec.capacity
    elem_size = spec.element_size

    # Use uint8_t arrays as elements for simplicity
    return f"""\
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "{os.path.abspath(header_path)}"

int main(void) {{
    /* Test 1: create */
    {name}_t *rb = {name}_create();
    if (!rb) {{ fprintf(stderr, "FAIL: create returned NULL\\n"); return 1; }}

    /* Test 2: empty pop fails */
    uint8_t buf[{elem_size}];
    if ({name}_pop(rb, buf) == 0) {{
        fprintf(stderr, "FAIL: pop from empty should fail\\n");
        return 1;
    }}

    /* Test 3: push then pop */
    uint8_t data[{elem_size}];
    memset(data, 0x42, {elem_size});
    if ({name}_push(rb, data) != 0) {{
        fprintf(stderr, "FAIL: push to empty buffer failed\\n");
        return 1;
    }}

    uint8_t out[{elem_size}];
    memset(out, 0, {elem_size});
    if ({name}_pop(rb, out) != 0) {{
        fprintf(stderr, "FAIL: pop after push failed\\n");
        return 1;
    }}
    if (memcmp(data, out, {elem_size}) != 0) {{
        fprintf(stderr, "FAIL: popped data doesn't match pushed data\\n");
        return 1;
    }}

    /* Test 4: FIFO ordering */
    for (int i = 0; i < 10 && i < {cap}; i++) {{
        uint8_t d[{elem_size}];
        memset(d, (uint8_t)i, {elem_size});
        if ({name}_push(rb, d) != 0) {{
            fprintf(stderr, "FAIL: push %d failed\\n", i);
            return 1;
        }}
    }}
    for (int i = 0; i < 10 && i < {cap}; i++) {{
        uint8_t d[{elem_size}];
        if ({name}_pop(rb, d) != 0) {{
            fprintf(stderr, "FAIL: pop %d failed\\n", i);
            return 1;
        }}
        if (d[0] != (uint8_t)i) {{
            fprintf(stderr, "FAIL: FIFO order broken at %d (got %d)\\n", i, d[0]);
            return 1;
        }}
    }}

    /* Test 5: fill to capacity */
    for (int i = 0; i < {cap}; i++) {{
        uint8_t d[{elem_size}];
        memset(d, (uint8_t)i, {elem_size});
        if ({name}_push(rb, d) != 0) {{
            fprintf(stderr, "FAIL: push %d of {cap} failed\\n", i);
            return 1;
        }}
    }}

    /* Test 6: push to full buffer fails */
    {{
        uint8_t d[{elem_size}];
        memset(d, 0xFF, {elem_size});
        if ({name}_push(rb, d) == 0) {{
            fprintf(stderr, "FAIL: push to full buffer should fail\\n");
            return 1;
        }}
    }}

    /* Test 7: drain and verify */
    for (int i = 0; i < {cap}; i++) {{
        uint8_t d[{elem_size}];
        if ({name}_pop(rb, d) != 0) {{
            fprintf(stderr, "FAIL: pop %d during drain failed\\n", i);
            return 1;
        }}
    }}

    /* Test 8: wraparound — push/pop past capacity boundary */
    for (int round = 0; round < 3; round++) {{
        for (int i = 0; i < {cap}; i++) {{
            uint8_t d[{elem_size}];
            memset(d, (uint8_t)(round * 10 + i), {elem_size});
            if ({name}_push(rb, d) != 0) {{
                fprintf(stderr, "FAIL: wraparound push round=%d i=%d\\n", round, i);
                return 1;
            }}
        }}
        for (int i = 0; i < {cap}; i++) {{
            uint8_t d[{elem_size}];
            if ({name}_pop(rb, d) != 0) {{
                fprintf(stderr, "FAIL: wraparound pop round=%d i=%d\\n", round, i);
                return 1;
            }}
            if (d[0] != (uint8_t)(round * 10 + i)) {{
                fprintf(stderr, "FAIL: wraparound data mismatch round=%d i=%d\\n", round, i);
                return 1;
            }}
        }}
    }}

    /* Cleanup */
    {name}_destroy(rb);
    fprintf(stderr, "PASS: all ring buffer tests passed\\n");
    return 0;
}}
"""


def run_ringbuf_test(spec: RingBufferSpec, header_path: str) -> tuple[bool, str]:
    """Generate, compile, and run ring buffer tests."""
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
            [gcc, "-O2", "-o", bin_path, test_path, "-Wno-unused-function", "-lpthread"],
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
