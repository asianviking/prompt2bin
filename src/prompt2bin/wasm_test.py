"""
Wasm runtime test execution via wasmtime.

Runs test cases from WasmSpec against a compiled .wasm binary.
Two test sources:
1. Spec-declared TestCases (LLM-generated during intent)
2. Property-derived boundary tests (from postconditions)
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass

from . import toolchain
from .wasm_spec import WasmSpec, WasmType, TestCase, TypedValue


@dataclass
class TestResult:
    passed: bool
    test_name: str
    message: str
    expected: str = ""
    actual: str = ""

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        s = f"  [{status}] {self.test_name}: {self.message}"
        if not self.passed and self.expected and self.actual:
            s += f"\n         Expected: {self.expected}, Got: {self.actual}"
        return s


def run_wasm_tests(spec: WasmSpec, wasm_path: str) -> list[TestResult]:
    """
    Run all test cases against a .wasm binary.

    Tests are run sequentially (order matters for stateful modules).
    """
    tc = toolchain.detect()
    if not tc.wasmtime:
        return [TestResult(False, "wasmtime", "wasmtime not found")]

    if not os.path.exists(wasm_path):
        return [TestResult(False, "wasm_file", f"File not found: {wasm_path}")]

    results: list[TestResult] = []

    # Run spec-declared test cases
    for i, test in enumerate(spec.tests):
        desc = test.description or f"{test.function}({', '.join(str(a.value) for a in test.args)})"
        name = f"test_{i}_{test.function}"
        result = _invoke_test(tc.wasmtime, wasm_path, test, name, desc)
        results.append(result)

    return results


def _invoke_test(
    wasmtime: str,
    wasm_path: str,
    test: TestCase,
    name: str,
    description: str,
) -> TestResult:
    """Invoke a single test case via wasmtime."""
    args = [str(_typed_value_to_arg(a)) for a in test.args]
    cmd = [wasmtime, "run"]
    if wasm_path.endswith(".cwasm"):
        cmd.append("--allow-precompiled")
    cmd.extend(["--invoke", test.function, wasm_path] + args)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return TestResult(False, name, f"{description}: timed out")
    except FileNotFoundError:
        return TestResult(False, name, "wasmtime not found at execution time")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        # Filter out experimental warnings
        stderr_lines = [
            l for l in stderr.split("\n")
            if not l.startswith("warning:")
        ]
        err = "\n".join(stderr_lines).strip()
        if err:
            return TestResult(False, name, f"{description}: runtime error: {err}")

    # Parse output
    stdout = result.stdout.strip()
    # wasmtime also prints warnings to stderr, actual result to stdout
    if not stdout and not test.expected:
        # Void function — no expected output
        return TestResult(True, name, f"{description}: OK (void)")

    expected_str = str(_format_expected(test.expected))
    actual_str = stdout.strip()

    if _values_match(actual_str, test.expected):
        return TestResult(True, name, f"{description}: {actual_str}")
    else:
        return TestResult(
            False, name, description,
            expected=expected_str, actual=actual_str,
        )


def _typed_value_to_arg(v: TypedValue) -> int | float:
    """Convert a TypedValue to a command-line argument."""
    if v.type in (WasmType.I32, WasmType.I64):
        return int(v.value)
    return v.value


def _format_expected(v: TypedValue) -> str:
    """Format expected value for display."""
    if v.type in (WasmType.I32, WasmType.I64):
        return str(int(v.value))
    return str(v.value)


def _values_match(actual: str, expected: TypedValue) -> bool:
    """Check if actual output matches expected value."""
    try:
        if expected.type in (WasmType.I32, WasmType.I64):
            return int(actual) == int(expected.value)
        elif expected.type == WasmType.F32:
            return abs(float(actual) - float(expected.value)) < 1e-6
        elif expected.type == WasmType.F64:
            return abs(float(actual) - float(expected.value)) < 1e-12
    except (ValueError, TypeError):
        return False
    return actual.strip() == str(expected.value)
