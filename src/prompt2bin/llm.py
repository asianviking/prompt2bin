"""
LLM backend abstraction.

Supports Claude CLI and Codex CLI. The backend is selected by:
  1. P2B_BACKEND env var ("claude" or "codex")
  2. Auto-detection: tries Claude CLI first, then Codex CLI

Both are subscription-based CLIs — no API keys needed.

Two operations:
  - structured(): prompt + system prompt + JSON schema → parsed dict
  - generate():   prompt + system prompt → raw text (C code)
"""

import json
import os
import shutil
import subprocess
import tempfile


def _detect_backend() -> str:
    """Detect which LLM backend to use."""
    env = os.environ.get("P2B_BACKEND", "").lower()
    if env in ("claude", "codex"):
        return env

    if shutil.which("claude"):
        return "claude"

    if shutil.which("codex"):
        return "codex"

    return "claude"  # will fail with a helpful message later


def get_backend() -> str:
    """Return the active backend name."""
    return _detect_backend()


# ── Claude CLI backend ──

def _claude_structured(prompt: str, system_prompt: str, json_schema: str, timeout: int = 60) -> dict | None:
    """Call Claude CLI with --json-schema for structured output."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return None

    try:
        result = subprocess.run(
            [
                claude_bin, "-p",
                "--output-format", "json",
                "--system-prompt", system_prompt,
                "--json-schema", json_schema,
                "--tools", "",
                "--model", "haiku",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None

    try:
        response = json.loads(result.stdout)
        params = response.get("structured_output")
        if params and isinstance(params, dict):
            return params
        raw = response.get("result", "")
        if isinstance(raw, str):
            return json.loads(raw)
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    return None


def _claude_generate(prompt: str, system_prompt: str, timeout: int = 90) -> str | None:
    """Call Claude CLI for raw text generation."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return None

    try:
        result = subprocess.run(
            [
                claude_bin, "-p",
                "--system-prompt", system_prompt,
                "--tools", "",
                "--model", "haiku",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None

    return result.stdout


# ── Codex CLI backend ──

def _codex_with_instructions(system_prompt: str) -> tuple[str | None, str | None]:
    """
    Write system prompt to a temp file for Codex CLI's -c flag.
    Returns (codex_bin, instructions_path) or (None, None).
    """
    codex_bin = shutil.which("codex")
    if not codex_bin:
        return None, None

    f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
    f.write(system_prompt)
    f.close()
    return codex_bin, f.name


def _codex_structured(prompt: str, system_prompt: str, json_schema: str, timeout: int = 60) -> dict | None:
    """Call Codex CLI with --json for structured output."""
    codex_bin, instructions_path = _codex_with_instructions(system_prompt)
    if not codex_bin:
        return None

    # Embed the schema in the prompt so Codex knows the expected format
    full_prompt = (
        f"{prompt}\n\n"
        f"Respond with ONLY valid JSON matching this schema:\n{json_schema}"
    )

    try:
        result = subprocess.run(
            [
                codex_bin, "exec",
                "--json",
                "-c", f"model_instructions_file={instructions_path}",
                full_prompt,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    finally:
        os.unlink(instructions_path)

    if result.returncode != 0:
        return None

    # Codex --json outputs JSONL events. Find the last agent message.
    last_message = None
    for line in result.stdout.strip().split("\n"):
        try:
            event = json.loads(line)
            # Look for the final message content
            if event.get("type") == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "message" and item.get("role") == "assistant":
                    for content in item.get("content", []):
                        if content.get("type") == "text":
                            last_message = content.get("text", "")
        except json.JSONDecodeError:
            continue

    if not last_message:
        # Fallback: try parsing stdout directly (non-json mode output)
        last_message = result.stdout.strip()

    try:
        return json.loads(last_message)
    except json.JSONDecodeError:
        # Try to extract JSON from the message
        for line in last_message.split("\n"):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return None


def _codex_generate(prompt: str, system_prompt: str, timeout: int = 90) -> str | None:
    """Call Codex CLI for raw text generation."""
    codex_bin, instructions_path = _codex_with_instructions(system_prompt)
    if not codex_bin:
        return None

    try:
        result = subprocess.run(
            [
                codex_bin, "exec",
                "-c", f"model_instructions_file={instructions_path}",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    finally:
        os.unlink(instructions_path)

    if result.returncode != 0:
        return None

    return result.stdout


# ── Public API ──

def structured(prompt: str, system_prompt: str, json_schema: str, timeout: int = 60) -> dict | None:
    """
    Translate a prompt into structured JSON using the configured LLM backend.

    Returns a parsed dict matching the schema, or None on failure.
    """
    backend = _detect_backend()
    if backend == "codex":
        return _codex_structured(prompt, system_prompt, json_schema, timeout)
    return _claude_structured(prompt, system_prompt, json_schema, timeout)


def generate(prompt: str, system_prompt: str, timeout: int = 90) -> str | None:
    """
    Generate raw text (C code) using the configured LLM backend.

    Returns the generated text, or None on failure.
    """
    backend = _detect_backend()
    if backend == "codex":
        return _codex_generate(prompt, system_prompt, timeout)
    return _claude_generate(prompt, system_prompt, timeout)
