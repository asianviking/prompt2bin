"""
Build cache for prompt2bin.

Caches build artifacts per component keyed by SHA-256 of the prompt text.
Cache layout:
    build/.cache/{component}/
        manifest.json   ← {"prompt_hash": "abc123..."}
        {component}.h
        {component}.s
        {component}.o
"""

import hashlib
import json
import shutil
from pathlib import Path


ARTIFACT_EXTS = (".h", ".s", ".o")


def prompt_hash(text: str) -> str:
    """SHA-256 hex digest of prompt text (normalized)."""
    return hashlib.sha256(text.strip().encode()).hexdigest()


class BuildCache:
    """Manages per-component artifact caching inside build/.cache/."""

    def __init__(self, build_dir: Path):
        self.cache_dir = build_dir / ".cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

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
        # Verify all artifacts actually exist
        comp_dir = self._comp_dir(name)
        for ext in ARTIFACT_EXTS:
            if not (comp_dir / f"{name}{ext}").exists():
                return False
        return True

    def restore(self, name: str, build_dir: Path) -> bool:
        """Copy cached artifacts to build dir. Returns True on success."""
        comp_dir = self._comp_dir(name)
        try:
            for ext in ARTIFACT_EXTS:
                src = comp_dir / f"{name}{ext}"
                dst = build_dir / f"{name}{ext}"
                shutil.copy2(str(src), str(dst))
            return True
        except OSError:
            return False

    def store(self, name: str, current_hash: str, build_dir: Path) -> None:
        """Cache artifacts from build dir."""
        comp_dir = self._comp_dir(name)
        comp_dir.mkdir(parents=True, exist_ok=True)
        for ext in ARTIFACT_EXTS:
            src = build_dir / f"{name}{ext}"
            if src.exists():
                shutil.copy2(str(src), str(comp_dir / f"{name}{ext}"))
        # Write manifest last (atomic-ish)
        self._manifest_path(name).write_text(
            json.dumps({"prompt_hash": current_hash})
        )
