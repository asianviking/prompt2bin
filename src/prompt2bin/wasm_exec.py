"""
Wasm exec — fast exploration mode.

Reuses the WasmSpec pipeline (intent → spec → WAT → compile → test)
but skips Z3/structural verification and wasm-opt/AOT for speed.

Two modes:
- One-shot: exec_once(intent) → ExecResult
- REPL: ExecSession + exec_turn() for iterative development
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .wasm_spec import WasmSpec, TypedValue, WasmType
from .wasm_test import TestResult, run_wasm_tests


@dataclass
class ExecResult:
    """Result of a single exec pipeline run."""
    spec: WasmSpec
    wat_code: str
    wasm_path: str
    test_results: list[TestResult]
    success: bool
    error: str | None = None


@dataclass
class ExecSession:
    """REPL session state."""
    history: list[tuple[str, str, list[TestResult]]] = field(default_factory=list)
    current_spec: WasmSpec | None = None
    current_wat: str | None = None
    temp_dir: Path = field(default_factory=lambda: Path(tempfile.mkdtemp(prefix="p2b-exec-")))

    @property
    def wasm_path(self) -> str | None:
        """Path to current compiled .wasm, if any."""
        if self.current_spec:
            return str(self.temp_dir / f"{self.current_spec.name}.wasm")
        return None

    def cleanup(self) -> None:
        """Remove temp files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)


def exec_once(intent: str) -> ExecResult:
    """
    One-shot: intent → spec → WAT → compile → run tests → result.

    No Z3. No structural verification. No wasm-opt. No AOT.
    Generate → compile → run.
    """
    from .wasm_intent import intent_to_wasm_spec
    from .wasm_codegen import generate_wat
    from .wasm_validate import compile_wat_to_wasm

    # Phase 1: Intent → WasmSpec (lightweight — sigs + tests)
    try:
        spec = intent_to_wasm_spec(intent)
    except Exception as e:
        return ExecResult(
            spec=None, wat_code="", wasm_path="",
            test_results=[], success=False,
            error=f"Intent translation failed: {e}",
        )

    # Phase 2: WasmSpec → WAT (with retry loop)
    wat_code = generate_wat(spec, max_retries=3)
    if not wat_code:
        return ExecResult(
            spec=spec, wat_code="", wasm_path="",
            test_results=[], success=False,
            error="WAT generation failed after retries",
        )

    # Phase 3: WAT → .wasm (wat2wasm only, no optimize/AOT)
    tmp = tempfile.mkdtemp(prefix="p2b-exec-")
    wasm_path = os.path.join(tmp, f"{spec.name}.wasm")
    ok, err = compile_wat_to_wasm(wat_code, wasm_path)
    if not ok:
        return ExecResult(
            spec=spec, wat_code=wat_code, wasm_path="",
            test_results=[], success=False,
            error=f"Compilation failed: {err}",
        )

    # Phase 4: Run tests
    test_results = run_wasm_tests(spec, wasm_path)
    all_passed = all(r.passed for r in test_results)

    return ExecResult(
        spec=spec,
        wat_code=wat_code,
        wasm_path=wasm_path,
        test_results=test_results,
        success=all_passed,
    )


