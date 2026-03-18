"""
Intent → ProcessSpawnerSpec translator.

Same pattern as other domains: LLM primary, regex fallback.
"""

import json
import re
from .spec import ProcessSpawnerSpec, CaptureMode


# ── LLM translator ──

JSON_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Process spawner name (lowercase, valid C identifier)",
        },
        "max_args": {
            "type": "integer",
            "description": "Maximum number of command-line arguments. Default: 64",
        },
        "max_env": {
            "type": "integer",
            "description": "Maximum environment variables to pass. Default: 64",
        },
        "max_arg_len": {
            "type": "integer",
            "description": "Max length of a single argument string in bytes. Default: 4096",
        },
        "capture_stdout": {
            "type": "string",
            "enum": ["none", "buffer", "pipe"],
            "description": "How to capture stdout: none, buffer (fixed-size), pipe (streaming)",
        },
        "capture_stderr": {
            "type": "string",
            "enum": ["none", "buffer", "pipe"],
            "description": "How to capture stderr: none, buffer (fixed-size), pipe (streaming)",
        },
        "stdout_buf_size": {
            "type": "integer",
            "description": "Stdout capture buffer size in bytes. Default: 65536",
        },
        "stderr_buf_size": {
            "type": "integer",
            "description": "Stderr capture buffer size in bytes. Default: 65536",
        },
        "timeout_ms": {
            "type": "integer",
            "description": "Child process timeout in milliseconds. Default: 30000",
        },
        "pipe_stdin": {
            "type": "boolean",
            "description": "Allow writing to child's stdin via pipe. Default: true",
        },
    },
    "required": ["name", "max_args", "capture_stdout", "capture_stderr", "timeout_ms", "pipe_stdin"],
})

SYSTEM_PROMPT = (
    "You translate process spawning / subprocess descriptions into formal JSON specs. "
    "You MUST fill ALL required fields — no omissions. "
    "Rules: "
    "name must be lowercase valid C identifier. "
    "If user mentions 'CLI', 'command', 'subprocess', 'exec': this is a process spawner. "
    "If user says 'capture output': capture_stdout=buffer, capture_stderr=buffer. "
    "If user says 'stream': capture_stdout=pipe, capture_stderr=pipe. "
    "If user says 'long running' or 'timeout': increase timeout_ms. "
    "If user mentions 'stdin' or 'pipe input' or 'feed': pipe_stdin=true. "
    "Default: capture both stdout/stderr to buffer, 30s timeout, pipe_stdin=true."
)


def intent_to_proc_llm(intent: str) -> ProcessSpawnerSpec | None:
    """Use LLM backend to translate intent → ProcessSpawnerSpec."""
    from . import llm
    params = llm.structured(intent, SYSTEM_PROMPT, JSON_SCHEMA, timeout=60)
    if params and isinstance(params, dict):
        try:
            return _params_to_spec(params)
        except (KeyError, TypeError):
            pass
    return None


def _params_to_spec(params: dict) -> ProcessSpawnerSpec:
    """Convert JSON params to ProcessSpawnerSpec."""
    capture_map = {
        "none": CaptureMode.NONE,
        "buffer": CaptureMode.BUFFER,
        "pipe": CaptureMode.PIPE,
    }
    return ProcessSpawnerSpec(
        name=params.get("name", "proc"),
        max_args=params.get("max_args", 64),
        max_env=params.get("max_env", 64),
        max_arg_len=params.get("max_arg_len", 4096),
        capture_stdout=capture_map.get(params.get("capture_stdout", "buffer"), CaptureMode.BUFFER),
        capture_stderr=capture_map.get(params.get("capture_stderr", "buffer"), CaptureMode.BUFFER),
        stdout_buf_size=params.get("stdout_buf_size", 65536),
        stderr_buf_size=params.get("stderr_buf_size", 65536),
        timeout_ms=params.get("timeout_ms", 30000),
        pipe_stdin=params.get("pipe_stdin", True),
    )


# ── Regex fallback ──

def intent_to_proc_regex(intent: str) -> ProcessSpawnerSpec:
    """Regex-based fallback for process spawner intent."""
    text = intent.lower()

    # Capture mode
    if any(w in text for w in ["stream", "realtime", "real-time", "live"]):
        capture_stdout = CaptureMode.PIPE
        capture_stderr = CaptureMode.PIPE
    elif any(w in text for w in ["discard", "ignore output", "no output"]):
        capture_stdout = CaptureMode.NONE
        capture_stderr = CaptureMode.NONE
    else:
        capture_stdout = CaptureMode.BUFFER
        capture_stderr = CaptureMode.BUFFER

    # Buffer sizes
    buf_match = re.search(r"(\d+)\s*(?:KB|kb)\s*(?:buffer|capture)", text)
    if buf_match:
        buf_size = int(buf_match.group(1)) * 1024
    elif any(w in text for w in ["large output", "big output", "verbose"]):
        buf_size = 262144  # 256KB
    else:
        buf_size = 65536  # 64KB

    # Timeout
    timeout_match = re.search(r"(\d+)\s*(?:s|sec|seconds?)\s*timeout", text)
    if timeout_match:
        timeout_ms = int(timeout_match.group(1)) * 1000
    elif any(w in text for w in ["long running", "slow", "long timeout"]):
        timeout_ms = 120000
    elif any(w in text for w in ["fast", "quick"]):
        timeout_ms = 5000
    else:
        timeout_ms = 30000

    # Stdin
    pipe_stdin = not any(w in text for w in ["no stdin", "no input", "read only"])

    # Name
    name_match = re.search(r"(?:called|named)\s+['\"]?(\w+)['\"]?", intent, re.IGNORECASE)
    name = name_match.group(1) if name_match else "proc"

    return ProcessSpawnerSpec(
        name=name,
        capture_stdout=capture_stdout,
        capture_stderr=capture_stderr,
        stdout_buf_size=buf_size,
        stderr_buf_size=buf_size,
        timeout_ms=timeout_ms,
        pipe_stdin=pipe_stdin,
    )


# ── Public API ──

def intent_to_proc(intent: str) -> ProcessSpawnerSpec:
    """Translate natural language → ProcessSpawnerSpec. LLM first, regex fallback."""
    from . import llm
    spec = intent_to_proc_llm(intent)
    if spec is not None:
        print(f"  (translated by {llm.get_backend()})")
        return spec

    print("  (translated by regex fallback)")
    return intent_to_proc_regex(intent)
