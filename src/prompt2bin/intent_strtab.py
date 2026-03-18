"""
Intent → StringTableSpec translator.

Same pattern as other domains: LLM primary, regex fallback.
"""

import json
import re
from .spec import StringTableSpec, HashFunction


# ── LLM translator ──

JSON_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "String table name (lowercase, valid C identifier)",
        },
        "max_strings": {
            "type": "integer",
            "description": "Maximum number of unique strings to store. Default: 1024",
        },
        "max_total_bytes": {
            "type": "integer",
            "description": "Total string storage in bytes. Default: 65536",
        },
        "max_string_len": {
            "type": "integer",
            "description": "Maximum length of a single string in bytes. Default: 4096",
        },
        "hash_bits": {
            "type": "integer",
            "description": "Hash table size as power of 2. E.g., 10 means 1024 buckets. Default: 10",
        },
        "hash_func": {
            "type": "string",
            "enum": ["fnv1a", "djbx33a"],
            "description": "Hash function: fnv1a (default, good distribution) or djbx33a (simpler)",
        },
    },
    "required": ["name", "max_strings", "max_total_bytes", "hash_bits", "hash_func"],
})

SYSTEM_PROMPT = (
    "You translate string storage / string table / intern pool descriptions into formal JSON specs. "
    "You MUST fill ALL required fields — no omissions. "
    "Rules: "
    "name must be lowercase valid C identifier. "
    "hash_bits determines table size as 2^hash_bits. Should be >= log2(max_strings). "
    "If user says 'prompt', 'response', 'context': max_string_len=4096, larger storage. "
    "If user says 'token', 'keyword', 'command': max_string_len=256, smaller storage. "
    "If user says 'large' or 'many strings': increase max_strings and hash_bits. "
    "Default: 1024 strings, 64KB storage, FNV-1a hash, 10-bit table."
)


def intent_to_strtab_llm(intent: str) -> StringTableSpec | None:
    """Use LLM backend to translate intent → StringTableSpec."""
    from . import llm
    params = llm.structured(intent, SYSTEM_PROMPT, JSON_SCHEMA, timeout=60)
    if params and isinstance(params, dict):
        try:
            return _params_to_spec(params)
        except (KeyError, TypeError):
            pass
    return None


def _params_to_spec(params: dict) -> StringTableSpec:
    """Convert JSON params to StringTableSpec."""
    hash_map = {
        "fnv1a": HashFunction.FNV1A,
        "djbx33a": HashFunction.DJBX33A,
    }
    return StringTableSpec(
        name=params.get("name", "strtab"),
        max_strings=params.get("max_strings", 1024),
        max_total_bytes=params.get("max_total_bytes", 65536),
        max_string_len=params.get("max_string_len", 4096),
        hash_bits=params.get("hash_bits", 10),
        hash_func=hash_map.get(params.get("hash_func", "fnv1a"), HashFunction.FNV1A),
    )


# ── Regex fallback ──

def intent_to_strtab_regex(intent: str) -> StringTableSpec:
    """Regex-based fallback for string table intent."""
    text = intent.lower()

    # Max strings
    str_match = re.search(r"(\d+)\s*(?:strings?|entries|keys|tokens)", text)
    if str_match:
        max_strings = int(str_match.group(1))
    elif any(w in text for w in ["large", "big", "many"]):
        max_strings = 4096
    elif any(w in text for w in ["small", "tiny", "few"]):
        max_strings = 128
    else:
        max_strings = 1024

    # Hash bits — at least log2(max_strings)
    hash_bits = 10
    n = max_strings
    while (1 << hash_bits) < n:
        hash_bits += 1

    # Storage
    size_match = re.search(r"(\d+)\s*(?:KB|kb)", text)
    if size_match:
        max_total_bytes = int(size_match.group(1)) * 1024
    elif any(w in text for w in ["prompt", "response", "context", "large"]):
        max_total_bytes = 262144  # 256KB
    else:
        max_total_bytes = 65536  # 64KB

    # Max string length
    if any(w in text for w in ["prompt", "response", "long"]):
        max_string_len = 8192
    elif any(w in text for w in ["token", "keyword", "command", "short"]):
        max_string_len = 256
    else:
        max_string_len = 4096

    # Name
    name_match = re.search(r"(?:called|named)\s+['\"]?(\w+)['\"]?", intent, re.IGNORECASE)
    name = name_match.group(1) if name_match else "strtab"

    return StringTableSpec(
        name=name,
        max_strings=max_strings,
        max_total_bytes=max_total_bytes,
        max_string_len=max_string_len,
        hash_bits=hash_bits,
    )


# ── Public API ──

def intent_to_strtab(intent: str) -> StringTableSpec:
    """Translate natural language → StringTableSpec. LLM first, regex fallback."""
    from . import llm
    spec = intent_to_strtab_llm(intent)
    if spec is not None:
        print(f"  (translated by {llm.get_backend()})")
        return spec

    print("  (translated by regex fallback)")
    return intent_to_strtab_regex(intent)