def exec_turn(session: ExecSession, user_input: str) -> ExecResult:
    """
    Single REPL turn: takes session context + new input, returns result.

    The LLM sees the full conversation context so it can modify
    the existing module rather than starting fresh.
    """
    from .wasm_intent import intent_to_wasm_spec
    from .wasm_codegen import generate_wat
    from .wasm_validate import compile_wat_to_wasm

    # Build context-enriched intent for the LLM
    if session.history:
        context_intent = _build_context_intent(session, user_input)
    else:
        context_intent = user_input

    # Phase 1: Intent → WasmSpec
    try:
        spec = intent_to_wasm_spec(context_intent)
    except Exception as e:
        return ExecResult(
            spec=None, wat_code="", wasm_path="",
            test_results=[], success=False,
            error=f"Intent translation failed: {e}",
        )

    # Phase 2: WasmSpec → WAT
    wat_code = generate_wat(spec, max_retries=3)
    if not wat_code:
        return ExecResult(
            spec=spec, wat_code="", wasm_path="",
            test_results=[], success=False,
            error="WAT generation failed after retries",
        )

    # Phase 3: Compile
    wasm_path = str(session.temp_dir / f"{spec.name}.wasm")
    ok, err = compile_wat_to_wasm(wat_code, wasm_path)
    if not ok:
        return ExecResult(
            spec=spec, wat_code=wat_code, wasm_path="",
            test_results=[], success=False,
            error=f"Compilation failed: {err}",
        )

    # Phase 4: Run tests
    test_results = run_wasm_tests(spec, wasm_path)
    all_passed = all(r.passed for r in test_results)

    # Update session state
    session.current_spec = spec
    session.current_wat = wat_code
    session.history.append((user_input, wat_code, test_results))

    return ExecResult(
        spec=spec,
        wat_code=wat_code,
        wasm_path=wasm_path,
        test_results=test_results,
        success=all_passed,
    )


def exec_invoke(session: ExecSession, func: str, args: list[str]) -> str:
    """Direct invocation: run a specific function with given args."""
    from . import toolchain

    if not session.wasm_path or not os.path.exists(session.wasm_path):
        return "Error: no compiled module in session"

    tc = toolchain.detect()
    if not tc.wasmtime:
        return "Error: wasmtime not found"

    cmd = [tc.wasmtime, "run", "--invoke", func, session.wasm_path] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return "Error: invocation timed out"

    if result.returncode != 0:
        stderr = result.stderr.strip()
        lines = [l for l in stderr.split("\n") if not l.startswith("warning:")]
        err = "\n".join(lines).strip()
        if err:
            return f"Error: {err}"

    return result.stdout.strip()


def save_session_record(
    path: str,
    intent: str,
    result: ExecResult,
    turn: int,
    prior_wat: str | None,
) -> None:
    """Append a JSONL record for training data capture."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "intent": intent,
        "spec": _spec_to_dict(result.spec) if result.spec else None,
        "wat": result.wat_code,
        "wasm_size": os.path.getsize(result.wasm_path) if result.wasm_path and os.path.exists(result.wasm_path) else 0,
        "tests": [
            {
                "name": t.test_name,
                "passed": t.passed,
                "message": t.message,
                "expected": t.expected,
                "actual": t.actual,
            }
            for t in result.test_results
        ],
        "success": result.success,
        "turn": turn,
        "prior_wat": prior_wat,
    }
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def _build_context_intent(session: ExecSession, new_input: str) -> str:
    """Build a context-enriched intent from session history + new input."""
    parts = []
    parts.append("Previous context (modify the existing module):\n")
    for i, (intent, wat, _results) in enumerate(session.history):
        parts.append(f"Turn {i + 1} intent: {intent}")
    if session.current_wat:
        parts.append(f"\nCurrent WAT source:\n```wat\n{session.current_wat}\n```")
    parts.append(f"\nNew request: {new_input}")
    parts.append("\nGenerate a complete updated module that incorporates the new request.")
    return "\n".join(parts)


def _spec_to_dict(spec: WasmSpec) -> dict:
    """Minimal serialization of WasmSpec for JSONL output."""
    return {
        "name": spec.name,
        "description": spec.description,
        "functions": [
            {
                "name": f.name,
                "params": [{"name": p.name, "type": p.type.value} for p in f.params],
                "results": [r.value for r in f.results],
            }
            for f in spec.functions
        ],
        "tests": [
            {
                "function": t.function,
                "args": [{"type": a.type.value, "value": a.value} for a in t.args],
                "expected": {"type": t.expected.type.value, "value": t.expected.value} if t.expected else None,
                "description": t.description,
            }
            for t in spec.tests
        ],
    }
