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

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prompt2bin.project import ModelConfig

BACKENDS = ("claude", "codex", "anthropic-api", "openai-api")

# Module-level model config, set by configure() from build.toml [model] section.
_model_config: ModelConfig | None = None

# Module-level debug flag, set by set_debug() from CLI --debug flag.
_debug: bool = False


def set_debug(enabled: bool) -> None:
    """Enable or disable debug output for all LLM operations."""
    global _debug
    _debug = enabled


def is_debug() -> bool:
    """Return whether debug mode is active."""
    return _debug


def _dbg(msg: str) -> None:
    """Print a debug message if debug mode is active."""
    if _debug:
        print(f"[DEBUG] {msg}", flush=True)


def configure(model_config: ModelConfig | None) -> None:
    """Set the active model config (from build.toml). Call before any LLM ops."""
    global _model_config
    _model_config = model_config


def _detect_backend() -> str:
    """Detect which LLM backend to use."""
    # build.toml [model] backend takes priority
    if _model_config and _model_config.backend:
        if _model_config.backend in BACKENDS:
            return _model_config.backend

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


def _get_model(env_var: str, default: str) -> str:
    """Resolve model name: build.toml > env var > default."""
    if _model_config and _model_config.name:
        return _model_config.name
    return os.environ.get(env_var, default)


def _get_temperature() -> float | None:
    """Get temperature from build.toml [model] if set."""
    if _model_config and _model_config.temperature is not None:
        return _model_config.temperature
    return None


def get_backend() -> str:
    """Return the active backend name."""
    return _detect_backend()


