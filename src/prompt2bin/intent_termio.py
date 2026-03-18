"""
Intent → TermIOSpec translator.

Same pattern as other domains: LLM primary, regex fallback.
"""

import json
import re
from .spec import TermIOSpec, EditMode


# ── LLM translator ──

JSON_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Terminal I/O name (lowercase, valid C identifier)",
        },
        "max_line_len": {
            "type": "integer",
            "description": "Maximum input line length in bytes. Default: 4096",
        },
        "history_size": {
            "type": "integer",
            "description": "Number of history entries to keep. Default: 256",
        },
        "prompt_max_len": {
            "type": "integer",
            "description": "Maximum prompt string length in bytes. Default: 256",
        },
        "edit_mode": {
            "type": "string",
            "enum": ["basic", "readline"],
            "description": "basic: simple fgets-style input. readline: line editing with cursor.",
        },
    },
    "required": ["name", "max_line_len", "history_size", "prompt_max_len", "edit_mode"],
})

SYSTEM_PROMPT = (
    "You translate terminal input / line editor / readline descriptions into formal JSON specs. "
    "You MUST fill ALL required fields — no omissions. "
    "Rules: "
    "name must be lowercase valid C identifier. "
    "If user says 'simple', 'basic', 'fgets': edit_mode=basic. "
    "If user says 'readline', 'line editing', 'cursor': edit_mode=readline. "
    "If user says 'CLI' or 'REPL' or 'interactive': history_size >= 256. "
    "If user says 'large input' or 'long lines': max_line_len=8192 or more. "
    "Default: basic mode, 4096 line length, 256 history entries."
)


def intent_to_termio_llm(intent: str) -> TermIOSpec | None:
    """Use LLM backend to translate intent → TermIOSpec."""
    from . import llm
    params = llm.structured(intent, SYSTEM_PROMPT, JSON_SCHEMA, timeout=60)
    if params and isinstance(params, dict):
        try:
            return _params_to_spec(params)
        except (KeyError, TypeError):
            pass
    return None


def _params_to_spec(params: dict) -> TermIOSpec:
    """Convert JSON params to TermIOSpec."""
    mode_map = {
        "basic": EditMode.BASIC,
        "readline": EditMode.READLINE,
    }
    return TermIOSpec(
        name=params.get("name", "termio"),
        max_line_len=params.get("max_line_len", 4096),
        history_size=params.get("history_size", 256),
        prompt_max_len=params.get("prompt_max_len", 256),
        edit_mode=mode_map.get(params.get("edit_mode", "basic"), EditMode.BASIC),
    )


# ── Regex fallback ──

def intent_to_termio_regex(intent: str) -> TermIOSpec:
    """Regex-based fallback for terminal I/O intent."""
    text = intent.lower()

    # Edit mode
    if any(w in text for w in ["readline", "line edit", "cursor", "arrow key"]):
        edit_mode = EditMode.READLINE
    else:
        edit_mode = EditMode.BASIC

    # Line length
    len_match = re.search(r"(\d+)\s*(?:byte|char|character)s?\s*(?:line|input|buffer)", text)
    if len_match:
        max_line_len = int(len_match.group(1))
    elif any(w in text for w in ["long line", "large input", "big input"]):
        max_line_len = 16384
    else:
        max_line_len = 4096

    # History
    hist_match = re.search(r"(\d+)\s*(?:history|entries|lines?)\s*(?:history)?", text)
    if hist_match:
        history_size = int(hist_match.group(1))
    elif any(w in text for w in ["no history", "no recall"]):
        history_size = 0
    elif any(w in text for w in ["large history", "long history"]):
        history_size = 1024
    else:
        history_size = 256

    # Name
    name_match = re.search(r"(?:called|named)\s+['\"]?(\w+)['\"]?", intent, re.IGNORECASE)
    name = name_match.group(1) if name_match else "termio"

    return TermIOSpec(
        name=name,
        max_line_len=max_line_len,
        history_size=history_size,
        edit_mode=edit_mode,
    )


# ── Public API ──

def intent_to_termio(intent: str) -> TermIOSpec:
    """Translate natural language → TermIOSpec. LLM first, regex fallback."""
    from . import llm
    spec = intent_to_termio_llm(intent)
    if spec is not None:
        print(f"  (translated by {llm.get_backend()})")
        return spec

    print("  (translated by regex fallback)")
    return intent_to_termio_regex(intent)
