"""
LLM backend abstraction.

Supports Claude CLI, Codex CLI, and their respective APIs.
Backend priority (auto-detected, or override with P2B_BACKEND):
  1. Claude CLI   (subscription, no API key)
  2. Codex CLI    (subscription, no API key)
  3. Anthropic API (ANTHROPIC_API_KEY)
  4. OpenAI API    (OPENAI_API_KEY)

Two operations:
  - structured(): prompt + system prompt + JSON schema → parsed dict
  - generate():   prompt + system prompt → raw text (C code)
"""

import json
import os
import shutil
import subprocess
import tempfile

BACKENDS = ("claude", "codex", "anthropic-api", "openai-api")


def _detect_backend() -> str:
    """Detect which LLM backend to use."""
    env = os.environ.get("P2B_BACKEND", "").lower()
    if env in BACKENDS:
        return env

    # CLIs first (no API key friction)
    if shutil.which("claude"):
        return "claude"
    if shutil.which("codex"):
        return "codex"

    # API keys as fallback
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic-api"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai-api"

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
            if event.get("type") == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "message" and item.get("role") == "assistant":
                    for content in item.get("content", []):
                        if content.get("type") == "text":
                            last_message = content.get("text", "")
        except json.JSONDecodeError:
            continue

    if not last_message:
        last_message = result.stdout.strip()

    try:
        return json.loads(last_message)
    except json.JSONDecodeError:
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


# ── Anthropic API backend ──

def _get_anthropic_client():
    """Get Anthropic client, importing lazily."""
    try:
        import anthropic
        return anthropic.Anthropic()
    except ImportError:
        print("  ✗ anthropic package not installed. Run: pip install anthropic")
        return None


def _anthropic_api_structured(prompt: str, system_prompt: str, json_schema: str, timeout: int = 60) -> dict | None:
    """Call Anthropic API with tool_use for structured output."""
    client = _get_anthropic_client()
    if not client:
        return None

    schema = json.loads(json_schema)
    model = os.environ.get("P2B_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

    try:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=system_prompt,
            tools=[{
                "name": "spec",
                "description": "Output the structured spec",
                "input_schema": schema,
            }],
            tool_choice={"type": "tool", "name": "spec"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "spec":
                return block.input
    except Exception as e:
        print(f"  ⚠ Anthropic API failed: {e}")

    return None


def _anthropic_api_generate(prompt: str, system_prompt: str, timeout: int = 90) -> str | None:
    """Call Anthropic API for raw text generation."""
    client = _get_anthropic_client()
    if not client:
        return None

    model = os.environ.get("P2B_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        print(f"  ⚠ Anthropic API failed: {e}")
        return None


# ── OpenAI API backend ──

def _get_openai_client():
    """Get OpenAI client, importing lazily."""
    try:
        import openai
        return openai.OpenAI()
    except ImportError:
        print("  ✗ openai package not installed. Run: pip install openai")
        return None


def _openai_api_structured(prompt: str, system_prompt: str, json_schema: str, timeout: int = 60) -> dict | None:
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
        print(f"  ⚠ OpenAI API failed: {e}")
        return None


def _openai_api_generate(prompt: str, system_prompt: str, timeout: int = 90) -> str | None:
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
        print(f"  ⚠ OpenAI API failed: {e}")
        return None


# ── Public API ──

_DISPATCH = {
    "claude":        (_claude_structured,        _claude_generate),
    "codex":         (_codex_structured,         _codex_generate),
    "anthropic-api": (_anthropic_api_structured, _anthropic_api_generate),
    "openai-api":    (_openai_api_structured,    _openai_api_generate),
}


def structured(prompt: str, system_prompt: str, json_schema: str, timeout: int = 60) -> dict | None:
    """
    Translate a prompt into structured JSON using the configured LLM backend.

    Returns a parsed dict matching the schema, or None on failure.
    """
    backend = _detect_backend()
    fn = _DISPATCH[backend][0]
    return fn(prompt, system_prompt, json_schema, timeout)


def generate(prompt: str, system_prompt: str, timeout: int = 90) -> str | None:
    """
    Generate raw text (C code) using the configured LLM backend.

    Returns the generated text, or None on failure.
    """
    backend = _detect_backend()
    fn = _DISPATCH[backend][1]
    return fn(prompt, system_prompt, timeout)
