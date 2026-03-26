"""
WasmSpec IR — the universal intermediate representation for wasm compilation.

Replaces domain-specific specs (ArenaSpec, RingBufferSpec, etc.) with one
general-purpose spec that can represent any pure-compute wasm module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ── Wasm types ──

class WasmType(str, Enum):
    I32 = "i32"
    I64 = "i64"
    F32 = "f32"
    F64 = "f64"


# ── Constraint DSL ──
#
# Grammar:
#   expr  := term (OP term)*
#   term  := IDENT | NUMBER | term '.' field
#   OP    := '<' | '<=' | '>' | '>=' | '==' | '!=' | '+' | '-' | '*'
#   IDENT := [a-z_][a-z0-9_]*
#   field := 'pages' | 'size' | 'offset' | 'length'

class TokenKind(str, Enum):
    IDENT = "IDENT"
    NUMBER = "NUMBER"
    OP = "OP"
    DOT = "DOT"
    EOF = "EOF"


@dataclass
class Token:
    kind: TokenKind
    value: str


# AST nodes

@dataclass
class Ident:
    name: str


@dataclass
class Number:
    value: int | float


@dataclass
class FieldAccess:
    obj: Ident
    field: str


@dataclass
class BinOp:
    left: "Expr"
    op: str
    right: "Expr"


Expr = Ident | Number | FieldAccess | BinOp

COMPARISON_OPS = {"<", "<=", ">", ">=", "==", "!="}
ARITH_OPS = {"+", "-", "*", "%"}
ALL_OPS = COMPARISON_OPS | ARITH_OPS
VALID_FIELDS = {"pages", "size", "offset", "length"}


def tokenize(s: str) -> list[Token]:
    """Tokenize a constraint expression."""
    tokens: list[Token] = []
    i = 0
    while i < len(s):
        if s[i].isspace():
            i += 1
            continue
        # Two-char operators
        if i + 1 < len(s) and s[i : i + 2] in ALL_OPS:
            tokens.append(Token(TokenKind.OP, s[i : i + 2]))
            i += 2
            continue
        # Single-char operators
        if s[i] in {c for op in ALL_OPS for c in op if len(op) == 1}:
            # Check if it's the start of a two-char op
            if i + 1 < len(s) and s[i : i + 2] in ALL_OPS:
                tokens.append(Token(TokenKind.OP, s[i : i + 2]))
                i += 2
            else:
                tokens.append(Token(TokenKind.OP, s[i]))
                i += 1
            continue
        if s[i] == ".":
            tokens.append(Token(TokenKind.DOT, "."))
            i += 1
            continue
        # Numbers
        if s[i].isdigit():
            j = i
            while j < len(s) and (s[j].isdigit() or s[j] == "."):
                j += 1
            tokens.append(Token(TokenKind.NUMBER, s[i:j]))
            i = j
            continue
        # Identifiers
        if s[i].isalpha() or s[i] == "_":
            j = i
            while j < len(s) and (s[j].isalnum() or s[j] == "_"):
                j += 1
            tokens.append(Token(TokenKind.IDENT, s[i:j]))
            i = j
            continue
        raise ValueError(f"Unexpected character '{s[i]}' at position {i}")

    tokens.append(Token(TokenKind.EOF, ""))
    return tokens


def parse_expr(s: str) -> Expr:
    """Parse a constraint DSL expression string into an AST."""
    tokens = tokenize(s)
    pos = [0]  # mutable index

    def peek() -> Token:
        return tokens[pos[0]]

    def advance() -> Token:
        t = tokens[pos[0]]
        pos[0] += 1
        return t

    def parse_term() -> Expr:
        t = peek()
        if t.kind == TokenKind.NUMBER:
            advance()
            val = float(t.value) if "." in t.value else int(t.value)
            return Number(val)
        if t.kind == TokenKind.IDENT:
            advance()
            ident = Ident(t.value)
            # Check for field access
            if peek().kind == TokenKind.DOT:
                advance()  # consume '.'
                field_tok = advance()
                if field_tok.kind != TokenKind.IDENT:
                    raise ValueError(f"Expected field name after '.', got {field_tok}")
                if field_tok.value not in VALID_FIELDS:
                    raise ValueError(
                        f"Invalid field '{field_tok.value}', "
                        f"expected one of {VALID_FIELDS}"
                    )
                return FieldAccess(ident, field_tok.value)
            return ident
        raise ValueError(f"Unexpected token: {t}")

    def parse_binary() -> Expr:
        left = parse_term()
        while peek().kind == TokenKind.OP:
            op_tok = advance()
            right = parse_term()
            left = BinOp(left, op_tok.value, right)
        return left

    result = parse_binary()
    if peek().kind != TokenKind.EOF:
        raise ValueError(f"Unexpected token after expression: {peek()}")
    return result


# ── Spec dataclasses ──

class InvariantKind(str, Enum):
    STRUCTURAL = "structural"
    RUNTIME = "runtime"


@dataclass
class Param:
    name: str
    type: WasmType


@dataclass
class FuncSpec:
    name: str
    params: list[Param]
    results: list[WasmType]
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)


@dataclass
class Region:
    name: str
    offset_expr: str
    size_expr: str


@dataclass
class MemorySpec:
    min_pages: int = 1
    max_pages: int = 1
    regions: list[Region] = field(default_factory=list)


@dataclass
class GlobalSpec:
    name: str
    type: WasmType = WasmType.I32
    mutable: bool = True
    initial_value: int | float = 0


@dataclass
class Invariant:
    name: str
    expression: str  # constraint DSL string
    kind: InvariantKind = InvariantKind.STRUCTURAL


@dataclass
class TypedValue:
    type: WasmType
    value: int | float


@dataclass
class TestCase:
    function: str
    args: list[TypedValue]
    expected: TypedValue
    description: str = ""


@dataclass
class WasmSpec:
    name: str
    description: str
    functions: list[FuncSpec]
    memory: MemorySpec = field(default_factory=MemorySpec)
    globals: list[GlobalSpec] = field(default_factory=list)
    invariants: list[Invariant] = field(default_factory=list)
    constants: dict[str, int | float] = field(default_factory=dict)
    tests: list[TestCase] = field(default_factory=list)
    wasi_imports: list[str] = field(default_factory=list)
    size_budget_bytes: int = 0
    algorithm_notes: str = ""

    def describe(self) -> str:
        """Human-readable summary."""
        lines = [
            f"WasmSpec: {self.name}",
            f"  {self.description}",
            f"  Functions: {', '.join(f.name for f in self.functions)}",
            f"  Memory: {self.memory.min_pages}-{self.memory.max_pages} pages",
        ]
        if self.globals:
            lines.append(f"  Globals: {', '.join(g.name for g in self.globals)}")
        if self.invariants:
            lines.append(f"  Invariants: {len(self.invariants)}")
        if self.tests:
            lines.append(f"  Tests: {len(self.tests)}")
        if self.size_budget_bytes:
            lines.append(f"  Size budget: {self.size_budget_bytes} bytes")
        if self.wasi_imports:
            lines.append(f"  WASI imports: {', '.join(self.wasi_imports)}")
        return "\n".join(lines)


# ── JSON schema for LLM structured output ──

WASM_SPEC_JSON_SCHEMA = {
    "type": "object",
    "required": ["name", "description", "functions"],
    "properties": {
        "name": {"type": "string", "description": "Module name (snake_case)"},
        "description": {"type": "string", "description": "What the module does"},
        "functions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "params", "results"],
                "properties": {
                    "name": {"type": "string"},
                    "params": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "type"],
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string", "enum": ["i32", "i64", "f32", "f64"]},
                            },
                        },
                    },
                    "results": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["i32", "i64", "f32", "f64"]},
                    },
                    "preconditions": {"type": "array", "items": {"type": "string"}, "default": []},
                    "postconditions": {"type": "array", "items": {"type": "string"}, "default": []},
                    "side_effects": {"type": "array", "items": {"type": "string"}, "default": []},
                },
            },
        },
        "memory": {
            "type": "object",
            "properties": {
                "min_pages": {"type": "integer", "default": 1},
                "max_pages": {"type": "integer", "default": 1},
                "regions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "offset_expr", "size_expr"],
                        "properties": {
                            "name": {"type": "string"},
                            "offset_expr": {"type": "string"},
                            "size_expr": {"type": "string"},
                        },
                    },
                    "default": [],
                },
            },
            "default": {"min_pages": 1, "max_pages": 1, "regions": []},
        },
        "globals": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": ["i32", "i64", "f32", "f64"], "default": "i32"},
                    "mutable": {"type": "boolean", "default": True},
                    "initial_value": {"type": "number", "default": 0},
                },
            },
            "default": [],
        },
        "invariants": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "expression"],
                "properties": {
                    "name": {"type": "string"},
                    "expression": {"type": "string", "description": "Constraint DSL: e.g. 'alloc_offset <= memory.size'"},
                    "kind": {"type": "string", "enum": ["structural", "runtime"], "default": "structural"},
                },
            },
            "default": [],
        },
        "constants": {
            "type": "object",
            "additionalProperties": {"type": "number"},
            "default": {},
        },
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["function", "args", "expected"],
                "properties": {
                    "function": {"type": "string"},
                    "args": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["type", "value"],
                            "properties": {
                                "type": {"type": "string", "enum": ["i32", "i64", "f32", "f64"]},
                                "value": {"type": "number"},
                            },
                        },
                    },
                    "expected": {
                        "type": "object",
                        "required": ["type", "value"],
                        "properties": {
                            "type": {"type": "string", "enum": ["i32", "i64", "f32", "f64"]},
                            "value": {"type": "number"},
                        },
                    },
                    "description": {"type": "string", "default": ""},
                },
            },
            "default": [],
        },
        "wasi_imports": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
        },
        "size_budget_bytes": {"type": "integer", "default": 0},
        "algorithm_notes": {"type": "string", "default": ""},
    },
}


def spec_from_dict(d: dict) -> WasmSpec:
    """Build a WasmSpec from a JSON dict (LLM structured output)."""

    def parse_param(p: dict) -> Param:
        return Param(name=p["name"], type=WasmType(p["type"]))

    def parse_func(f: dict) -> FuncSpec:
        return FuncSpec(
            name=f["name"],
            params=[parse_param(p) for p in f.get("params", [])],
            results=[WasmType(r) for r in f.get("results", [])],
            preconditions=f.get("preconditions", []),
            postconditions=f.get("postconditions", []),
            side_effects=f.get("side_effects", []),
        )

    def parse_memory(m: dict | None) -> MemorySpec:
        if not m:
            return MemorySpec()
        return MemorySpec(
            min_pages=m.get("min_pages", 1),
            max_pages=m.get("max_pages", 1),
            regions=[
                Region(
                    name=r["name"],
                    offset_expr=r["offset_expr"],
                    size_expr=r["size_expr"],
                )
                for r in m.get("regions", [])
            ],
        )

    def parse_global(g: dict) -> GlobalSpec:
        return GlobalSpec(
            name=g["name"],
            type=WasmType(g.get("type", "i32")),
            mutable=g.get("mutable", True),
            initial_value=g.get("initial_value", 0),
        )

    def parse_invariant(inv: dict) -> Invariant:
        return Invariant(
            name=inv["name"],
            expression=inv["expression"],
            kind=InvariantKind(inv.get("kind", "structural")),
        )

    def parse_typed_value(v: dict) -> TypedValue:
        return TypedValue(type=WasmType(v["type"]), value=v["value"])

    def parse_test(t: dict) -> TestCase:
        return TestCase(
            function=t["function"],
            args=[parse_typed_value(a) for a in t.get("args", [])],
            expected=parse_typed_value(t["expected"]),
            description=t.get("description", ""),
        )

    return WasmSpec(
        name=d["name"],
        description=d["description"],
        functions=[parse_func(f) for f in d["functions"]],
        memory=parse_memory(d.get("memory")),
        globals=[parse_global(g) for g in d.get("globals", [])],
        invariants=[parse_invariant(i) for i in d.get("invariants", [])],
        constants=d.get("constants", {}),
        tests=[parse_test(t) for t in d.get("tests", [])],
        wasi_imports=d.get("wasi_imports", []),
        size_budget_bytes=d.get("size_budget_bytes", 0),
        algorithm_notes=d.get("algorithm_notes", ""),
    )
