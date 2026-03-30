"""
NLAH prompt format support for prompt2bin.

`.prompt` files are still primarily natural-language, but may optionally begin
with TOML frontmatter delimited by `+++` lines. When present, the frontmatter
configures compilation stages, contracts, failure handling, caching, and
adapters — while the body remains the intent text.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


@dataclass
class ContractSpec:
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    validation_gates: list[str] = field(default_factory=list)


class FailureAction(str, Enum):
    RETRY = "retry"
    RETRY_WITH_CLARIFICATION = "retry_with_clarification"
    OPTIMIZE_THEN_RETRY = "optimize_then_retry"
    ABORT = "abort"
    SKIP = "skip"

    @classmethod
    def parse(cls, value: str) -> "FailureAction":
        if not isinstance(value, str):
            raise TypeError("failure action must be a string")
        normalized = value.strip().lower().replace("-", "_")
        try:
            return FailureAction(normalized)
        except ValueError as e:
            raise ValueError(f"invalid failure action: {value!r}") from e


@dataclass
class FailureTaxonomy:
    wat_generation_failure: FailureAction = FailureAction.RETRY
    test_failure: FailureAction = FailureAction.RETRY
    budget_exceeded: FailureAction = FailureAction.OPTIMIZE_THEN_RETRY


_DEFAULT_PIPELINE = ["translate", "verify", "codegen", "compile", "optimize", "aot", "test"]


@dataclass
class StageSpec:
    pipeline: list[str] = field(default_factory=lambda: list(_DEFAULT_PIPELINE))
    skip: list[str] = field(default_factory=list)

    @property
    def active_stages(self) -> list[str]:
        skip = {s.strip().lower() for s in self.skip}
        return [s for s in self.pipeline if s.strip().lower() not in skip]


@dataclass
class StateSpec:
    persist: list[str] = field(default_factory=list)
    ephemeral: list[str] = field(default_factory=list)


@dataclass
class AdapterSpec:
    wasm_opt: bool = True


class CachePolicy(str, Enum):
    CONTENT_HASH = "content-hash"
    NONE = "none"
    ALWAYS = "always"

    @classmethod
    def parse(cls, value: str) -> "CachePolicy":
        if not isinstance(value, str):
            raise TypeError("cache policy must be a string")
        normalized = value.strip().lower().replace("_", "-")
        try:
            return CachePolicy(normalized)
        except ValueError as e:
            raise ValueError(f"invalid cache policy: {value!r}") from e


@dataclass
class NlahPrompt:
    body: str

    # Top-level metadata (all optional overrides)
    target: str | None = None
    wasi: list[str] | None = None
    size_budget: int | None = None
    cache: CachePolicy = CachePolicy.CONTENT_HASH

    # Structured sections (defaulted)
    contracts: ContractSpec = field(default_factory=ContractSpec)
    stages: StageSpec = field(default_factory=StageSpec)
    failure: FailureTaxonomy = field(default_factory=FailureTaxonomy)
    state: StateSpec = field(default_factory=StateSpec)
    adapters: AdapterSpec = field(default_factory=AdapterSpec)

    # Provenance
    source_path: Path | None = None
    has_frontmatter: bool = False


def _expect_table(value: Any, field_path: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise TypeError(f"{field_path} must be a TOML table")


def _expect_str_list(value: Any, field_path: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise TypeError(f"{field_path} must be a list of strings")
    return value


def parse_prompt(text: str, source_path: str | Path | None = None) -> NlahPrompt:
    """
    Parse a `.prompt` file into (metadata + natural-language body).

    Frontmatter syntax:
        +++
        ...TOML...
        +++
        body...

    If no valid frontmatter is present, the entire text is treated as the body.
    """
    src = Path(source_path) if isinstance(source_path, (str, Path)) else None
    raw_text = text or ""

    # Detect `+++` frontmatter only when it is the very first line.
    lines = raw_text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "+++":
        return NlahPrompt(body=raw_text.strip(), source_path=src, has_frontmatter=False)

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "+++":
            end_idx = i
            break

    # If the opening delimiter is present but the closing delimiter is missing,
    # treat as a plain-text prompt for backward compatibility.
    if end_idx is None:
        return NlahPrompt(body=raw_text.strip(), source_path=src, has_frontmatter=False)

    frontmatter_text = "".join(lines[1:end_idx])
    body_text = "".join(lines[end_idx + 1 :])

    try:
        raw = tomllib.loads(frontmatter_text) if frontmatter_text.strip() else {}
    except (tomllib.TOMLDecodeError, TypeError) as e:
        loc = f"{src}" if src else "<prompt>"
        raise ValueError(f"Invalid TOML frontmatter in {loc}: {e}") from e

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        loc = f"{src}" if src else "<prompt>"
        raise ValueError(f"Invalid TOML frontmatter in {loc}: expected a table at top level")

    # Top-level overrides
    target = raw.get("target")
    if target is not None and not isinstance(target, str):
        raise TypeError("target must be a string")

    wasi = raw.get("wasi")
    if wasi is not None:
        wasi_list = _expect_str_list(wasi, "wasi")
    else:
        wasi_list = None

    size_budget = raw.get("size_budget")
    if size_budget is not None and not isinstance(size_budget, int):
        raise TypeError("size_budget must be an integer")

    cache = raw.get("cache")
    cache_policy = CachePolicy.CONTENT_HASH
    if cache is not None:
        cache_policy = CachePolicy.parse(cache)

    # [contracts]
    raw_contracts = _expect_table(raw.get("contracts"), "contracts")
    contracts = ContractSpec(
        preconditions=_expect_str_list(raw_contracts.get("preconditions"), "contracts.preconditions"),
        postconditions=_expect_str_list(raw_contracts.get("postconditions"), "contracts.postconditions"),
        validation_gates=_expect_str_list(raw_contracts.get("validation_gates"), "contracts.validation_gates"),
    )

    # [stages]
    raw_stages = _expect_table(raw.get("stages"), "stages")
    pipeline = raw_stages.get("pipeline")
    if pipeline is not None:
        pipeline_list = _expect_str_list(pipeline, "stages.pipeline")
    else:
        pipeline_list = list(_DEFAULT_PIPELINE)
    stages = StageSpec(
        pipeline=pipeline_list,
        skip=_expect_str_list(raw_stages.get("skip"), "stages.skip"),
    )

    # [failure]
    raw_failure = _expect_table(raw.get("failure"), "failure")
    failure = FailureTaxonomy(
        wat_generation_failure=FailureAction.parse(raw_failure.get("wat_generation_failure", FailureAction.RETRY.value)),
        test_failure=FailureAction.parse(raw_failure.get("test_failure", FailureAction.RETRY.value)),
        budget_exceeded=FailureAction.parse(
            raw_failure.get("budget_exceeded", FailureAction.OPTIMIZE_THEN_RETRY.value)
        ),
    )

    # [state]
    raw_state = _expect_table(raw.get("state"), "state")
    state = StateSpec(
        persist=_expect_str_list(raw_state.get("persist"), "state.persist"),
        ephemeral=_expect_str_list(raw_state.get("ephemeral"), "state.ephemeral"),
    )

    # [adapters]
    raw_adapters = _expect_table(raw.get("adapters"), "adapters")
    wasm_opt = raw_adapters.get("wasm_opt", True)
    if not isinstance(wasm_opt, bool):
        raise TypeError("adapters.wasm_opt must be a boolean")
    adapters = AdapterSpec(wasm_opt=wasm_opt)

    return NlahPrompt(
        body=body_text.strip(),
        target=target,
        wasi=wasi_list,
        size_budget=size_budget,
        cache=cache_policy,
        contracts=contracts,
        stages=stages,
        failure=failure,
        state=state,
        adapters=adapters,
        source_path=src,
        has_frontmatter=True,
    )

