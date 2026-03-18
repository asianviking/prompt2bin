"""
Test harness generator.

Generates a small C program that exercises the allocator and checks
the verified properties at runtime. This catches bugs that GCC's
type checker misses — wrong pointer math, incorrect alignment, etc.
"""

import os
import shutil
import subprocess
import tempfile
from .spec import ArenaSpec


def generate_test_c(spec: ArenaSpec, header_path: str) -> str:
    """Generate a C test program for the given arena spec."""
    name = spec.name
    align = spec.alignment.min_align
    max_alloc = spec.memory.effective_max_alloc
    capacity = spec.memory.usable_capacity

    # Pick a test alloc size that's reasonable
    test_size = min(64, max_alloc)
    # How many allocs should fit in a SINGLE page (create() allocates one page)
    per_page_capacity = spec.memory.page_size - spec.memory.header_size
    worst_case = test_size + (align - 1)
    n_allocs = min(per_page_capacity // worst_case, 100) if worst_case > 0 else 0

    checks = []

    # Basic lifecycle
    checks.append(f"""
    /* Test 1: create and destroy */
    {name}_t *arena = {name}_create();
    if (!arena) {{ fprintf(stderr, "FAIL: create returned NULL\\n"); return 1; }}
    """)

    # Allocation returns non-NULL
    checks.append(f"""
    /* Test 2: basic allocation */
    void *p1 = {name}_alloc(arena, {test_size});
    if (!p1) {{ fprintf(stderr, "FAIL: alloc({test_size}) returned NULL\\n"); return 1; }}
    """)

    # Alignment check
    checks.append(f"""
    /* Test 3: alignment */
    if ((uintptr_t)p1 % {align} != 0) {{
        fprintf(stderr, "FAIL: pointer %p not {align}-byte aligned\\n", p1);
        return 1;
    }}
    """)

    # Multiple allocations, all aligned
    if n_allocs > 1:
        checks.append(f"""
    /* Test 4: multiple allocations all aligned */
    {name}_reset(arena);
    for (int i = 0; i < {n_allocs}; i++) {{
        void *p = {name}_alloc(arena, {test_size});
        if (!p) {{ fprintf(stderr, "FAIL: alloc %d returned NULL\\n", i); return 1; }}
        if ((uintptr_t)p % {align} != 0) {{
            fprintf(stderr, "FAIL: alloc %d not aligned: %p\\n", i, p);
            return 1;
        }}
    }}
    """)

    # Zero-on-alloc check
    if spec.safety.zero_on_alloc:
        checks.append(f"""
    /* Test 5: zero on alloc */
    {name}_reset(arena);
    /* Write garbage first */
    void *dirty = {name}_alloc(arena, {test_size});
    if (dirty) memset(dirty, 0xAA, {test_size});
    {name}_reset(arena);
    /* Allocate again — should be zeroed */
    void *clean = {name}_alloc(arena, {test_size});
    if (clean) {{
        unsigned char *bytes = (unsigned char *)clean;
        for (int i = 0; i < {test_size}; i++) {{
            if (bytes[i] != 0) {{
                fprintf(stderr, "FAIL: byte %d not zeroed (0x%02x)\\n", i, bytes[i]);
                return 1;
            }}
        }}
    }}
    """)

    # Bounds: alloc beyond max should return NULL
    over_size = min(max_alloc + 1, per_page_capacity + 1)
    checks.append(f"""
    /* Test 6: over-capacity returns NULL */
    {name}_reset(arena);
    void *big = {name}_alloc(arena, {over_size});
    if (big != NULL) {{
        fprintf(stderr, "FAIL: alloc({over_size}) should return NULL\\n");
        return 1;
    }}
    """)

    # Zero-size returns NULL
    checks.append(f"""
    /* Test 7: zero size returns NULL */
    void *z = {name}_alloc(arena, 0);
    if (z != NULL) {{
        fprintf(stderr, "FAIL: alloc(0) should return NULL\\n");
        return 1;
    }}
    """)

    # Reset works
    checks.append(f"""
    /* Test 8: reset allows reuse */
    {name}_reset(arena);
    void *after_reset = {name}_alloc(arena, {test_size});
    if (!after_reset) {{
        fprintf(stderr, "FAIL: alloc after reset returned NULL\\n");
        return 1;
    }}
    """)

    # Cleanup
    checks.append(f"""
    /* Cleanup */
    {name}_destroy(arena);
    fprintf(stderr, "PASS: all tests passed\\n");
    return 0;
    """)

    all_checks = "\n".join(checks)

    includes = "#include <stdio.h>\n#include <stdint.h>\n"
    if spec.safety.zero_on_alloc or spec.safety.zero_on_reset:
        includes += "#include <string.h>\n"

    return f"""\
{includes}#include "{os.path.abspath(header_path)}"

int main(void) {{
{all_checks}
}}
"""


def run_test_harness(spec: ArenaSpec, header_path: str) -> tuple[bool, str]:
    """
    Generate, compile, and run a test harness for the given allocator.
    Returns (passed, output_message).
    """
    gcc = shutil.which("gcc")
    if not gcc:
        return True, "gcc not found, skipping runtime tests"

    test_c = generate_test_c(spec, header_path)

    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
        f.write(test_c)
        test_path = f.name

    bin_path = test_path.replace(".c", "")

    try:
        # Compile
        result = subprocess.run(
            [gcc, "-O2", "-o", bin_path, test_path, "-Wno-unused-function"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return False, f"Test compilation failed:\n{result.stderr}"

        # Run
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
