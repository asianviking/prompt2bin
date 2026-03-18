# prompt2bin

An AI-first compiler. Natural language in, verified machine code out.

## The idea

Most programming languages were designed for humans to communicate with machines. But if AI can translate intent directly, why write code at all?

**prompt2bin** skips the language entirely. You describe what you need in plain English — both the components and the application that wires them together. A formal verification engine (Z3) proves each spec is safe. An LLM generates the C implementation. A test harness validates correctness at runtime. GCC compiles it down to x86-64 assembly and machine code.

No syntax to learn. No language to master. Just intent → verified binary.

```
Component prompts → formal specs → Z3 proves safety → LLM generates C components
App prompt → LLM generates main.c wiring everything together
GCC → x86-64 assembly + linkable machine code → working application
```

## Why this matters

Programming languages are encoding schemes — structured ways to express intent so a compiler can turn it into machine code. Every language is a tradeoff between expressiveness and performance. Rust gives you memory safety but demands you think in lifetimes. Python gives you speed of expression but costs you runtime speed.

What if the tradeoff disappears? If AI can go from intent to formally verified machine code, the "language" layer is just overhead. The compiler becomes the last translator you'll ever need — and it speaks English.

This is a prototype exploring that future. It works today for multiple domains (arena allocators, ring buffers, process spawners, string tables, terminal I/O) and proves the architecture scales.

## How it works

```
Component .prompt files (one per component)
     ↓
Claude CLI → formal spec (structured, machine-readable)
     ↓
Z3 SMT solver → proves safety properties (blocks codegen on failure)
     ↓
Claude LLM → generates C code (header-only library per component)
     ↓
Test harness → compiles and runs validation tests
     ↓
GCC → x86-64 assembly (.s) + machine code (.o)

app.prompt (describes the whole application)
     ↓
LLM + component headers → generates main.c (the glue code)
     ↓
GCC → final linked binary
```

Every stage is a quality gate. Z3 blocks code generation if any safety property fails. The test harness catches runtime bugs the formal proof can't cover. GCC catches anything the LLM got syntactically wrong. The app.prompt generation validates against component headers with GCC and retries on failure. If any gate fails, you get a clear error — not a broken binary.

## Install

```bash
pip install prompt2bin
```

Requires GCC and at least one LLM backend:

| Backend | Setup | API key? |
|---------|-------|----------|
| **Claude CLI** (default) | [Install Claude CLI](https://docs.anthropic.com/en/docs/claude-cli) | No — uses subscription |
| **Codex CLI** | [Install Codex CLI](https://github.com/openai/codex) | No — uses subscription |
| **Anthropic API** | `pip install prompt2bin[anthropic]` + set `ANTHROPIC_API_KEY` | Yes |
| **OpenAI API** | `pip install prompt2bin[openai]` + set `OPENAI_API_KEY` | Yes |

Auto-detects in priority order: CLI > API key. Set `P2B_BACKEND` to override.

## Usage

`p2b` is the short alias for `prompt2bin` — both work everywhere.

```bash
p2b init my_project          # scaffold a new project
p2b build my_project         # build all components
p2b "I need a memory pool"   # single prompt, one-shot
p2b --interactive            # interactive mode
```

## Project builds

A project is a directory with a `build.toml`, component `.prompt` files, and an `app.prompt`:

```
my_project/
├── build.toml
├── app.prompt                # describes the application: what it does, how components wire together
├── specs/
│   ├── memory_pool.prompt    # “I need a memory pool for allocating small objects...”
│   └── message_queue.prompt  # “I need a queue for passing messages between two threads...”
└── build/                    # generated: .h .s .o for each component + main.c
```

```toml
[project]
name = “my_project”
target = “x86-64-linux”

[components.memory_pool]
prompt = “specs/memory_pool.prompt”

[components.message_queue]
prompt = “specs/message_queue.prompt”
```

The `app.prompt` describes what the application does — how the components work together as a whole:

```
Interactive demo that allocates objects and passes messages.
- Allocate several small objects from memory_pool
- Push numbered messages into message_queue from a producer loop
- Pop and print all messages from a consumer loop
- Clean up and exit with a summary
```

`prompt2bin build` reads each component `.prompt` file, runs the full pipeline, then uses the `app.prompt` + generated component headers to produce a complete `main.c` via LLM. Every piece of code — components and application glue — is generated from prompts.

See `sample_grok_cli/` for a complete example — an interactive CLI that calls the xAI Grok API, built entirely from prompts.

## Domains

### Arena allocators
Memory arenas with bump allocation, configurable page sizes, alignment, threading models, and growth policies. Z3 proves overflow safety, alignment correctness, and memory bounds.

### Ring buffers
Lock-free SPSC/MPSC/MPMC ring buffers with bitmask indexing. Z3 proves capacity invariants, index bounds, data loss prevention, and empty/full state distinction.

### Process spawners
Fork/exec wrappers with captured stdout/stderr, timeouts, and pipe-based stdin feeding. Z3 proves buffer bounds, timeouts, and static memory bounds.

### String tables
Interned string storage with hashing and deduplication. Z3 proves table sizing, index masking safety, and storage bounds.

### Terminal I/O
Bounded line input + history utilities for interactive CLIs. Z3 proves cursor/index bounds and that input can’t overflow buffers.

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

### Process spawners

| Property | What it proves |
|----------|---------------|
| `args_bounded` | Argument count is positive and bounded |
| `buffer_sizes` | Capture buffers valid when enabled |
| `timeout_positive` | Timeout is positive |
| `arg_length_valid` | Argument length is positive and bounded |
| `bounded_memory` | Total memory is statically bounded |
| `no_buffer_overflow` | Captured output can’t overflow buffers |
| `env_bounded` | Environment variable count is bounded |

### String tables

| Property | What it proves |
|----------|---------------|
| `hash_table_power_of_two` | Hash table size is a power of two |
| `table_fits_strings` | Table size supports max strings (reasonable load) |
| `string_length_bounded` | Max string length fits total storage |
| `bounded_memory` | Total memory is statically bounded |
| `hash_mask_valid` | Hash masking always yields a valid index |
| `no_storage_overflow` | String storage can’t overflow |
| `string_count_bounded` | Max strings is positive |

### Terminal I/O

| Property | What it proves |
|----------|---------------|
| `line_length` | Line buffer length is positive and bounded |
| `history_size` | History size is non-negative |
| `prompt_length` | Prompt buffer length is positive |
| `bounded_memory` | Total memory is statically bounded |
| `cursor_bounds` | Cursor stays within line bounds |
| `history_index` | History index always refers to a valid slot |
| `no_input_overflow` | Input can’t overflow line buffer |

## Architecture

All source lives in `src/prompt2bin/`:

| Module | Role |
|--------|------|
| `cli.py` | Pipeline orchestrator + project build system + LLM-based main.c generation |
| `llm.py` | LLM backend abstraction (Claude CLI / Codex CLI / Anthropic API / OpenAI API) |
| `project.py` | TOML project loader (`build.toml` + `app.prompt` → project config) |
| `spec.py` | Formal spec formats (ArenaSpec, RingBufferSpec, ProcessSpawnerSpec, StringTableSpec, TermIOSpec) |
| `intent.py` | Arena intent translator (Claude CLI + regex fallback) |
| `intent_ringbuf.py` | Ring buffer intent translator |
| `intent_proc.py` | Process spawner intent translator |
| `intent_strtab.py` | String table intent translator |
| `intent_termio.py` | Terminal I/O intent translator |
| `verify.py` | Z3 verification for arena specs |
| `verify_ringbuf.py` | Z3 verification for ring buffer specs |
| `verify_proc.py` | Z3 verification for process spawner specs |
| `verify_strtab.py` | Z3 verification for string table specs |
| `verify_termio.py` | Z3 verification for terminal I/O specs |
| `codegen.py` | Template-based C generator (arena fallback) |
| `codegen_llm.py` | LLM-powered C generator (arena) |
| `codegen_ringbuf_llm.py` | LLM-powered C generator (ring buffer) |
| `codegen_proc_llm.py` | LLM-powered C generator (process spawner) |
| `codegen_strtab_llm.py` | LLM-powered C generator (string table) |
| `codegen_termio_llm.py` | LLM-powered C generator (terminal I/O) |
| `test_harness.py` | Runtime test harness (arena) |
| `test_ringbuf.py` | Runtime test harness (ring buffer) |
| `test_proc.py` | Runtime test harness (process spawner) |
| `test_strtab.py` | Runtime test harness (string table) |
| `test_termio.py` | Runtime test harness (terminal I/O) |

## Requirements

- Python 3.11+
- GCC
- LLM backend (at least one):
  - [Claude CLI](https://docs.anthropic.com/en/docs/claude-cli) or [Codex CLI](https://github.com/openai/codex) (no API key needed)
  - Or set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` (install SDK: `pip install prompt2bin[anthropic]` or `pip install prompt2bin[openai]`)