def get_model_info() -> dict[str, str]:
    """Return a dict of active model settings for display."""
    info: dict[str, str] = {"backend": _detect_backend()}

    backend = info["backend"]
    if backend == "claude":
        info["model"] = _claude_model_arg()
    elif backend == "anthropic-api":
        info["model"] = _get_model("P2B_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    elif backend == "openai-api":
        info["model"] = _get_model("P2B_OPENAI_MODEL", "gpt-4o-mini")
    elif backend == "codex":
        info["model"] = _model_config.name if _model_config and _model_config.name else "codex (default)"

    if _model_config:
        if _model_config.reasoning:
            info["reasoning"] = _model_config.reasoning
        if _model_config.temperature is not None:
            info["temperature"] = str(_model_config.temperature)

    return info


# ── Claude CLI backend ──

def _claude_model_arg() -> str:
    """Resolve Claude CLI --model arg: build.toml > default."""
    if _model_config and _model_config.name:
        return _model_config.name
    return "haiku"


def _claude_structured(prompt: str, system_prompt: str, json_schema: str, timeout: int = 60) -> dict | None:
    """Call Claude CLI with --json-schema for structured output."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        _dbg("Claude CLI not found")
        return None

    extra = _model_config.extra_args if _model_config and _model_config.extra_args else []
    cmd = [
        claude_bin, "-p",
        "--output-format", "json",
        "--system-prompt", system_prompt,
        "--json-schema", json_schema,
        "--tools", "",
        "--model", _claude_model_arg(),
        *extra,
        prompt,
    ]
    _dbg(f"Command: {' '.join(cmd[:8])}... <prompt>")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _dbg(f"Claude CLI timed out after {timeout}s")
        return None
    except FileNotFoundError:
        _dbg("Claude CLI binary not found at execution time")
        return None

    _dbg(f"Exit code: {result.returncode}")
    if result.stderr:
        _dbg(f"Stderr: {result.stderr[:500]}")

    if result.returncode != 0:
        _dbg(f"Stdout on failure: {result.stdout[:500]}")
        return None

    _dbg(f"Raw stdout ({len(result.stdout)} chars): {result.stdout[:500]}")

    try:
        response = json.loads(result.stdout)
        params = response.get("structured_output")
        if params and isinstance(params, dict):
            return params
        raw = response.get("result", "")
        if isinstance(raw, str):
            return json.loads(raw)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        _dbg(f"JSON parse error: {e}")

    _dbg("Failed to extract structured output from response")
    return None


def _claude_generate(prompt: str, system_prompt: str, timeout: int | None = None) -> str | None:
    """Call Claude CLI for raw text generation."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        _dbg("Claude CLI not found")
        return None

    extra = _model_config.extra_args if _model_config and _model_config.extra_args else []
    cmd = [
        claude_bin, "-p",
        "--system-prompt", system_prompt,
        "--tools", "",
        "--model", _claude_model_arg(),
        *extra,
        prompt,
    ]
    _dbg(f"Command: {' '.join(cmd[:8])}... <prompt>")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _dbg(f"Claude CLI timed out after {timeout}s")
        return None
    except FileNotFoundError:
        _dbg("Claude CLI binary not found at execution time")
        return None

    _dbg(f"Exit code: {result.returncode}")
    if result.stderr:
        _dbg(f"Stderr: {result.stderr[:500]}")

    if result.returncode != 0:
        _dbg(f"Stdout on failure: {result.stdout[:500]}")
        return None

    _dbg(f"Response ({len(result.stdout)} chars): {result.stdout[:200]}...")
    return result.stdout


# ── Codex CLI backend ──

def _codex_model_args() -> list[str]:
    """Build codex CLI args from build.toml [model] settings."""
    args: list[str] = []
    if _model_config:
        if _model_config.name:
            args += ["--model", _model_config.name]
        # reasoning_effort not supported by codex CLI — use extra_args if needed
        if _model_config.extra_args:
            args += list(_model_config.extra_args)
    return args


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
        _dbg("Codex CLI not found")
        return None

    full_prompt = (
        f"{prompt}\n\n"
        f"Respond with ONLY valid JSON matching this schema:\n{json_schema}"
    )
    _dbg(f"Command: codex exec --json -c model_instructions_file=... <prompt>")

    try:
        result = subprocess.run(
            [
                codex_bin, "exec",
                "--json",
                *_codex_model_args(),
                "-c", f"model_instructions_file={instructions_path}",
                full_prompt,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _dbg(f"Codex CLI timed out after {timeout}s")
        return None
    except FileNotFoundError:
        _dbg("Codex CLI binary not found at execution time")
        return None
    finally:
        os.unlink(instructions_path)

    _dbg(f"Exit code: {result.returncode}")
    if result.stderr:
        _dbg(f"Stderr: {result.stderr[:500]}")

    if result.returncode != 0:
        _dbg(f"Stdout on failure: {result.stdout[:500]}")
        return None

    _dbg(f"Raw stdout ({len(result.stdout)} chars): {result.stdout[:500]}")

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
        _dbg(f"Failed to parse last_message as JSON: {last_message[:200]}")
        for line in last_message.split("\n"):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return None


def _codex_generate(prompt: str, system_prompt: str, timeout: int | None = None) -> str | None:
    """Call Codex CLI for raw text generation."""
    codex_bin, instructions_path = _codex_with_instructions(system_prompt)
    if not codex_bin:
        _dbg("Codex CLI not found")
        return None

    _dbg(f"Command: codex exec -c model_instructions_file=... <prompt>")

    try:
        result = subprocess.run(
            [
                codex_bin, "exec",
                *_codex_model_args(),
                "-c", f"model_instructions_file={instructions_path}",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _dbg(f"Codex CLI timed out after {timeout}s")
        return None
    except FileNotFoundError:
        _dbg("Codex CLI binary not found at execution time")
        return None
    finally:
        os.unlink(instructions_path)

    _dbg(f"Exit code: {result.returncode}")
    if result.stderr:
        _dbg(f"Stderr: {result.stderr[:500]}")

    if result.returncode != 0:
        _dbg(f"Stdout on failure: {result.stdout[:500]}")
        return None

    _dbg(f"Response ({len(result.stdout)} chars): {result.stdout[:200]}...")
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
    model = _get_model("P2B_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

    kwargs: dict = dict(
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
    temp = _get_temperature()
    if temp is not None:
        kwargs["temperature"] = temp

    _dbg(f"Anthropic API structured: model={model}, max_tokens=1024")

    try:
        response = client.messages.create(**kwargs)
        _dbg(f"Response: stop_reason={response.stop_reason}, usage={response.usage}")
        for block in response.content:
            _dbg(f"Content block: type={block.type}")
            if block.type == "tool_use" and block.name == "spec":
                return block.input
    except Exception as e:
        _dbg(f"Anthropic API exception: {type(e).__name__}: {e}")
        print(f"  ⚠ Anthropic API failed: {e}")

    _dbg("No tool_use block found in response")
    return None


def _anthropic_api_generate(prompt: str, system_prompt: str, timeout: int | None = None) -> str | None:
    """Call Anthropic API for raw text generation."""
    client = _get_anthropic_client()
    if not client:
        return None

    model = _get_model("P2B_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

    kwargs: dict = dict(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
    )
    temp = _get_temperature()
    if temp is not None:
        kwargs["temperature"] = temp

    # Extended thinking for reasoning=high on supported models
    if _model_config and _model_config.reasoning == "high":
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": 2048}
        kwargs.pop("temperature", None)  # thinking doesn't support temperature

    _dbg(f"Anthropic API generate: model={model}, max_tokens=4096")

    try:
        response = client.messages.create(**kwargs)
        _dbg(f"Response: stop_reason={response.stop_reason}, usage={response.usage}")
        text = response.content[0].text
        _dbg(f"Generated text ({len(text)} chars): {text[:200]}...")
        return text
    except Exception as e:
        _dbg(f"Anthropic API exception: {type(e).__name__}: {e}")
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
    model = _get_model("P2B_OPENAI_MODEL", "gpt-4o-mini")

    kwargs: dict = dict(
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
    temp = _get_temperature()
    if temp is not None:
        kwargs["temperature"] = temp

    # OpenAI reasoning models (o1, o3) use reasoning_effort
    if _model_config and _model_config.reasoning:
        kwargs["reasoning_effort"] = _model_config.reasoning

    _dbg(f"OpenAI API structured: model={model}")

    try:
        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        _dbg(f"Response ({len(content)} chars): {content[:300]}")
        return json.loads(content)
    except Exception as e:
        _dbg(f"OpenAI API exception: {type(e).__name__}: {e}")
        print(f"  ⚠ OpenAI API failed: {e}")
        return None


def _openai_api_generate(prompt: str, system_prompt: str, timeout: int | None = None) -> str | None:
    """Call OpenAI API for raw text generation."""
    client = _get_openai_client()
    if not client:
        return None

    model = _get_model("P2B_OPENAI_MODEL", "gpt-4o-mini")

    kwargs: dict = dict(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    )
    if timeout is not None:
        kwargs["timeout"] = timeout
    temp = _get_temperature()
    if temp is not None:
        kwargs["temperature"] = temp

    if _model_config and _model_config.reasoning:
        kwargs["reasoning_effort"] = _model_config.reasoning

    _dbg(f"OpenAI API generate: model={model}")

    try:
        response = client.chat.completions.create(**kwargs)
        text = response.choices[0].message.content
        _dbg(f"Generated text ({len(text)} chars): {text[:200]}...")
        return text
    except Exception as e:
        _dbg(f"OpenAI API exception: {type(e).__name__}: {e}")
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
    _dbg(f"structured() via {backend}")
    _dbg(f"System prompt ({len(system_prompt)} chars): {system_prompt[:150]}...")
    _dbg(f"Prompt ({len(prompt)} chars): {prompt[:150]}...")
    fn = _DISPATCH[backend][0]
    result = fn(prompt, system_prompt, json_schema, timeout)
    if result is None:
        _dbg("structured() returned None")
    return result


def generate(prompt: str, system_prompt: str, timeout: int | None = None) -> str | None:
    """
    Generate raw text (C code) using the configured LLM backend.

    No timeout by default — LLM code generation can legitimately take
    minutes on complex prompts.  Users can Ctrl+C to cancel.

    Returns the generated text, or None on failure.
    """
    backend = _detect_backend()
    _dbg(f"generate() via {backend}")
    _dbg(f"System prompt ({len(system_prompt)} chars): {system_prompt[:150]}...")
    _dbg(f"Prompt ({len(prompt)} chars): {prompt[:150]}...")
    fn = _DISPATCH[backend][1]
    result = fn(prompt, system_prompt, timeout)
    if result is None:
        _dbg("generate() returned None")
    return result
