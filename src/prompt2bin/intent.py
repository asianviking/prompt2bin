"""
Intent → Spec translator.

Two backends:
1. Claude CLI — structured JSON output via `claude -p --json-schema`, no API key needed
2. Regex fallback — fast, no dependency, handles simple/structured intent

The key insight: the translator doesn't need to be perfect.
The verifier catches bad specs. So we can be aggressive in
extraction and rely on verification as the safety net.
"""

import json
import os
import re
import shutil
import subprocess
from .spec import (
    ArenaSpec, AllocStrategy, GrowthPolicy, ThreadSafety,
    MemoryBounds, AlignmentSpec, SafetyInvariants, PerformanceConstraints,
)

# ── Claude CLI translator ──

JSON_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Allocator name (lowercase, valid C identifier)",
        },
        "strategy": {
            "type": "string",
            "enum": ["bump", "pool", "freelist"],
            "description": "bump=O(1) linear, pool=fixed-size blocks, freelist=variable with coalescing",
        },
        "growth": {
            "type": "string",
            "enum": ["fixed", "chain", "double"],
            "description": "fixed=no growth, chain=link new pages, double=double size",
        },
        "thread_safety": {
            "type": "string",
            "enum": ["single_threaded", "thread_local", "lock_free"],
        },
        "page_size": {
            "type": "integer",
            "description": "Page size in bytes. MUST be power of two.",
        },
        "max_pages": {
            "type": "integer",
            "description": "Max pages. Use total_size / page_size if user gives total.",
        },
        "max_alloc_size": {
            "type": "integer",
            "description": "Max single alloc in bytes. 0 = page_size minus header overhead.",
        },
        "min_align": {
            "type": "integer",
            "description": "Min alignment in bytes. MUST be power of two.",
        },
        "zero_on_alloc": { "type": "boolean" },
        "zero_on_reset": { "type": "boolean" },
        "no_use_after_reset": { "type": "boolean" },
    },
    "required": [
        "name", "strategy", "growth", "thread_safety",
        "page_size", "max_pages", "max_alloc_size", "min_align",
        "zero_on_alloc", "zero_on_reset", "no_use_after_reset",
    ],
})

SYSTEM_PROMPT = (
    "You translate memory allocator descriptions into formal JSON specs. "
    "You MUST fill ALL fields — no omissions. "
    "Rules: "
    "page_size and min_align MUST be powers of two. "
    "name must be lowercase valid C identifier. "
    "If user says 'fast' or 'simple': strategy=bump, thread_safety=single_threaded. "
    "If user says 'secure'/'sensitive': zero_on_alloc=true, zero_on_reset=true. "
    "If user says 'thread'/'concurrent'/'parallel': thread_safety=lock_free. "
    "If user gives total size (e.g. '64KB'): set page_size appropriately and max_pages=total/page_size. "
    "For a '64KB' arena with no page size specified, use page_size=65536 and max_pages=1. "
    "If user says 'game frame': strategy=bump, growth=fixed, single_threaded. "
    "max_alloc_size=0 unless user specifies a limit."
)


def intent_to_spec_claude(intent: str) -> ArenaSpec | None:
    """Use Claude CLI to translate intent → ArenaSpec via structured JSON output."""
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
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None

    try:
        response = json.loads(result.stdout)
        # structured_output contains the schema-validated JSON
        params = response.get("structured_output")
        if params and isinstance(params, dict):
            return _tool_input_to_spec(params)
        # fallback: try parsing result as JSON
        raw = response.get("result", "")
        if isinstance(raw, str):
            params = json.loads(raw)
            return _tool_input_to_spec(params)
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    return None


def _tool_input_to_spec(params: dict) -> ArenaSpec:
    """Convert Claude's tool_use output into an ArenaSpec."""
    strategy_map = {
        "bump": AllocStrategy.BUMP,
        "pool": AllocStrategy.POOL,
        "freelist": AllocStrategy.FREELIST,
    }
    growth_map = {
        "fixed": GrowthPolicy.FIXED,
        "chain": GrowthPolicy.CHAIN,
        "double": GrowthPolicy.DOUBLE,
    }
    thread_map = {
        "single_threaded": ThreadSafety.SINGLE_THREADED,
        "thread_local": ThreadSafety.THREAD_LOCAL,
        "lock_free": ThreadSafety.LOCK_FREE,
    }

    return ArenaSpec(
        name=params.get("name", "arena"),
        strategy=strategy_map.get(params.get("strategy", "bump"), AllocStrategy.BUMP),
        growth=growth_map.get(params.get("growth", "fixed"), GrowthPolicy.FIXED),
        thread_safety=thread_map.get(params.get("thread_safety", "single_threaded"), ThreadSafety.SINGLE_THREADED),
        memory=MemoryBounds(
            page_size=params.get("page_size", 4096),
            max_pages=params.get("max_pages", 1),
            max_alloc_size=params.get("max_alloc_size", 0),
        ),
        alignment=AlignmentSpec(
            min_align=params.get("min_align", 16),
            max_align=max(params.get("min_align", 16), 64),
        ),
        safety=SafetyInvariants(
            zero_on_alloc=params.get("zero_on_alloc", False),
            zero_on_reset=params.get("zero_on_reset", False),
            no_use_after_reset=params.get("no_use_after_reset", False),
        ),
    )


# ── Regex fallback translator ──

