"""
Project system for prompt2bin.

A project is a directory of .prompt files + a build.toml that
defines components and how they link together.

myproject/
  specs/
    allocator.prompt     ← plain text, natural language
    event_queue.prompt
  build.toml             ← component definitions
  build/                 ← generated output (gitignored)
"""

import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelConfig:
    """LLM model configuration from [model] in build.toml. All fields optional."""
    backend: str | None = None       # claude, codex, anthropic-api, openai-api
    name: str | None = None          # model ID (e.g. claude-sonnet-4-6, gpt-4o)
    reasoning: str | None = None     # low, medium, high — ignored if backend doesn't support it
    temperature: float | None = None


@dataclass
class ComponentConfig:
    name: str
    prompt_path: Path
    prompt_text: str = ""
    depends_on: list[str] = field(default_factory=list)


@dataclass
class ProjectConfig:
    name: str
    target: str
    components: dict[str, ComponentConfig]
    project_dir: Path = field(default_factory=lambda: Path("."))
    app_prompt: str | None = None
    model: ModelConfig = field(default_factory=ModelConfig)

    @property
    def build_dir(self) -> Path:
        return self.project_dir / "build"


def load_project(project_dir: str | Path = ".") -> ProjectConfig:
    """
    Load a project from a directory containing build.toml.

    Reads all .prompt files and validates the config.
    """
    project_dir = Path(project_dir).resolve()
    toml_path = project_dir / "build.toml"

    if not toml_path.exists():
        raise FileNotFoundError(f"No build.toml found in {project_dir}")

    with open(toml_path, "rb") as f:
        raw = tomllib.load(f)

    # Parse [project] section
    proj = raw.get("project", {})
    name = proj.get("name", project_dir.name)
    target = proj.get("target", "x86-64-linux")

    # Parse [model] section (all fields optional)
    raw_model = raw.get("model", {})
    model = ModelConfig(
        backend=raw_model.get("backend"),
        name=raw_model.get("name"),
        reasoning=raw_model.get("reasoning"),
        temperature=raw_model.get("temperature"),
    )
    if model.reasoning and model.reasoning not in ("low", "medium", "high"):
        raise ValueError(
            f"Invalid reasoning level '{model.reasoning}' in build.toml — "
            f"must be low, medium, or high"
        )

    # Parse [components.*] sections
    components = {}
    raw_components = raw.get("components", {})

    for comp_name, comp_cfg in raw_components.items():
        prompt_rel = comp_cfg.get("prompt", f"specs/{comp_name}.prompt")
        prompt_path = project_dir / prompt_rel

        if not prompt_path.exists():
            raise FileNotFoundError(
                f"Prompt file not found: {prompt_path}\n"
                f"  (referenced by component '{comp_name}' in build.toml)"
            )

        prompt_text = prompt_path.read_text().strip()
        if not prompt_text:
            raise ValueError(f"Prompt file is empty: {prompt_path}")

        depends_on = comp_cfg.get("depends_on", [])

        components[comp_name] = ComponentConfig(
            name=comp_name,
            prompt_path=prompt_path,
            prompt_text=prompt_text,
            depends_on=depends_on,
        )

    if not components:
        raise ValueError("No components defined in build.toml")

    # Load app.prompt if it exists
    app_prompt = None
    app_prompt_path = project_dir / "app.prompt"
    if not app_prompt_path.exists():
        app_prompt_path = project_dir / "specs" / "app.prompt"
    if app_prompt_path.exists():
        app_prompt = app_prompt_path.read_text().strip()
        if not app_prompt:
            app_prompt = None

    return ProjectConfig(
        name=name,
        target=target,
        components=components,
        project_dir=project_dir,
        app_prompt=app_prompt,
        model=model,
    )


def ensure_build_dir(project: ProjectConfig) -> Path:
    """Create the build/ directory if it doesn't exist."""
    build_dir = project.build_dir
    build_dir.mkdir(exist_ok=True)
    return build_dir


