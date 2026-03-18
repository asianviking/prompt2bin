"""
Intent → RingBufferSpec translator.

Same pattern as arena: Claude CLI primary, regex fallback.
"""

import json
import re
import shutil
import subprocess
from .spec import RingBufferSpec, RingBufferMode, ElementType


# ── Claude CLI translator ──

JSON_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Ring buffer name (lowercase, valid C identifier)",
        },
        "mode": {
            "type": "string",
            "enum": ["spsc", "mpsc", "spmc", "mpmc"],
            "description": "spsc=single producer/consumer (fastest), mpmc=multi producer/consumer",
        },
        "element_size": {
            "type": "integer",
            "description": "Size of each element in bytes",
        },
        "capacity": {
            "type": "integer",
            "description": "Number of element slots. MUST be power of two.",
        },
        "cache_line_pad": {
            "type": "boolean",
            "description": "Pad head/tail to separate cache lines (prevents false sharing). Default: true",
        },
        "blocking": {
            "type": "boolean",
            "description": "Block on full/empty (true) vs return error code (false). Default: false",
        },
        "no_data_loss": {
            "type": "boolean",
            "description": "Reject writes when full instead of overwriting. Default: true",
        },
    },
    "required": ["name", "mode", "element_size", "capacity", "cache_line_pad", "blocking", "no_data_loss"],
})

SYSTEM_PROMPT = (
    "You translate ring buffer / circular queue descriptions into formal JSON specs. "
    "You MUST fill ALL fields — no omissions. "
    "Rules: "
    "capacity MUST be a power of two (64, 128, 256, 512, 1024, etc). "
    "name must be lowercase valid C identifier. "
    "If user says 'audio'/'sample': element_size=4 (float), high capacity (4096+). "
    "If user says 'log'/'message'/'event': element_size=64 or 128, moderate capacity. "
    "If user says 'byte'/'stream': element_size=1, high capacity. "
    "If only one producer and one consumer mentioned: mode=spsc. "
    "If user says 'fast'/'low latency'/'real-time': cache_line_pad=true, blocking=false. "
    "If user says 'multi-producer' or 'concurrent writers': mode=mpsc or mpmc. "
    "Default to spsc, non-blocking, no_data_loss=true if unspecified."
)


def intent_to_ringbuf_claude(intent: str) -> RingBufferSpec | None:
    """Use Claude CLI to translate intent → RingBufferSpec."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return None

    try:
        result = subprocess.run(
            [
                claude_bin, "-p",
                "--output-format", "json",
                "--system-prompt", SYSTEM_PROMPT,
                "--json-schema", JSON_SCHEMA,
                "--tools", "",
                "--model", "haiku",
                intent,
            ],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None

    try:
        response = json.loads(result.stdout)
        params = response.get("structured_output")
        if params and isinstance(params, dict):
            return _params_to_spec(params)
        raw = response.get("result", "")
        if isinstance(raw, str):
            return _params_to_spec(json.loads(raw))
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    return None


def _params_to_spec(params: dict) -> RingBufferSpec:
    """Convert JSON params to RingBufferSpec."""
    mode_map = {
        "spsc": RingBufferMode.SPSC,
        "mpsc": RingBufferMode.MPSC,
        "spmc": RingBufferMode.SPMC,
        "mpmc": RingBufferMode.MPMC,
    }
    return RingBufferSpec(
        name=params.get("name", "ringbuf"),
        mode=mode_map.get(params.get("mode", "spsc"), RingBufferMode.SPSC),
        element_size=params.get("element_size", 8),
        capacity=params.get("capacity", 1024),
        cache_line_pad=params.get("cache_line_pad", True),
        blocking=params.get("blocking", False),
        no_data_loss=params.get("no_data_loss", True),
    )


# ── Regex fallback ──

def intent_to_ringbuf_regex(intent: str) -> RingBufferSpec:
    """Regex-based fallback for ring buffer intent."""
    text = intent.lower()

    # Mode
    if any(w in text for w in ["mpmc", "multi-producer multi-consumer"]):
        mode = RingBufferMode.MPMC
    elif any(w in text for w in ["mpsc", "multi-producer", "multiple producers", "concurrent writer"]):
        mode = RingBufferMode.MPSC
    elif any(w in text for w in ["spmc", "multi-consumer", "multiple consumers"]):
        mode = RingBufferMode.SPMC
    else:
        mode = RingBufferMode.SPSC

    # Element size
    elem_match = re.search(r"(\d+)\s*-?\s*byte\s+element", text)
    if elem_match:
        element_size = int(elem_match.group(1))
    elif any(w in text for w in ["float", "sample", "audio"]):
        element_size = 4
    elif any(w in text for w in ["pointer", "ptr", "int64", "uint64"]):
        element_size = 8
    elif any(w in text for w in ["log", "message", "event"]):
        element_size = 64
    else:
        element_size = 8

    # Capacity
    cap_match = re.search(r"(\d+)\s*(?:slots?|elements?|entries|items)", text)
    if cap_match:
        capacity = int(cap_match.group(1))
        # Round up to power of two
        p = 1
        while p < capacity:
            p *= 2
        capacity = p
    elif any(w in text for w in ["large", "big", "high throughput"]):
        capacity = 8192
    elif any(w in text for w in ["small", "tiny"]):
        capacity = 64
    else:
        capacity = 1024

    # Blocking
    blocking = any(w in text for w in ["blocking", "block on full", "wait"])

    # Name
    name_match = re.search(r"(?:called|named)\s+['\"]?(\w+)['\"]?", intent, re.IGNORECASE)
    name = name_match.group(1) if name_match else "ringbuf"

    return RingBufferSpec(
        name=name,
        mode=mode,
        element_size=element_size,
        capacity=capacity,
        cache_line_pad=mode != RingBufferMode.SPSC or "cache" in text or "pad" in text,
        blocking=blocking,
        no_data_loss="overwrite" not in text,
    )


# ── Public API ──

def intent_to_ringbuf(intent: str) -> RingBufferSpec:
    """Translate natural language → RingBufferSpec. Claude first, regex fallback."""
    spec = intent_to_ringbuf_claude(intent)
    if spec is not None:
        print("  (translated by Claude via CLI)")
        return spec

    print("  (translated by regex fallback)")
    return intent_to_ringbuf_regex(intent)
