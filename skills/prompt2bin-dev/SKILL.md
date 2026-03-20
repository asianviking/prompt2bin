---
name: prompt2bin-dev
description: Develop with the prompt2bin framework — write .prompt files, scaffold projects, debug verification failures, and compile natural language to verified machine code.
---

# prompt2bin Development Skill

You are assisting a developer using **prompt2bin** — a compiler that turns natural language descriptions into formally verified, compiled machine code.

## Setup Check

Before any prompt2bin work, verify the toolchain is installed. Run `p2b --version` to check. If `p2b` is not found, guide the user through setup:

```bash
# 1. Install prompt2bin
pip install prompt2bin

# 2. Verify GCC is available (required for compilation)
gcc --version

# 3. Set up at least one LLM backend (in priority order):

# Option A: Claude CLI (recommended — no API key needed, uses subscription)
# Install from https://docs.anthropic.com/en/docs/claude-cli
claude --version

# Option B: Codex CLI (no API key needed, uses subscription)
# Install from https://github.com/openai/codex
codex --version

# Option C: Anthropic API
pip install prompt2bin[anthropic]
export ANTHROPIC_API_KEY="your-key-here"

# Option D: OpenAI API
pip install prompt2bin[openai]
export OPENAI_API_KEY="your-key-here"
```

If GCC is missing: on Ubuntu/Debian `sudo apt install gcc`, on macOS `xcode-select --install`, on Fedora `sudo dnf install gcc`.

The backend is auto-detected (CLI > API key). Set `P2B_BACKEND` to override.

## Pipeline

```
.prompt file (plain English)
  → Intent extraction (LLM classifies domain + parameters)
  → Formal spec (typed dataclass)
  → Z3 verification (7 safety properties per domain)
  → LLM code generation (C implementation)
  → Test harness (compile + run correctness tests)
  → GCC compilation (x86-64 assembly + object file)
  → Linked binary (one-shot mode or project build)
```

Verification happens BEFORE code generation. Bad specs are blocked — no C, no assembly, no binary.

## .prompt File Format

`.prompt` files are **plain English** — not structured syntax. One file describes one component.

**Pattern:** State *what* you need, the *constraints* (sizes, alignment, threading), and *safety requirements*.

### Examples

```
# Arena allocator
I need a memory pool for allocating small objects. 4KB capacity, 16-byte alignment, single-threaded.

# Ring buffer
I need a queue for passing messages between two threads. Each message is 32 bytes, 128 slots, no messages can be lost.

# Process spawner
I need a process spawner for launching curl to call an API. Capture stdout to a 512KB buffer, capture stderr to 16KB buffer, pipe stdin, 120 second timeout.

# String table
I need a string table for storing conversation context — user prompts, assistant responses, system messages. Up to 512 strings, 256KB total storage, max 8192 bytes per string. FNV-1a hash.

# Terminal I/O
I need a terminal input handler for an interactive CLI. Basic line input, 4096 byte max line length, 512 history entries.
```

## Domain Detection

The compiler classifies intent by keywords. Use these to ensure correct domain detection:

| Domain | Trigger keywords |
|--------|-----------------|
| **Arena allocator** | memory pool, allocator, arena, scratch memory, bump allocator, frame allocator |
| **Ring buffer** | ring buffer, circular buffer, queue, SPSC, MPSC, MPMC, circular queue |
| **Process spawner** | process, spawn, exec, command, subprocess, launch, fork |
| **String table** | string table, hash table, symbol table, intern, string pool, dictionary |
| **Terminal I/O** | terminal, readline, line input, CLI input, history, interactive input |

## Spec Parameters by Domain

### Arena Allocator

| Parameter | Values | Constraints |
|-----------|--------|-------------|
| strategy | bump, pool, freelist | bump = O(1), pool = fixed blocks, freelist = coalescing |
| growth | fixed, chain, double | fixed = single page, chain = linked, double = exponential |
| threading | single_threaded, thread_local, lock_free | lock_free uses atomics |
| page_size | bytes | **Must be power-of-2** |
| max_pages | integer | Total memory = page_size * max_pages |
| min_align, max_align | bytes | **Both power-of-2**, min_align <= max_align <= page_size |

Safety properties verified: `power_of_two`, `alignment_range`, `capacity_sanity`, `no_overflow`, `aligned_allocs`, `bounded_memory`, `alloc_sequence`

### Ring Buffer

| Parameter | Values | Constraints |
|-----------|--------|-------------|
| mode | spsc, mpsc, spmc, mpmc | Single/multi producer/consumer |
| element_size | bytes | Size of each slot |
| capacity | slots | **Must be power-of-2** |
| blocking | true/false | Whether operations block |
| no_data_loss | true/false | Prevent overwrites |

Safety properties verified: `capacity_power_of_two`, `element_size_positive`, `index_bounds`, `no_data_loss`, `no_torn_reads`, `bounded_memory`, `wrap_around`

### Process Spawner

| Parameter | Values | Constraints |
|-----------|--------|-------------|
| max_args | integer (default 64) | Maximum argument count |
| max_env | integer (default 64) | Maximum env vars |
| timeout_ms | integer (default 30000) | Execution timeout |
| stdout_capture | none, buffer, pipe | How to capture stdout |
| stderr_capture | none, buffer, pipe | How to capture stderr |
| stdout_buffer_size | bytes | Buffer size for stdout capture |
| stderr_buffer_size | bytes | Buffer size for stderr capture |

