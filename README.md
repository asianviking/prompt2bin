# prompt2bin

AI-first compiler prototype. Natural language → formally verified C code.

```
"I need a fast game frame allocator, 64KB, nothing fancy"
    → formal spec (structured, machine-readable)
    → Z3 proves safety properties
    → generates verified C code
```

## How it works

```
Human intent (natural language)
        ↓
   Claude CLI (translates intent → formal spec)
        ↓
   Z3 verification (proves overflow safety, alignment, memory bounds)
        ↓
   C code generation (header-only library)
```

The verifier blocks code generation if any safety property fails. Claude handles ambiguous intent ("maybe 64KB", "nothing fancy"); the regex fallback handles structured input when CLI is unavailable.

## Usage

```bash
pip install z3-solver
python3 prompt2bin.py "I want a fast game frame allocator, 64KB, nothing fancy"
```

Interactive mode:

```bash
python3 prompt2bin.py --interactive
```

## Examples

See [`examples/`](examples/) for generated outputs. Three demos:

**1. Game frame allocator** — vague intent, Claude infers 64KB page, bump strategy, generation tracking:
```
"I want a fast game frame allocator for temporary per-frame scratch memory, maybe 64KB, nothing fancy"
→ game_frame.h (86 lines, 7/7 properties verified)
```

**2. Crypto-secure buffer** — security-focused, Claude enables zeroing and wipe-on-reset:
```
"I need something to handle crypto key material in memory — it absolutely must be wiped when we're done, no traces left, small buffer is fine, like 512 bytes"
→ crypto_secure.h (89 lines, 7/7 properties verified)
```

**3. Lock-free concurrent arena** — explicit spec, 1MB total across 256 pages:
```
"Lock-free concurrent arena, 1MB total, 4KB pages, 64-byte alignment, max allocation of 256 bytes"
→ concurrent.h (84 lines, 7/7 properties verified, 12 allocations proven safe)
```

**4. Bad spec rejected** — Z3 catches non-power-of-two alignment with counterexample:
```
page_size=100, min_align=3
→ [FAIL] power_of_two: Not power of two: page_size, min_align
→ [FAIL] aligned_allocs: Allocation can return unaligned pointer
         Counterexample: base=1, offset=1
→ Code generation blocked.
```

## Verified properties

Z3 proves these before any code is generated:

| Property | What it proves |
|----------|---------------|
| `power_of_two` | Page size and alignments are powers of two |
| `alignment_range` | min_align ≤ max_align ≤ page_size |
| `capacity_sanity` | Usable capacity is positive after headers |
| `no_overflow` | Bump pointer arithmetic cannot overflow |
| `aligned_allocs` | All returned pointers are correctly aligned |
| `bounded_memory` | Total memory usage never exceeds declared bound |
| `alloc_sequence` | N allocations fit safely within arena bounds |

## Architecture

| File | Role |
|------|------|
| `spec.py` | Formal spec format — structured contract between intent and code |
| `intent.py` | Intent translator — Claude CLI with regex fallback |
| `verify.py` | Z3 verification — proves safety properties on the spec |
| `codegen.py` | C code generator — produces header-only library from verified spec |
| `prompt2bin.py` | Pipeline orchestrator |

## Requirements

- Python 3.10+
- `z3-solver` (`pip install z3-solver`)
- Claude CLI (optional, for AI translation — falls back to regex without it)
