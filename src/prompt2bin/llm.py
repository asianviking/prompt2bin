"""
LLM backend abstraction.

Supports Claude CLI and OpenAI API. The backend is selected by:
  1. P2B_BACKEND env var ("claude" or "openai")
  2. Auto-detection: tries Claude CLI first, then OpenAI API key

Two operations:
  - structured(): prompt + system prompt + JSON schema → parsed dict
  - generate():   prompt + system prompt → raw text (C code)
"""

import json
import os
import shutil
import subprocess


def _detect_backend() -> str:
    """Detect which LLM backend to use."""
    env = os.environ.get("P2B_BACKEND", "").lower()
    if env in ("claude", "openai"):
        return env

    if shutil.which("claude"):
        return "claude"

    if os.environ.get("OPENAI_API_KEY"):
        return "openai"

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


# ── OpenAI backend ──

def _get_openai_client():
    """Get OpenAI client, importing lazily."""
    try:
        import openai
        return openai.OpenAI()
    except ImportError:
        print("  ✗ openai package not installed. Run: pip install openai")
        return None
    except Exception as e:
        print(f"  ✗ OpenAI client error: {e}")
        return None


def _openai_structured(prompt: str, system_prompt: str, json_schema: str, timeout: int = 60) -> dict | None:
    """Call OpenAI API with structured output (response_format)."""
    client = _get_openai_client()
    if not client:
        return None

    schema = json.loads(json_schema)
    model = os.environ.get("P2B_OPENAI_MODEL", "gpt-4o-mini")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "spec",
                    "strict": True,
                    "schema": schema,
                },
            },
            timeout=timeout,
        )
        content = response.choices[0].message.content
        return json.loads(content)
    except Exception as e:
        print(f"  ⚠ OpenAI structured output failed: {e}")
        return None


def _openai_generate(prompt: str, system_prompt: str, timeout: int = 90) -> str | None:
    """Call OpenAI API for raw text generation."""
    client = _get_openai_client()
    if not client:
        return None

    model = os.environ.get("P2B_OPENAI_MODEL", "gpt-4o-mini")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            timeout=timeout,
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"  ⚠ OpenAI generation failed: {e}")
        return None


# ── Public API ──

def structured(prompt: str, system_prompt: str, json_schema: str, timeout: int = 60) -> dict | None:
    """
    Translate a prompt into structured JSON using the configured LLM backend.

    Returns a parsed dict matching the schema, or None on failure.
    """
    backend = _detect_backend()
    if backend == "openai":
        return _openai_structured(prompt, system_prompt, json_schema, timeout)
    return _claude_structured(prompt, system_prompt, json_schema, timeout)


def generate(prompt: str, system_prompt: str, timeout: int = 90) -> str | None:
    """
    Generate raw text (C code) using the configured LLM backend.

    Returns the generated text, or None on failure.
    """
    backend = _detect_backend()
    if backend == "openai":
        return _openai_generate(prompt, system_prompt, timeout)
    return _claude_generate(prompt, system_prompt, timeout)