Safety properties verified: `bounded_memory`, `no_zombie`, `timeout_enforced`, `arg_bounds`, `env_bounds`, `buffer_bounds`, `static_memory`

### String Table

| Parameter | Values | Constraints |
|-----------|--------|-------------|
| max_strings | integer (default 1024) | Maximum entries |
| max_total_bytes | bytes (default 64KB) | Total storage |
| max_string_len | bytes (default 4096) | Per-string limit |
| hash_bits | integer (default 10) | **Should be power-of-2 sized table** |
| hash_func | fnv1a, djb_x33a | Hash algorithm |

Safety properties verified: `bounded_memory`, `no_duplicate_storage`, `null_terminated`, `table_sizing`, `index_masking`, `string_bounds`, `hash_distribution`

### Terminal I/O

| Parameter | Values | Constraints |
|-----------|--------|-------------|
| max_line_len | bytes (default 4096) | Input buffer size |
| history_size | integer (default 256) | History entries |
| prompt_max_len | bytes (default 256) | Prompt string limit |
| edit_mode | basic, readline | Input editing style |

Safety properties verified: `bounded_memory`, `no_buffer_overflow`, `null_terminated`, `cursor_bounds`, `history_bounds`, `line_bounds`, `index_safety`

## Common Verification Failures

When a user hits verification errors, diagnose using these patterns:

| Error | Cause | Fix |
|-------|-------|-----|
| `FAIL: power_of_two` | page_size or alignment not power-of-2 | Use 4096, 8192, 16384, etc. |
| `FAIL: alignment_range` | min_align > max_align or max_align > page_size | Ensure min <= max <= page_size |
| `FAIL: capacity_sanity` | Header eats all usable space | Increase page_size |
| `FAIL: aligned_allocs` + counterexample | Allocation can return unaligned pointer | Fix alignment constraints |
| `FAIL: capacity_power_of_two` | Ring buffer capacity not power-of-2 | Use 64, 128, 256, 512, 1024, etc. |
| `FAIL: no_data_loss` | Buffer can overwrite unread data | Add `no messages can be lost` to prompt |

**Key rule:** Sizes, capacities, and alignments should almost always be powers of 2.

## Project Structure

A multi-component project uses `build.toml` + `specs/*.prompt` + `app.prompt`:

```
my_project/
├── build.toml              # project config + component registry
├── app.prompt              # how components wire together (plain English)
├── specs/
│   ├── component_a.prompt  # one .prompt per component
│   └── component_b.prompt
└── build/                  # generated output: .h .s .o + main.c + binary
```

### build.toml

```toml
[project]
name = "my_project"
target = "x86-64-linux"

# Optional: per-project LLM config
[model]
backend = "anthropic-api"       # claude, codex, anthropic-api, openai-api
name = "claude-sonnet-4-6"      # model ID
reasoning = "high"              # low, medium, high (optional)
temperature = 0.2               # optional

[components.memory_pool]
prompt = "specs/memory_pool.prompt"

[components.message_queue]
prompt = "specs/message_queue.prompt"
```

### app.prompt

The `app.prompt` describes the application behavior and how components connect. It is plain English:

```
Interactive CLI for an API.
- Read user input via input_handler
- Store conversation history in context_store
- Call the API via api_caller
- Buffer responses through response_buffer
- Print responses, loop until user quits
```

## CLI Commands

```bash
# One-shot: describe what you need, get a binary
p2b "I need a lock-free audio ring buffer"

# Scaffold a new project
p2b init my_project
p2b init my_project --template starter      # arena + ring buffer demo
p2b init my_project --template grok-cli     # xAI Grok API client

# Build all components
p2b build my_project
p2b build my_project --no-cache             # skip SHA-256 cache

# Build and run
p2b run my_project

# Interactive REPL
p2b --interactive

# Debug mode (show LLM prompts, GCC commands)
p2b --debug "I need a memory pool"
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `P2B_BACKEND` | Force backend: `claude`, `codex`, `anthropic-api`, `openai-api` |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `P2B_ANTHROPIC_MODEL` | Override Anthropic model name |
| `P2B_OPENAI_MODEL` | Override OpenAI model name |
| `P2B_RUN_TIMEOUT` | Seconds to allow built binary to run |

## Writing Good .prompt Files

### Do

- Be specific about sizes: "4KB capacity", "128 slots", "32 bytes per element"
- State threading model: "single-threaded", "two threads", "lock-free"
- Mention safety needs: "no messages can be lost", "must be wiped when done"
- Use domain trigger keywords (see table above)
- Use powers of 2 for all sizes, capacities, and alignments

### Don't

- Write structured/formal syntax — use natural English
- Combine multiple domains in one file — one component per .prompt
- Use non-power-of-2 values for sizes/capacities (will fail verification)
- Be vague about constraints — "some kind of buffer" won't extract well
- Forget to specify timeout for process spawners

## When Helping Users

1. **Writing .prompt files** — Author natural language that will pass intent extraction and verification. Always use power-of-2 sizes.
2. **Scaffolding projects** — Create `build.toml`, `specs/*.prompt`, and `app.prompt` files.
3. **Debugging verification failures** — Read the Z3 counterexample and map it to the fix table above.
4. **Choosing parameters** — Recommend strategies (bump vs pool vs freelist), threading models, and buffer sizes based on the use case.
5. **Wiring multi-component apps** — Write `app.prompt` files that describe component interaction.
