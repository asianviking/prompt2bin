# prompt2bin

An AI-first compiler. Natural language in, verified machine code out.

## The idea

Most programming languages were designed for humans to communicate with machines. But if AI can translate intent directly, why write code at all?

**prompt2bin** skips the language entirely. You describe what you need in plain English. A formal verification engine (Z3) proves your spec is safe. An LLM generates the C implementation. A test harness validates correctness at runtime. GCC compiles it down to x86-64 assembly and machine code.

No syntax to learn. No language to master. Just intent → verified binary.

```
"I need a lock-free ring buffer for audio, 4096 float samples"
    → formal spec (structured, machine-readable)
    → Z3 proves 7 safety properties
    → LLM generates C code
    → test harness validates runtime behavior
    → x86-64 assembly + linkable machine code
```

## Why this matters

Programming languages are encoding schemes — structured ways to express intent so a compiler can turn it into machine code. Every language is a tradeoff between expressiveness and performance. Rust gives you memory safety but demands you think in lifetimes. Python gives you speed of expression but costs you runtime speed.

What if the tradeoff disappears? If AI can go from intent to formally verified machine code, the "language" layer is just overhead. The compiler becomes the last translator you'll ever need — and it speaks English.

This is a prototype exploring that future. It works today for two domains (arena allocators and ring buffers) and proves the architecture scales.

## How it works

```
English intent
     ↓
Claude CLI → formal spec (structured, machine-readable)
     ↓
Z3 SMT solver → proves safety properties (blocks codegen on failure)
     ↓
Claude LLM → generates C code (header-only library)
     ↓
Test harness → compiles and runs validation tests
     ↓
GCC → x86-64 assembly (.s) + machine code (.o)
```

Every stage is a quality gate. Z3 blocks code generation if any safety property fails. The test harness catches runtime bugs the formal proof can't cover. GCC catches anything the LLM got syntactically wrong. If any gate fails, you get a clear error — not a broken binary.

## Install

```bash
pip install prompt2bin
```

Requires GCC and [Claude CLI](https://docs.anthropic.com/en/docs/claude-cli) for AI translation and codegen.

## Usage

`p2b` is the short alias for `prompt2bin` — both work everywhere.

```bash
p2b init my_project          # scaffold a new project
p2b build my_project         # build all components
p2b "I need a memory pool"   # single prompt, one-shot
p2b --interactive            # interactive mode
```

## Project builds

A project is a directory with a `build.toml` and `.prompt` files:

```
my_project/
├── build.toml
├── specs/
│   ├── memory_pool.prompt    # "I need a memory pool for allocating small objects..."
│   └── message_queue.prompt  # "I need a queue for passing messages between two threads..."
└── build/                    # generated: .h .s .o for each component
```

```toml
[project]
name = "my_project"
target = "x86-64-linux"

[components.memory_pool]
prompt = "specs/memory_pool.prompt"

[components.message_queue]
prompt = "specs/message_queue.prompt"
```

`prompt2bin build` reads each `.prompt` file, runs the full pipeline, and outputs all artifacts to `build/`.

## Domains

### Arena allocators
Memory arenas with bump allocation, configurable page sizes, alignment, threading models, and growth policies. Z3 proves overflow safety, alignment correctness, and memory bounds.

### Ring buffers
Lock-free SPSC/MPSC/MPMC ring buffers with bitmask indexing. Z3 proves capacity invariants, index bounds, data loss prevention, and empty/full state distinction.

Adding a new domain requires: a spec format, Z3 verification properties, and an intent translator. The LLM codegen and test harness handle the rest.

## Examples

See [`examples/`](examples/) for generated outputs including C code, assembly, and terminal transcripts.

**Game frame allocator** — vague intent, Claude infers 64KB page, bump strategy:
```
"I want a fast game frame allocator for temporary per-frame scratch memory, maybe 64KB"
→ 7/7 properties verified → game_frame.h + .s + .o
```

**Crypto-secure buffer** — security-focused, zeroing and wipe-on-reset:
```
"Handle crypto key material in memory — must be wiped when done, no traces left"
→ 7/7 properties verified → crypto_secure.h + .s + .o
```

**SPSC audio ring buffer** — lock-free, 4096 float samples:
```
"Lock-free SPSC ring buffer for audio processing, 4096 float samples"
→ 7/7 properties verified → audio_ringbuf.h + .s + .o
```

**Bad spec rejected** — Z3 catches non-power-of-two alignment:
```
page_size=100, min_align=3
→ [FAIL] power_of_two → [FAIL] aligned_allocs (counterexample: base=1, offset=1)
→ Code generation blocked.
```

## Verified properties

### Arena allocators

| Property | What it proves |
|----------|---------------|
| `power_of_two` | Page size and alignments are powers of two |
| `alignment_range` | min_align ≤ max_align ≤ page_size |
| `capacity_sanity` | Usable capacity is positive after headers |
| `no_overflow` | Bump pointer arithmetic cannot overflow |
| `aligned_allocs` | All returned pointers are correctly aligned |
| `bounded_memory` | Total memory usage never exceeds declared bound |
| `alloc_sequence` | N allocations fit safely within arena bounds |

### Ring buffers

| Property | What it proves |
|----------|---------------|
| `capacity_power_of_two` | Capacity is a power of two (enables bitmask indexing) |
| `element_size` | Element size is valid and non-zero |
| `index_bounds` | Bitmask indexing always produces valid index |
| `no_overflow` | Head/tail arithmetic safe with 64-bit wraparound |
| `no_data_loss` | Full buffer always detected before overwrite |
| `bounded_memory` | Total memory bounded at declared limit |
| `empty_full_distinct` | Empty and full states are distinguishable |

## Architecture

All source lives in `src/prompt2bin/`:

| Module | Role |
|--------|------|
| `cli.py` | Pipeline orchestrator + project build system |
| `project.py` | TOML project loader (`build.toml` → component configs) |
| `spec.py` | Formal spec formats (ArenaSpec, RingBufferSpec) |
| `intent.py` | Arena intent translator (Claude CLI + regex fallback) |
| `intent_ringbuf.py` | Ring buffer intent translator |
| `verify.py` | Z3 verification for arena specs |
| `verify_ringbuf.py` | Z3 verification for ring buffer specs |
| `codegen.py` | Template-based C generator (arena fallback) |
| `codegen_llm.py` | LLM-powered C generator (arena) |
| `codegen_ringbuf_llm.py` | LLM-powered C generator (ring buffer) |
| `test_harness.py` | Runtime test harness (arena) |
| `test_ringbuf.py` | Runtime test harness (ring buffer) |

## Requirements

- Python 3.11+
- GCC
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-cli) (for AI translation and codegen — regex fallback available for intent parsing)