def parse_size(s: str) -> int | None:
    """Parse a human-readable size string like '4KB', '1MB', '256 bytes'."""
    s = s.strip().upper().replace(" ", "")
    match = re.match(r"(\d+)(B|BYTES?|KB|MB|GB)?", s)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2) or "B"
    multipliers = {
        "B": 1, "BYTE": 1, "BYTES": 1,
        "KB": 1024,
        "MB": 1024 * 1024,
        "GB": 1024 * 1024 * 1024,
    }
    return value * multipliers.get(unit, 1)


def extract_sizes(text: str) -> dict[str, int]:
    """Extract all size mentions from text with context."""
    sizes = {}
    patterns = [
        (r"(\d+\s*(?:KB|MB|GB|bytes?))\s+page", "page_size"),
        (r"page\s*(?:size)?\s*(?:of|:)?\s*(\d+\s*(?:KB|MB|GB|bytes?))", "page_size"),
        (r"(\d+)\s*-?\s*byte\s+align", "alignment"),
        (r"align(?:ment|ed)?\s*(?:to|of|:)?\s*(\d+)", "alignment"),
        (r"(\d+\s*(?:KB|MB|GB|bytes?))\s+(?:arena|pool|buffer)", "total_size"),
        (r"(?:arena|pool|buffer)\s*(?:size)?\s*(?:of|:)?\s*(\d+\s*(?:KB|MB|GB|bytes?))", "total_size"),
        (r"max\s*(?:alloc|allocation)\s*(?:size)?\s*(?:of|:)?\s*(\d+\s*(?:KB|MB|GB|bytes?))", "max_alloc"),
        (r"(\d+)\s+pages?", "num_pages"),
    ]
    for pattern, key in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            val = m.group(1)
            if key == "num_pages":
                sizes[key] = int(val)
            elif key == "alignment":
                sizes[key] = int(val) if val.isdigit() else parse_size(val)
            else:
                sizes[key] = parse_size(val)
    return sizes


def intent_to_spec_regex(intent: str) -> ArenaSpec:
    """Regex-based fallback translator for when Claude API is unavailable."""
    sizes = extract_sizes(intent)
    text_lower = intent.lower()

    # Strategy
    if any(w in text_lower for w in ["pool", "fixed-size", "slab"]):
        strategy = AllocStrategy.POOL
    elif any(w in text_lower for w in ["freelist", "free list", "coalesce"]):
        strategy = AllocStrategy.FREELIST
    else:
        strategy = AllocStrategy.BUMP

    # Growth
    if any(w in text_lower for w in ["chain", "linked", "growable", "grow"]):
        growth = GrowthPolicy.CHAIN
    elif any(w in text_lower for w in ["double", "doubling", "exponential"]):
        growth = GrowthPolicy.DOUBLE
    else:
        growth = GrowthPolicy.FIXED

    # Threading
    if any(w in text_lower for w in ["lock-free", "lockfree", "atomic", "concurrent"]):
        threading = ThreadSafety.LOCK_FREE
    elif any(w in text_lower for w in ["thread-local", "thread local", "per-thread"]):
        threading = ThreadSafety.THREAD_LOCAL
    elif any(w in text_lower for w in ["thread", "multi-thread", "multithread"]):
        threading = ThreadSafety.LOCK_FREE
    else:
        threading = ThreadSafety.SINGLE_THREADED

    # Safety
    safety_flags = {}
    if any(w in text_lower for w in ["zero", "zeroed", "clear", "secure"]):
        safety_flags["zero_on_alloc"] = True
    if any(w in text_lower for w in ["secure reset", "secure free", "wipe"]):
        safety_flags["zero_on_reset"] = True
    if any(w in text_lower for w in ["generation", "use-after-free", "use after free", "uaf"]):
        safety_flags["no_use_after_reset"] = True

    # Build spec
    page_size = sizes.get("page_size", 4096)
    num_pages = sizes.get("num_pages", 1)
    if "total_size" in sizes and "page_size" in sizes:
        num_pages = max(1, sizes["total_size"] // page_size)
    elif "total_size" in sizes:
        page_size = sizes["total_size"]

    align_val = sizes.get("alignment", 16)

    name_match = re.search(r"(?:called|named)\s+['\"]?(\w+)['\"]?", intent, re.IGNORECASE)
    name = name_match.group(1) if name_match else "arena"

    return ArenaSpec(
        name=name,
        strategy=strategy,
        growth=growth,
        thread_safety=threading,
        memory=MemoryBounds(
            page_size=page_size,
            max_pages=num_pages,
            max_alloc_size=sizes.get("max_alloc", 0),
        ),
        alignment=AlignmentSpec(
            min_align=align_val,
            max_align=max(align_val, 64),
        ),
        safety=SafetyInvariants(
            zero_on_alloc=safety_flags.get("zero_on_alloc", False),
            zero_on_reset=safety_flags.get("zero_on_reset", False),
            no_use_after_reset=safety_flags.get("no_use_after_reset", False),
        ),
    )


# ── Public API ──

def intent_to_spec(intent: str) -> ArenaSpec:
    """
    Translate natural language intent into a formal ArenaSpec.

    Tries Claude CLI first (better understanding of ambiguous intent).
    Falls back to regex parser if CLI unavailable or fails.
    """
    # Try Claude CLI first
    spec = intent_to_spec_claude(intent)
    if spec is not None:
        print("  (translated by Claude via CLI)")
        return spec

    # Fallback to regex
    print("  (translated by regex fallback — install Claude CLI for AI translation)")
    return intent_to_spec_regex(intent)
