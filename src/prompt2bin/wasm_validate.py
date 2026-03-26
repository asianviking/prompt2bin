"""
WAT/WASM binary validation pipeline.

1. wat2wasm: compile WAT text → .wasm binary
2. wasm-validate: validate the binary
3. Size budget check (optional)
"""

from __future__ import annotations

import os
import subprocess
import tempfile

from . import toolchain


def validate_wat(wat_code: str) -> tuple[bool, bytes | None, str | None]:
    """
    Validate WAT code by compiling with wat2wasm.

    Returns (success, wasm_bytes, error_message).
    On success, wasm_bytes contains the compiled binary.
    """
    tc = toolchain.detect()
    if not tc.wat2wasm:
        return False, None, "wat2wasm not found (install wabt)"

    with tempfile.NamedTemporaryFile(suffix=".wat", mode="w", delete=False) as wat_f:
        wat_f.write(wat_code)
        wat_path = wat_f.name

    wasm_path = wat_path.replace(".wat", ".wasm")

    try:
        result = subprocess.run(
            [tc.wat2wasm, wat_path, "-o", wasm_path],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return False, None, result.stderr.strip()

        with open(wasm_path, "rb") as f:
            wasm_bytes = f.read()

        return True, wasm_bytes, None
    except subprocess.TimeoutExpired:
        return False, None, "wat2wasm timed out"
    except FileNotFoundError:
        return False, None, "wat2wasm binary not found at execution time"
    finally:
        _safe_remove(wat_path)
        _safe_remove(wasm_path)


def validate_wasm(wasm_bytes: bytes) -> tuple[bool, str | None]:
    """
    Validate a .wasm binary with wasm-validate.

    Returns (success, error_message).
    """
    tc = toolchain.detect()
    if not tc.wasm_validate:
        return True, None  # Can't validate, assume OK

    with tempfile.NamedTemporaryFile(suffix=".wasm", delete=False) as f:
        f.write(wasm_bytes)
        wasm_path = f.name

    try:
        result = subprocess.run(
            [tc.wasm_validate, wasm_path],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return True, None
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "wasm-validate timed out"
    finally:
        _safe_remove(wasm_path)


def check_size_budget(wasm_bytes: bytes, budget: int) -> tuple[bool, str | None]:
    """
    Check if wasm binary fits within size budget.

    Returns (within_budget, feedback_message).
    feedback_message is set when over budget (for retry prompts).
    """
    if budget <= 0:
        return True, None  # No budget set

    actual = len(wasm_bytes)
    if actual <= budget:
        return True, None

    return False, (
        f"Generated {actual} bytes, budget is {budget} bytes. "
        f"Simplify the implementation to reduce binary size."
    )


def compile_wat_to_wasm(wat_code: str, output_path: str) -> tuple[bool, str | None]:
    """
    Compile WAT to .wasm file at the given output path.

    Returns (success, error_message).
    """
    tc = toolchain.detect()
    if not tc.wat2wasm:
        return False, "wat2wasm not found (install wabt)"

    with tempfile.NamedTemporaryFile(suffix=".wat", mode="w", delete=False) as f:
        f.write(wat_code)
        wat_path = f.name

    try:
        result = subprocess.run(
            [tc.wat2wasm, wat_path, "-o", output_path],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return False, result.stderr.strip()
        return True, None
    except subprocess.TimeoutExpired:
        return False, "wat2wasm timed out"
    finally:
        _safe_remove(wat_path)


def optimize_wasm(wasm_path: str) -> tuple[bool, str | None]:
    """
    Run wasm-opt on a .wasm file (in-place). Optional — skipped if binaryen not installed.

    Returns (success, error_message).
    """
    tc = toolchain.detect()
    if not tc.wasm_opt:
        return True, None  # Optional, skip silently

    opt_path = wasm_path + ".opt"
    try:
        result = subprocess.run(
            [tc.wasm_opt, "-O2", wasm_path, "-o", opt_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return False, result.stderr.strip()

        # Replace original with optimized
        os.replace(opt_path, wasm_path)
        return True, None
    except subprocess.TimeoutExpired:
        _safe_remove(opt_path)
        return False, "wasm-opt timed out"
    except FileNotFoundError:
        return True, None  # wasm-opt disappeared between check and exec


def _safe_remove(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
