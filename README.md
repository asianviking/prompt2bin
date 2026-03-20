```
                                  _   ____  _     _
  _ __  _ __ ___  _ __ ___  _ __ | |_|___ \| |__ (_)_ __
 | '_ \| '__/ _ \| '_ ` _ \| '_ \| __| __) | '_ \| | '_ \
 | |_) | | | (_) | | | | | | |_) | |_ / __/| |_) | | | | |
 | .__/|_|  \___/|_| |_| |_| .__/ \__|_____|_.__/|_|_| |_|
 |_|                        |_|
```

**Natural language in, verified machine code out.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.3.3-orange)](https://github.com/asianviking/prompt2bin)

---

Describe what you need in plain English. A formal verification engine (Z3) proves safety properties. An LLM generates the C implementation. A test harness validates correctness. GCC compiles it to a binary.

```
"I need a lock-free audio ring buffer"
     ↓
  formal spec → Z3 proves 7/7 safety properties
     ↓
  LLM generates C → test harness validates
     ↓
  GCC → x86-64 assembly + machine code
     ↓
  audio_ringbuf.h  audio_ringbuf.s  audio_ringbuf.o
```

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

## Quick start

`p2b` is the short alias for `prompt2bin`.

```bash
# one-shot: describe what you need, get a binary
p2b "I need a memory pool for allocating small objects"

# scaffold a new project
p2b init my_project

# build all components + generate main.c + compile
p2b build my_project

# build and run
p2b run my_project

# interactive mode
p2b --interactive
```

## Project builds

A project is a directory with a `build.toml`, component `.prompt` files, and an `app.prompt`:

```
my_project/
├── build.toml
├── app.prompt                # what the app does, how components wire together
├── specs/
│   ├── memory_pool.prompt    # "I need a memory pool for allocating small objects..."
│   └── message_queue.prompt  # "I need a queue for passing messages between two threads..."
└── build/                    # generated: .h .s .o for each component + main.c
```

```toml
[project]
name = "my_project"
target = "x86-64-linux"

[model]                         # optional: per-project LLM configuration
backend = "anthropic-api"       # claude, codex, anthropic-api, openai-api
name = "claude-sonnet-4-6"
reasoning = "high"              # low, medium, high
temperature = 0.2

[components.memory_pool]
prompt = "specs/memory_pool.prompt"

[components.message_queue]
prompt = "specs/message_queue.prompt"
```

**Templates:**

```bash
p2b init my_project --template starter     # memory pool + message queue demo
p2b init my_project --template grok-cli    # interactive xAI Grok API client
```

**Build features:** incremental caching (SHA-256), parallel builds (up to 4 workers), per-project model config.

## Supported domains

| Domain | What Z3 proves |
|--------|---------------|
| **Arena allocators** | Overflow safety, alignment correctness, memory bounds |
| **Ring buffers** | Capacity invariants, index bounds, data loss prevention |
| **Process spawners** | Buffer bounds, timeouts, static memory bounds |
| **String tables** | Table sizing, index masking safety, storage bounds |
| **Terminal I/O** | Cursor/index bounds, input overflow prevention |

Each domain has 7 verified safety properties. See [`examples/`](examples/) for generated outputs including C code, assembly, and terminal transcripts.

## Examples

```
"I want a fast game frame allocator for temporary per-frame scratch memory, maybe 64KB"
→ 7/7 properties verified → game_frame.h + .s + .o

"Handle crypto key material in memory — must be wiped when done, no traces left"
→ 7/7 properties verified → crypto_secure.h + .s + .o

page_size=100, min_align=3  (bad spec)
→ [FAIL] power_of_two → Code generation blocked.
```

## License

MIT
