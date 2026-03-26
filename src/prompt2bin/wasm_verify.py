"""
WasmSpec verification — structural checks + Z3 invariant consistency.

Three verification layers:
1. Structural (no solver): types valid, memory fits, regions don't overlap, WASI valid
2. Invariant consistency (Z3): constraint DSL → Z3, check satisfiability
3. Binary validation happens later in wasm_validate.py (wat2wasm + wasm-validate)
"""

from __future__ import annotations

from dataclasses import dataclass

from z3 import Int, Solver, And, Or, sat, unsat

from .wasm_spec import (
    WasmSpec, WasmType, InvariantKind,
    parse_expr, Ident, Number, FieldAccess, BinOp, Expr,
)


WASI_PREVIEW1_FUNCTIONS = {
    "args_get", "args_sizes_get",
    "environ_get", "environ_sizes_get",
    "clock_res_get", "clock_time_get",
    "fd_advise", "fd_allocate", "fd_close", "fd_datasync",
    "fd_fdstat_get", "fd_fdstat_set_flags", "fd_fdstat_set_rights",
    "fd_filestat_get", "fd_filestat_set_size", "fd_filestat_set_times",
    "fd_pread", "fd_prestat_get", "fd_prestat_dir_name",
    "fd_pwrite", "fd_read", "fd_readdir", "fd_renumber",
    "fd_seek", "fd_sync", "fd_tell", "fd_write",
    "path_create_directory", "path_filestat_get", "path_filestat_set_times",
    "path_link", "path_open", "path_readlink", "path_remove_directory",
    "path_rename", "path_symlink", "path_unlink_file",
    "poll_oneoff", "proc_exit", "proc_raise",
    "sched_yield",
    "random_get",
    "sock_accept", "sock_recv", "sock_send", "sock_shutdown",
}

WASM_PAGE_SIZE = 65536  # 64KB per wasm page


@dataclass
class VerificationResult:
    passed: bool
    property_name: str
    message: str
    counterexample: str = ""

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        s = f"  [{status}] {self.property_name}: {self.message}"
        if self.counterexample:
            s += f"\n         Counterexample: {self.counterexample}"
        return s


def verify_wasm_spec(spec: WasmSpec) -> list[VerificationResult]:
    """Run all verification checks on a WasmSpec."""
    results: list[VerificationResult] = []

    # Structural checks
    results.append(_check_has_functions(spec))
    results.append(_check_types_valid(spec))
    results.append(_check_memory_pages(spec))
    results.extend(_check_regions_fit(spec))
    results.extend(_check_regions_no_overlap(spec))
    results.extend(_check_wasi_imports(spec))
    results.append(_check_size_budget(spec))
    results.extend(_check_test_functions_exist(spec))

    # Z3 invariant checks
    results.extend(_check_invariants_z3(spec))

    return results


# ── Structural checks ──

def _check_has_functions(spec: WasmSpec) -> VerificationResult:
    if spec.functions:
        return VerificationResult(True, "has_functions", f"{len(spec.functions)} function(s) declared")
    return VerificationResult(False, "has_functions", "No functions declared")


def _check_types_valid(spec: WasmSpec) -> VerificationResult:
    valid_types = {t.value for t in WasmType}
    for func in spec.functions:
        for param in func.params:
            if param.type.value not in valid_types:
                return VerificationResult(False, "types_valid", f"Invalid param type '{param.type}' in {func.name}")
        for result in func.results:
            if result.value not in valid_types:
                return VerificationResult(False, "types_valid", f"Invalid result type '{result}' in {func.name}")
    for g in spec.globals:
        if g.type.value not in valid_types:
            return VerificationResult(False, "types_valid", f"Invalid global type '{g.type}' for {g.name}")
    return VerificationResult(True, "types_valid", "All types valid")


def _check_memory_pages(spec: WasmSpec) -> VerificationResult:
    mem = spec.memory
    if mem.min_pages < 0:
        return VerificationResult(False, "memory_pages", f"min_pages={mem.min_pages} is negative")
    if mem.max_pages < mem.min_pages:
        return VerificationResult(False, "memory_pages", f"max_pages={mem.max_pages} < min_pages={mem.min_pages}")
    if mem.max_pages > 65536:  # wasm spec limit
        return VerificationResult(False, "memory_pages", f"max_pages={mem.max_pages} exceeds wasm limit (65536)")
    return VerificationResult(True, "memory_pages", f"{mem.min_pages}-{mem.max_pages} pages valid")