TEMPLATES = {
    "starter": {
        "description": "A memory pool and a message queue — the simplest demo",
        "app_prompt": (
            "Interactive demo that allocates objects and passes messages.\n"
            "- Allocate several small objects (16, 32, 64 bytes) from memory_pool\n"
            "- Write a tag string into each allocated block to prove it works\n"
            "- Push 10 numbered messages into message_queue from a producer loop\n"
            "- Pop and print all messages from a consumer loop\n"
            "- Print pool usage stats between allocations\n"
            "- Clean up and exit with a summary of operations performed"
        ),
        "components": {
            "memory_pool": (
                "I need a memory pool for allocating small objects. "
                "4KB capacity, 16-byte alignment, single-threaded."
            ),
            "message_queue": (
                "I need a queue for passing messages between two threads. "
                "Each message is 32 bytes, 128 slots, no messages can be lost."
            ),
        },
    },
    "grok-cli": {
        "description": "Grok CLI — xAI API via curl, context store, terminal I/O, response buffer",
        "app_prompt": (
            "Interactive CLI for the xAI Grok API.\n"
            "- Read user input via input_handler (show \"grok> \" prompt, quit on \"quit\" or EOF)\n"
            "- Store conversation history in context_store (user and assistant messages as strings)\n"
            "- Call the Grok API via api_caller using curl with JSON chat completions format\n"
            "  (POST to https://api.x.ai/v1/chat/completions with Authorization: Bearer $XAI_API_KEY)\n"
            "- Buffer streaming responses through response_buffer\n"
            "- Parse the JSON response to extract the assistant's message content\n"
            "- Print assistant responses, loop until user quits\n"
            "- Use XAI_API_KEY env var for auth (exit with error if not set)\n"
            "- Use XAI_MODEL env var for model (default grok-4-0709)\n"
            "- Maintain multi-turn conversation by sending full message history each request\n"
            "- Handle JSON escaping for user input (escape quotes, backslashes, newlines)\n"
            "- Rotate oldest messages when context_store is full"
        ),
        "components": {
            "api_caller": (
                "I need a process spawner for launching curl to call the xAI Grok API. "
                "Capture stdout to a 512KB buffer for large JSON responses, "
                "capture stderr to 16KB buffer for curl diagnostics, "
                "pipe stdin for sending JSON request bodies, 120 second timeout for long AI responses."
            ),
            "context_store": (
                "I need a string table for storing conversation context — user prompts, "
                "Grok responses, and system messages. Up to 512 strings, 256KB total storage, "
                "max 8192 bytes per string for long AI responses. FNV-1a hash."
            ),
            "input_handler": (
                "I need a terminal input handler for an interactive Grok CLI. "
                "Basic line input, 4096 byte max line length, 512 history entries "
                "for recalling previous prompts."
            ),
            "response_buffer": (
                "I need a ring buffer for buffering Grok API response chunks. "
                "Each chunk is 256 bytes, 1024 slots, SPSC, no data loss."
            ),
        },
    },
}


def _pick_template_interactive() -> str:
    """Interactive template picker. Returns template name."""
    print("\n  What are you building?\n")
    templates = list(TEMPLATES.items())
    for i, (name, tmpl) in enumerate(templates):
        marker = ">" if i == 0 else " "
        print(f"    {i + 1}) {name:20s} {tmpl['description']}")

    print()
    while True:
        try:
            choice = input("  Pick a template [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return "starter"
        if not choice:
            return templates[0][0]
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(templates):
                return templates[idx][0]
        except ValueError:
            if choice in TEMPLATES:
                return choice
        print(f"    Enter 1-{len(templates)} or a template name.")


def init_project(project_dir: str | Path, template: str | None = None) -> tuple[Path, str]:
    """
    Scaffold a new prompt2bin project.

    Returns (project_path, template_name).
    """
    project_dir = Path(project_dir)

    if (project_dir / "build.toml").exists():
        raise FileExistsError(f"build.toml already exists in {project_dir}")

    if template is None:
        template = _pick_template_interactive()

    if template not in TEMPLATES:
        raise ValueError(f"Unknown template: {template}. Available: {', '.join(TEMPLATES)}")

    tmpl = TEMPLATES[template]
    components = tmpl["components"]

    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "specs").mkdir(exist_ok=True)

    name = project_dir.resolve().name

    # Write build.toml
    lines = [f'[project]\nname = "{name}"\ntarget = "x86-64-linux"\n']
    for comp_name in components:
        lines.append(f"[components.{comp_name}]")
        lines.append(f'prompt = "specs/{comp_name}.prompt"\n')
    (project_dir / "build.toml").write_text("\n".join(lines))

    # Write .prompt files
    for comp_name, prompt_text in components.items():
        (project_dir / "specs" / f"{comp_name}.prompt").write_text(prompt_text + "\n")

    # Write app.prompt if template has one
    app_prompt = tmpl.get("app_prompt")
    if app_prompt:
        (project_dir / "app.prompt").write_text(app_prompt + "\n")

    # Write .gitignore
    (project_dir / ".gitignore").write_text("build/\n")

    # Initialize git repo and create initial commit
    subprocess.run(["git", "init"], cwd=project_dir, capture_output=True, check=True)
    subprocess.run(["git", "add", "."], cwd=project_dir, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit from prompt2bin init"],
        cwd=project_dir, capture_output=True, check=True,
    )

    return project_dir.resolve(), template
