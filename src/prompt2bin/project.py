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

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


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

    return ProjectConfig(
        name=name,
        target=target,
        components=components,
        project_dir=project_dir,
    )


def ensure_build_dir(project: ProjectConfig) -> Path:
    """Create the build/ directory if it doesn't exist."""
    build_dir = project.build_dir
    build_dir.mkdir(exist_ok=True)
    return build_dir


TEMPLATES = {
    "blank": {
        "description": "Empty project with one example prompt",
        "components": {
            "example": "I need a simple arena allocator with 4KB pages and 16-byte alignment.",
        },
    },
    "game-engine": {
        "description": "Game engine primitives — frame allocator, event queue, object pool",
        "components": {
            "frame_alloc": (
                "I need a fast arena allocator for per-frame scratch memory in a game engine. "
                "4KB pages, 16-byte alignment, single-threaded, zero memory on reset for safety."
            ),
            "event_queue": (
                "I need a lock-free SPSC ring buffer for passing 64-byte event structs "
                "between a game's input thread and render thread. 256 slots, no data loss."
            ),
            "object_pool": (
                "I need an arena allocator for a game object pool. Fixed 1MB capacity, "
                "64-byte alignment for cache-friendly access, single-threaded, no growth needed."
            ),
        },
    },
    "network-stack": {
        "description": "Network service primitives — packet buffer, connection pool, async I/O queue",
        "components": {
            "packet_buffer": (
                "I need an arena allocator for network packet assembly. 8KB pages to fit jumbo frames, "
                "8-byte alignment, single-threaded, 64 pages max. Zero on reset for security."
            ),
            "connection_pool": (
                "I need an arena allocator for tracking TCP connections. Fixed 256KB capacity, "
                "64-byte alignment for cache lines, single-threaded. Each connection struct is ~128 bytes."
            ),
            "io_queue": (
                "I need a lock-free SPSC ring buffer for async I/O completion events. "
                "Each event is 32 bytes, 1024 slots. Must not lose events."
            ),
        },
    },
    "audio-pipeline": {
        "description": "Audio/media pipeline — sample buffer, processing scratch, command queue",
        "components": {
            "sample_buffer": (
                "I need a lock-free SPSC ring buffer for streaming audio samples between "
                "capture and processing threads. 4-byte float elements, 4096 slots for ~85ms at 48kHz."
            ),
            "processing_scratch": (
                "I need an arena allocator for temporary audio DSP scratch memory. "
                "16KB pages, 32-byte alignment for SIMD, single-threaded, zero on reset."
            ),
            "command_queue": (
                "I need a lock-free SPSC ring buffer for sending 16-byte control messages "
                "from the UI thread to the audio thread. 128 slots, no data loss, non-blocking."
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
            return "blank"
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

    return project_dir.resolve(), template