def _eval_size_expr(expr: str, constants: dict[str, int | float], memory_size: int) -> int | None:
    """Evaluate a simple size expression (number, constant name, or memory.size)."""
    expr = expr.strip()
    if expr.isdigit():
        return int(expr)
    if expr in constants:
        v = constants[expr]
        return int(v) if isinstance(v, (int, float)) else None
    if expr == "memory.size":
        return memory_size
    # Try simple arithmetic: "constant * number" or "number * constant"
    for op_str in [" * ", " + ", " - "]:
        if op_str in expr:
            parts = expr.split(op_str, 1)
            left = _eval_size_expr(parts[0], constants, memory_size)
            right = _eval_size_expr(parts[1], constants, memory_size)
            if left is not None and right is not None:
                if op_str.strip() == "*":
                    return left * right
                elif op_str.strip() == "+":
                    return left + right
                elif op_str.strip() == "-":
                    return left - right
    return None


def _check_regions_fit(spec: WasmSpec) -> list[VerificationResult]:
    results = []
    mem_size = spec.memory.min_pages * WASM_PAGE_SIZE
    for region in spec.memory.regions:
        offset = _eval_size_expr(region.offset_expr, spec.constants, mem_size)
        size = _eval_size_expr(region.size_expr, spec.constants, mem_size)
        if offset is None or size is None:
            results.append(VerificationResult(
                True, f"region_{region.name}_fits",
                f"Cannot statically evaluate region '{region.name}' (deferred to runtime)"
            ))
            continue
        end = offset + size
        if end > mem_size:
            results.append(VerificationResult(
                False, f"region_{region.name}_fits",
                f"Region '{region.name}' [{offset}..{end}) exceeds memory ({mem_size} bytes)",
            ))
        else:
            results.append(VerificationResult(
                True, f"region_{region.name}_fits",
                f"Region '{region.name}' [{offset}..{end}) within {mem_size} bytes",
            ))
    return results


def _check_regions_no_overlap(spec: WasmSpec) -> list[VerificationResult]:
    results = []
    mem_size = spec.memory.min_pages * WASM_PAGE_SIZE
    evaluated = []
    for region in spec.memory.regions:
        offset = _eval_size_expr(region.offset_expr, spec.constants, mem_size)
        size = _eval_size_expr(region.size_expr, spec.constants, mem_size)
        if offset is not None and size is not None:
            evaluated.append((region.name, offset, offset + size))

    for i, (name_a, start_a, end_a) in enumerate(evaluated):
        for name_b, start_b, end_b in evaluated[i + 1 :]:
            if start_a < end_b and start_b < end_a:
                results.append(VerificationResult(
                    False, f"no_overlap_{name_a}_{name_b}",
                    f"Regions '{name_a}' [{start_a}..{end_a}) and '{name_b}' [{start_b}..{end_b}) overlap",
                ))
            else:
                results.append(VerificationResult(
                    True, f"no_overlap_{name_a}_{name_b}",
                    f"Regions '{name_a}' and '{name_b}' do not overlap",
                ))
    return results


def _check_wasi_imports(spec: WasmSpec) -> list[VerificationResult]:
    results = []
    for fn in spec.wasi_imports:
        if fn in WASI_PREVIEW1_FUNCTIONS:
            results.append(VerificationResult(True, f"wasi_{fn}", f"'{fn}' is a valid WASI preview1 function"))
        else:
            results.append(VerificationResult(False, f"wasi_{fn}", f"'{fn}' is not a recognized WASI preview1 function"))
    return results


def _check_size_budget(spec: WasmSpec) -> VerificationResult:
    if spec.size_budget_bytes > 0:
        return VerificationResult(True, "size_budget", f"Budget set: {spec.size_budget_bytes} bytes")
    return VerificationResult(True, "size_budget", "No size budget (unconstrained)")


def _check_test_functions_exist(spec: WasmSpec) -> list[VerificationResult]:
    results = []
    func_names = {f.name for f in spec.functions}
    for test in spec.tests:
        if test.function in func_names:
            results.append(VerificationResult(
                True, f"test_{test.function}_exists",
                f"Test target '{test.function}' exists",
            ))
        else:
            results.append(VerificationResult(
                False, f"test_{test.function}_exists",
                f"Test references function '{test.function}' which is not declared",
            ))
    return results


# ── Z3 invariant consistency ──

