"""
LLM-powered C code generator for process spawners.

Same pattern as ring buffer: spec → LLM → GCC check → retry.
"""

import os
import re
import shutil
import subprocess
import tempfile
from . import llm
from .spec import ProcessSpawnerSpec, CaptureMode


SYSTEM_PROMPT = "You generate C header-only libraries. Output ONLY C code. No markdown, no explanation."


def _spec_to_prompt(spec: ProcessSpawnerSpec, verified_properties: list[str] | None = None) -> str:
    props = verified_properties or []
    props_text = "\n".join(f"  - {p}" for p in props) if props else "  (none provided)"

    capture_desc = {
        CaptureMode.NONE: "discard",
        CaptureMode.BUFFER: "capture to fixed buffer",
        CaptureMode.PIPE: "stream via pipe fd",
    }

    return f"""\
Generate a complete C header-only process spawner implementation using POSIX fork/exec:

Name: {spec.name}
Max args: {spec.max_args}
Max env vars: {spec.max_env}
Max arg length: {spec.max_arg_len} bytes
Stdout: {capture_desc[spec.capture_stdout]} ({spec.stdout_buf_size} bytes buffer)
Stderr: {capture_desc[spec.capture_stderr]} ({spec.stderr_buf_size} bytes buffer)
Timeout: {spec.timeout_ms}ms
Pipe stdin: {spec.pipe_stdin}
No zombies: {spec.no_zombie} (always waitpid)
Timeout enforced: {spec.timeout_enforced} (SIGKILL after timeout)

Verified properties (your code MUST maintain these):
{props_text}

Required types and functions:

typedef struct {{
    int exit_code;
    int timed_out;
    char *stdout_buf;       // captured stdout (caller must NOT free — owned by result)
    size_t stdout_len;
    char *stderr_buf;       // captured stderr
    size_t stderr_len;
}} {spec.name}_result_t;

- {spec.name}_result_t *{spec.name}_exec(const char *cmd, const char **args, int nargs)
  Fork/exec cmd with args. Capture output. Wait with timeout. Return result.

- {spec.name}_result_t *{spec.name}_exec_with_input(
      const char *cmd, const char **args, int nargs,
      const void *stdin_buf, size_t stdin_len)
  Like exec(), but feeds stdin_buf to the child's stdin via a pipe, then closes stdin.
  If stdin_buf is NULL or stdin_len==0, just closes stdin immediately.
  If Pipe stdin is false, this function should return NULL (ENOTSUP) rather than hanging.

- void {spec.name}_result_free({spec.name}_result_t *r)
  Free result and its buffers.
- int {spec.name}_exec_simple(const char *cmd, char *out_buf, size_t out_size)
  Simplified: exec cmd, capture stdout into caller buffer, return exit code.

Implementation notes:
- Use fork() + execvp() (NOT system())
- Create pipes before fork for stdout/stderr capture
- If Pipe stdin is enabled, create a pipe for stdin, dup2 it to STDIN_FILENO in the child,
  and in the parent write stdin_buf (handling partial writes) then close the pipe.
- In parent: read from pipes into malloc'd buffers, capped at buffer size
- Use waitpid() with WNOHANG + usleep loop for timeout, then SIGKILL
- All memory allocated via malloc, freed in result_free
- Include guard: {spec.name.upper()}_H
- Must compile with -Wall -Werror on Linux
- Include: unistd.h, sys/wait.h, signal.h, string.h, stdlib.h, stdio.h, errno.h
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


def generate_proc_llm(
    spec: ProcessSpawnerSpec,
    verified_properties: list[str] | None = None,
    max_retries: int = 1,
) -> str | None:
    """Generate process spawner C code via LLM backend. Returns None on failure."""
    prompt = _spec_to_prompt(spec, verified_properties)

    debug = llm.is_debug()

    for attempt in range(1, max_retries + 2):
        if debug:
            print(f"[DEBUG] proc codegen prompt ({len(prompt)} chars, attempt {attempt})", flush=True)
        raw = llm.generate(prompt, SYSTEM_PROMPT)
        if not raw:
            print(f"  LLM returned no output")
            return None

        if debug:
            print(f"[DEBUG] Raw LLM response ({len(raw)} chars):\n{raw[:500]}", flush=True)

        c_code = _extract_c_code(raw)
        if not c_code or len(c_code) < 50:
            print(f"  LLM returned insufficient code ({len(c_code) if c_code else 0} chars)")
            continue

        ok, err = _gcc_check(c_code)
        if ok:
            return c_code

        if attempt <= max_retries:
            print(f"  GCC rejected attempt {attempt}, retrying...")
            if debug:
                print(f"[DEBUG] GCC errors:\n{err}", flush=True)
            prompt = (
                f"Your previous C code had compilation errors:\n\n{err}\n\n"
                f"Fix the errors and regenerate. {_spec_to_prompt(spec, verified_properties)}"
            )
        else:
            print(f"  GCC rejected all attempts: {err[:200]}")

    return None
