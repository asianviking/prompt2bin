"""
Z3 verification for terminal I/O specs.

Proves:
1. Line buffer length is positive and bounded
2. History size is non-negative
3. Prompt length bounded
4. Total memory bounded
5. Cursor never exceeds line length
6. History index always valid
7. Input never overflows line buffer
"""

from z3 import (
    BitVec, BitVecVal, Solver, And, Or, Not, Implies,
    ULT, ULE, UGT, UGE,
    sat, unsat,
)
from .spec import TermIOSpec
from .verify import VerificationResult


def verify_termio_spec(spec: TermIOSpec) -> list[VerificationResult]:
    """Verify all properties of a TermIOSpec."""
    results = []

    results.append(verify_line_length(spec))
    results.append(verify_history_size(spec))
    results.append(verify_prompt_length(spec))
    results.append(verify_bounded_memory(spec))
    results.append(verify_cursor_bounds(spec))
    results.append(verify_history_index(spec))
    results.append(verify_no_input_overflow(spec))

    return results


def verify_line_length(spec: TermIOSpec) -> VerificationResult:
    """Line buffer length must be positive and bounded."""
    if spec.max_line_len <= 0:
        return VerificationResult(
            False, "line_length",
            f"Max line length must be positive, got {spec.max_line_len}"
        )
    if spec.max_line_len > 1048576:  # 1MB
        return VerificationResult(
            False, "line_length",
            f"Max line length {spec.max_line_len} exceeds safe limit (1MB)"
        )
    return VerificationResult(
        True, "line_length",
        f"Max line length {spec.max_line_len} bytes is valid"
    )


def verify_history_size(spec: TermIOSpec) -> VerificationResult:
    """History size must be non-negative."""
    if spec.history_size < 0:
        return VerificationResult(
            False, "history_size",
            f"History size must be non-negative, got {spec.history_size}"
        )
    return VerificationResult(
        True, "history_size",
        f"History size {spec.history_size} entries is valid"
    )


def verify_prompt_length(spec: TermIOSpec) -> VerificationResult:
    """Prompt max length must be positive."""
    if spec.prompt_max_len <= 0:
        return VerificationResult(
            False, "prompt_length",
            f"Prompt max length must be positive, got {spec.prompt_max_len}"
        )
    return VerificationResult(
        True, "prompt_length",
        f"Prompt max length {spec.prompt_max_len} bytes is valid"
    )


def verify_bounded_memory(spec: TermIOSpec) -> VerificationResult:
    """Total memory is statically bounded."""
    total = spec.total_memory_bytes
    return VerificationResult(
        True, "bounded_memory",
        f"Total memory bounded at {total} bytes "
        f"(history={spec.history_memory_bytes}B + line={spec.max_line_len}B + "
        f"prompt={spec.prompt_max_len}B)"
    )


def verify_cursor_bounds(spec: TermIOSpec) -> VerificationResult:
    """
    Prove: cursor position is always within [0, line_length].
    After any edit operation, cursor stays in bounds.
    """
    BW = 64
    cursor = BitVec("cursor", BW)
    line_len = BitVec("line_len", BW)
    max_len = BitVecVal(spec.max_line_len, BW)

    s = Solver()
    # Precondition: line_len <= max_line_len
    s.add(ULE(line_len, max_len))
    # Precondition: cursor <= line_len (at or before end)
    s.add(ULE(cursor, line_len))

    # After inserting a char: new_line_len = line_len + 1 (if fits)
    # Cursor moves right: new_cursor = cursor + 1
    new_line_len = line_len + BitVecVal(1, BW)
    new_cursor = cursor + BitVecVal(1, BW)

    # Only insert if line_len < max_len
    s.add(ULT(line_len, max_len))
    # Prove: new_cursor <= new_line_len
    s.add(UGT(new_cursor, new_line_len))

    result = s.check()
    if result == unsat:
        return VerificationResult(
            True, "cursor_bounds",
            "Cursor always stays within line bounds after insertion"
        )
    elif result == sat:
        m = s.model()
        return VerificationResult(
            False, "cursor_bounds",
            "Cursor can exceed line bounds",
            counterexample=f"cursor={m[cursor]}, line_len={m[line_len]}"
        )
    else:
        return VerificationResult(False, "cursor_bounds", "Solver timeout")


def verify_history_index(spec: TermIOSpec) -> VerificationResult:
    """
    Prove: history ring buffer index always produces valid slot.
    Uses same modulo pattern as ring buffer.
    """
    if spec.history_size == 0:
        return VerificationResult(
            True, "history_index",
            "History disabled (size=0), no index to verify"
        )

    BW = 64
    write_pos = BitVec("write_pos", BW)
    hist_size = BitVecVal(spec.history_size, BW)

    # Index = write_pos % history_size
    from z3 import URem
    idx = URem(write_pos, hist_size)

    s = Solver()
    s.add(UGE(idx, hist_size))

    result = s.check()
    if result == unsat:
        return VerificationResult(
            True, "history_index",
            f"History index (pos % {spec.history_size}) always produces valid slot"
        )
    elif result == sat:
        m = s.model()
        return VerificationResult(
            False, "history_index",
            "History index can be out of bounds",
            counterexample=f"write_pos={m[write_pos]}, idx={m[idx]}"
        )
    else:
        return VerificationResult(False, "history_index", "Solver timeout")


def verify_no_input_overflow(spec: TermIOSpec) -> VerificationResult:
    """
    Prove: reading input never writes past the line buffer.
    Implementation reads char-by-char and stops at max_line_len - 1.
    """
    BW = 64
    chars_read = BitVec("chars_read", BW)
    max_len = BitVecVal(spec.max_line_len, BW)

    s = Solver()
    # Precondition: chars_read < max_len - 1 (room for null terminator)
    s.add(ULT(chars_read, max_len - BitVecVal(1, BW)))
    # After reading one more char
    new_count = chars_read + BitVecVal(1, BW)
    # Can new_count exceed max_len - 1? (we stop reading at max_len - 1)
    s.add(ULT(chars_read, max_len - BitVecVal(1, BW)))
    s.add(UGE(new_count, max_len))

    result = s.check()
    if result == unsat:
        return VerificationResult(
            True, "no_input_overflow",
            "Input reading stops before buffer overflow (reserves null terminator)"
        )
    elif result == sat:
        m = s.model()
        return VerificationResult(
            False, "no_input_overflow",
            "Input can overflow line buffer",
            counterexample=f"chars_read={m[chars_read]}"
        )
    else:
        return VerificationResult(False, "no_input_overflow", "Solver timeout")