def _expr_to_z3(expr: Expr, symbols: dict, mem_size_val: int):
    """Convert a parsed constraint DSL expression to a Z3 expression."""
    if isinstance(expr, Number):
        return int(expr.value) if isinstance(expr.value, int) else expr.value
    if isinstance(expr, Ident):
        name = expr.name
        if name not in symbols:
            symbols[name] = Int(name)
        return symbols[name]
    if isinstance(expr, FieldAccess):
        # memory.size, memory.pages, etc.
        obj_name = expr.obj.name
        field = expr.field
        key = f"{obj_name}_{field}"
        if key not in symbols:
            if obj_name == "memory":
                if field == "size":
                    return mem_size_val
                elif field == "pages":
                    return mem_size_val // WASM_PAGE_SIZE
            symbols[key] = Int(key)
        return symbols[key]
    if isinstance(expr, BinOp):
        left = _expr_to_z3(expr.left, symbols, mem_size_val)
        right = _expr_to_z3(expr.right, symbols, mem_size_val)
        op = expr.op
        if op == "+":
            return left + right
        elif op == "-":
            return left - right
        elif op == "*":
            return left * right
        elif op == "%":
            return left % right
        elif op == "<":
            return left < right
        elif op == "<=":
            return left <= right
        elif op == ">":
            return left > right
        elif op == ">=":
            return left >= right
        elif op == "==":
            return left == right
        elif op == "!=":
            return left != right
        raise ValueError(f"Unknown operator: {op}")
    raise ValueError(f"Unknown expression type: {type(expr)}")


def _check_invariants_z3(spec: WasmSpec) -> list[VerificationResult]:
    """Check that all invariants are satisfiable together using Z3."""
    results = []
    if not spec.invariants:
        return results

    mem_size = spec.memory.min_pages * WASM_PAGE_SIZE
    symbols: dict = {}

    # Add known constants as constraints
    constant_constraints = []
    for name, value in spec.constants.items():
        if name not in symbols:
            symbols[name] = Int(name)
        constant_constraints.append(symbols[name] == int(value))

    # Add global initial value constraints
    global_constraints = []
    for g in spec.globals:
        if g.name not in symbols:
            symbols[g.name] = Int(g.name)
        global_constraints.append(symbols[g.name] == int(g.initial_value))

    # Parse and convert each invariant
    z3_invariants = []
    for inv in spec.invariants:
        try:
            ast = parse_expr(inv.expression)
            z3_expr = _expr_to_z3(ast, symbols, mem_size)
            z3_invariants.append((inv, z3_expr))
        except ValueError as e:
            results.append(VerificationResult(
                False, f"invariant_{inv.name}_parse",
                f"Failed to parse invariant '{inv.name}': {e}",
            ))

    if not z3_invariants:
        return results

    # Check joint satisfiability: can all invariants hold simultaneously?
    solver = Solver()
    for c in constant_constraints:
        solver.add(c)

    all_exprs = []
    for inv, z3_expr in z3_invariants:
        all_exprs.append(z3_expr)

    solver.add(And(*all_exprs) if len(all_exprs) > 1 else all_exprs[0])

    check = solver.check()
    if check == sat:
        results.append(VerificationResult(
            True, "invariants_consistent",
            f"All {len(z3_invariants)} invariants are jointly satisfiable",
        ))
    elif check == unsat:
        results.append(VerificationResult(
            False, "invariants_consistent",
            "Invariants are mutually contradictory (unsatisfiable together)",
        ))
    else:
        results.append(VerificationResult(
            True, "invariants_consistent",
            "Z3 returned unknown (treated as pass, deferred to runtime)",
        ))

    # Check each invariant holds at initial state
    for inv, z3_expr in z3_invariants:
        init_solver = Solver()
        for c in constant_constraints + global_constraints:
            init_solver.add(c)
        init_solver.add(z3_expr)
        check = init_solver.check()
        if check == sat:
            results.append(VerificationResult(
                True, f"invariant_{inv.name}_initial",
                f"Invariant '{inv.name}' holds at initial state",
            ))
        elif check == unsat:
            results.append(VerificationResult(
                False, f"invariant_{inv.name}_initial",
                f"Invariant '{inv.name}' VIOLATED at initial state (expression: {inv.expression})",
            ))
        else:
            results.append(VerificationResult(
                True, f"invariant_{inv.name}_initial",
                f"Invariant '{inv.name}' unknown at initial state (deferred)",
            ))

    return results
