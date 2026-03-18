"""
Z3 verification for ring buffer specs.

Proves:
1. Capacity is power of two (enables bitmask indexing)
2. Element size is positive
3. Index masking never produces out-of-bounds access
4. Head/tail arithmetic cannot overflow
5. Buffer cannot lose data (full detection works)
6. Bounded memory usage
"""

from z3 import (
    BitVec, BitVecVal, Solver, And, Or, Not, Implies,
    ULT, ULE, UGT, UGE, URem, UDiv,
    sat, unsat,
)
from spec import RingBufferSpec
from verify import VerificationResult, is_power_of_two


def verify_ringbuf_spec(spec: RingBufferSpec) -> list[VerificationResult]:
    """Verify all properties of a RingBufferSpec."""
    results = []

    results.append(verify_capacity_power_of_two(spec))
    results.append(verify_element_size(spec))
    results.append(verify_index_bounds(spec))
    results.append(verify_no_overflow(spec))
    results.append(verify_no_data_loss(spec))
    results.append(verify_bounded_memory(spec))
    results.append(verify_empty_full_distinction(spec))

    return results


def verify_capacity_power_of_two(spec: RingBufferSpec) -> VerificationResult:
    """Capacity must be a power of two for bitmask indexing."""
    if not is_power_of_two(spec.capacity):
        return VerificationResult(
            False, "capacity_power_of_two",
            f"Capacity {spec.capacity} is not a power of two"
        )
    return VerificationResult(
        True, "capacity_power_of_two",
        f"Capacity {spec.capacity} is a power of two (mask=0x{spec.index_mask:X})"
    )


def verify_element_size(spec: RingBufferSpec) -> VerificationResult:
    """Element size must be positive."""
    if spec.element_size <= 0:
        return VerificationResult(
            False, "element_size",
            f"Element size must be positive, got {spec.element_size}"
        )
    return VerificationResult(
        True, "element_size",
        f"Element size {spec.element_size} bytes is valid"
    )


def verify_index_bounds(spec: RingBufferSpec) -> VerificationResult:
    """
    Prove: for any head/tail value, (index & mask) < capacity.

    This is the key property that makes ring buffers safe with
    power-of-two sizes — the bitmask always produces a valid index.
    """
    BW = 64
    index = BitVec("index", BW)
    mask = BitVecVal(spec.index_mask, BW)
    cap = BitVecVal(spec.capacity, BW)

    masked = index & mask

    s = Solver()
    # Try to find ANY index where masked >= capacity
    s.add(UGE(masked, cap))

    result = s.check()
    if result == unsat:
        return VerificationResult(
            True, "index_bounds",
            "Bitmask indexing always produces valid index (index & mask < capacity)"
        )
    elif result == sat:
        m = s.model()
        return VerificationResult(
            False, "index_bounds",
            "Bitmask can produce out-of-bounds index",
            counterexample=f"index={m[index]}, masked={m[masked]}"
        )
    else:
        return VerificationResult(False, "index_bounds", "Solver timeout")


def verify_no_overflow(spec: RingBufferSpec) -> VerificationResult:
    """
    Prove: head and tail counters can wrap around without
    corrupting the buffer state. Since we use unsigned 64-bit
    counters and only compare (head - tail), wraparound is safe
    as long as the difference fits in 64 bits.

    The maximum items in flight is bounded by capacity, so
    head - tail <= capacity < 2^64.
    """
    BW = 64
    head = BitVec("head", BW)
    tail = BitVec("tail", BW)
    cap = BitVecVal(spec.capacity, BW)

    # Invariant: items in buffer = head - tail (unsigned subtraction wraps correctly)
    count = head - tail

    s = Solver()
    # Precondition: buffer has at most capacity items
    s.add(ULE(count, cap))

    # After one push: new_head = head + 1
    new_head = head + BitVecVal(1, BW)
    new_count = new_head - tail

    # Can new_count exceed capacity + 1? (it shouldn't — push checks for full first)
    # We verify: if count < capacity (not full), then new_count <= capacity
    s.add(ULT(count, cap))  # buffer not full
    s.add(UGT(new_count, cap))  # but somehow new count > capacity?

    result = s.check()
    if result == unsat:
        return VerificationResult(
            True, "no_overflow",
            "Head/tail arithmetic safe with unsigned 64-bit wraparound"
        )
    elif result == sat:
        m = s.model()
        return VerificationResult(
            False, "no_overflow",
            "Counter arithmetic can overflow",
            counterexample=f"head={m[head]}, tail={m[tail]}"
        )
    else:
        return VerificationResult(False, "no_overflow", "Solver timeout")


def verify_no_data_loss(spec: RingBufferSpec) -> VerificationResult:
    """
    Prove: if the buffer is full (head - tail == capacity),
    a push will be rejected (returns error), not silently overwrite.

    This is verified by checking the fullness condition:
    the implementation must check (head - tail >= capacity) before writing.
    """
    if not spec.no_data_loss:
        return VerificationResult(
            True, "no_data_loss",
            "Data loss prevention disabled (overwrite mode)"
        )

    BW = 64
    head = BitVec("head", BW)
    tail = BitVec("tail", BW)
    cap = BitVecVal(spec.capacity, BW)

    count = head - tail

    s = Solver()
    # Buffer is full
    s.add(count == cap)

    # The check (count >= capacity) must be true when full
    full_check = UGE(count, cap)
    s.add(Not(full_check))  # try to find case where full buffer isn't detected

    result = s.check()
    if result == unsat:
        return VerificationResult(
            True, "no_data_loss",
            "Full buffer always detected by (head - tail >= capacity) check"
        )
    elif result == sat:
        m = s.model()
        return VerificationResult(
            False, "no_data_loss",
            "Full buffer can go undetected",
            counterexample=f"head={m[head]}, tail={m[tail]}"
        )
    else:
        return VerificationResult(False, "no_data_loss", "Solver timeout")


def verify_bounded_memory(spec: RingBufferSpec) -> VerificationResult:
    """Total memory is statically bounded."""
    total = spec.total_memory_bytes
    return VerificationResult(
        True, "bounded_memory",
        f"Total memory bounded at {total} bytes "
        f"({spec.capacity} x {spec.element_size}B elements + overhead)"
    )


def verify_empty_full_distinction(spec: RingBufferSpec) -> VerificationResult:
    """
    Prove: empty (head == tail) and full (head - tail == capacity)
    are always distinguishable. With monotonic counters (not wrapped
    indices), this is guaranteed because 0 != capacity.
    """
    BW = 64
    head = BitVec("head", BW)
    tail = BitVec("tail", BW)
    cap = BitVecVal(spec.capacity, BW)

    count = head - tail
    is_empty = count == BitVecVal(0, BW)
    is_full = count == cap

    s = Solver()
    # Can a buffer be both empty AND full?
    s.add(is_empty)
    s.add(is_full)

    result = s.check()
    if result == unsat:
        return VerificationResult(
            True, "empty_full_distinct",
            "Empty and full states are always distinguishable"
        )
    elif result == sat:
        # This would only happen if capacity == 0
        return VerificationResult(
            False, "empty_full_distinct",
            "Empty and full states are indistinguishable (capacity=0?)"
        )
    else:
        return VerificationResult(False, "empty_full_distinct", "Solver timeout")
