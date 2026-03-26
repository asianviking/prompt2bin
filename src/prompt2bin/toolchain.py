"""
Wasm toolchain detection.

Locates wabt (wat2wasm, wasm-validate), wasmtime, and optionally binaryen (wasm-opt).
"""

import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class WasmToolchain:
    wat2wasm: str | None = None
    wasm_validate: str | None = None
    wasmtime: str | None = None
    wasm_opt: str | None = None  # optional (binaryen)

    def check_required(self) -> list[str]:
        """Return list of missing required tools."""
        missing = []
        if not self.wat2wasm:
            missing.append("wat2wasm (install wabt)")
        if not self.wasm_validate:
            missing.append("wasm-validate (install wabt)")
        if not self.wasmtime:
            missing.append("wasmtime (https://wasmtime.dev)")
        return missing

    def is_ready(self) -> bool:
        return not self.check_required()


def _tool_version(path: str) -> str | None:
    """Get version string from a tool, or None on failure."""
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def detect() -> WasmToolchain:
    """Detect available wasm tools on PATH."""
    return WasmToolchain(
        wat2wasm=shutil.which("wat2wasm"),
        wasm_validate=shutil.which("wasm-validate"),
        wasmtime=shutil.which("wasmtime"),
        wasm_opt=shutil.which("wasm-opt"),
    )


def print_status(tc: WasmToolchain) -> None:
    """Print toolchain status to stdout."""
    tools = [
        ("wat2wasm", tc.wat2wasm, True),
        ("wasm-validate", tc.wasm_validate, True),
        ("wasmtime", tc.wasmtime, True),
        ("wasm-opt", tc.wasm_opt, False),
    ]
    for name, path, required in tools:
        if path:
            ver = _tool_version(path) or "unknown version"
            print(f"  [OK] {name}: {ver} ({path})")
        else:
            tag = "MISSING" if required else "not found (optional)"
            print(f"  [{'!!' if required else '--'}] {name}: {tag}")
