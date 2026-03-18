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


def init_project(project_dir: str | Path) -> Path:
    """
    Scaffold a new prompt2bin project.

    Creates:
        project_dir/
        ├── build.toml
        └── specs/
            └── example.prompt
    """
    project_dir = Path(project_dir)

    if (project_dir / "build.toml").exists():
        raise FileExistsError(f"build.toml already exists in {project_dir}")

    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "specs").mkdir(exist_ok=True)

    name = project_dir.resolve().name

    (project_dir / "build.toml").write_text(f"""\
[project]
name = "{name}"
target = "x86-64-linux"

[components.example]
prompt = "specs/example.prompt"
""")

    (project_dir / "specs" / "example.prompt").write_text(
        "I need a simple arena allocator with 4KB pages and 16-byte alignment.\n"
    )

    return project_dir.resolve()
