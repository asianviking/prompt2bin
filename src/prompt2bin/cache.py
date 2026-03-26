"""
Build cache for prompt2bin.

Caches build artifacts per component keyed by SHA-256 of the prompt text.
Cache layout:
    build/.cache/{component}/
        manifest.json   <- {"prompt_hash": "abc123...", "target": "x86-64-linux"}

        # x86-64-linux artifacts:
        {component}.h
        {component}.s
        {component}.o

        # wasm artifacts:
        {component}.wat
        {component}.wasm
"""

import hashlib
import json
import shutil
from pathlib import Path


# Required artifact extensions by target (must all exist for cache hit)
ARTIFACT_EXTS = {
    "x86-64-linux": (".h", ".s", ".o"),
    "wasm": (".wat", ".wasm"),
}

# Optional artifacts that get cached if present (not required for cache hit)
OPTIONAL_EXTS = {
    "wasm": (".cwasm",),
}

# Legacy default (backwards compat for callers that don't pass target)
DEFAULT_TARGET = "x86-64-linux"


def prompt_hash(text: str) -> str:
    """SHA-256 hex digest of prompt text (normalized)."""
    return hashlib.sha256(text.strip().encode()).hexdigest()


class BuildCache:
    """Manages per-component artifact caching inside build/.cache/."""

    def __init__(self, build_dir: Path, target: str = DEFAULT_TARGET):
        self.cache_dir = build_dir / ".cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.target = target

    def _exts(self) -> tuple[str, ...]:
        return ARTIFACT_EXTS.get(self.target, ARTIFACT_EXTS[DEFAULT_TARGET])

    def _optional_exts(self) -> tuple[str, ...]:
        return OPTIONAL_EXTS.get(self.target, ())

    def _comp_dir(self, name: str) -> Path:
        return self.cache_dir / name

    def _manifest_path(self, name: str) -> Path:
        return self._comp_dir(name) / "manifest.json"

    def _read_manifest(self, name: str) -> dict:
        mp = self._manifest_path(name)
        if mp.exists():
            try:
                return json.loads(mp.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def is_cached(self, name: str, current_hash: str) -> bool:
        """Check if component has cached artifacts matching the prompt hash."""
        manifest = self._read_manifest(name)
        if manifest.get("prompt_hash") != current_hash:
            return False
        # Target must match (missing = legacy x86, still valid)
        cached_target = manifest.get("target", DEFAULT_TARGET)
        if cached_target != self.target:
            return False
        # Verify all artifacts actually exist
        comp_dir = self._comp_dir(name)
        for ext in self._exts():
            if not (comp_dir / f"{name}{ext}").exists():
                return False
        return True

    def restore(self, name: str, build_dir: Path) -> bool:
        """Copy cached artifacts to build dir. Returns True on success."""
        comp_dir = self._comp_dir(name)
        try:
            for ext in self._exts():
                src = comp_dir / f"{name}{ext}"
                dst = build_dir / f"{name}{ext}"
                shutil.copy2(str(src), str(dst))
            # Also restore optional artifacts if present
            for ext in self._optional_exts():
                src = comp_dir / f"{name}{ext}"
                if src.exists():
                    shutil.copy2(str(src), str(build_dir / f"{name}{ext}"))
            return True
        except OSError:
            return False

    def store(self, name: str, current_hash: str, build_dir: Path) -> None:
        """Cache artifacts from build dir."""
        comp_dir = self._comp_dir(name)
        comp_dir.mkdir(parents=True, exist_ok=True)
        for ext in (*self._exts(), *self._optional_exts()):
            src = build_dir / f"{name}{ext}"
            if src.exists():
                shutil.copy2(str(src), str(comp_dir / f"{name}{ext}"))
        # Write manifest last (atomic-ish)
        self._manifest_path(name).write_text(
            json.dumps({"prompt_hash": current_hash, "target": self.target})
        )
