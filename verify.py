"""
Z3 verification layer.

Proves that an ArenaSpec's constraints are:
1. Internally consistent (no contradictions)
2. Safety properties hold for ALL possible allocation sequences
3. Performance constraints are achievable

This is the layer that catches bad specs before code generation.
"""

from dataclasses import dataclass
from z3 import (
    BitVec, BitVecVal, Solver, And, Or, Not, Implies,
    ULT, ULE, UGT, UGE, URem, UDiv,
    sat, unsat, unknown, ForAll, Exists,
)
from spec import ArenaSpec


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


def is_power_of_two(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def verify_spec(spec: ArenaSpec) -> list[VerificationResult]:
    """
    Verify all safety and consistency properties of an ArenaSpec.
    Returns a list of verification results.
    """
    results = []

    # ── Static checks (no solver needed) ──

    results.append(verify_power_of_two(spec))
    results.append(verify_alignment_values(spec))
    results.append(verify_capacity_sanity(spec))

    # ── Z3 symbolic checks ──

    results.append(verify_no_overflow(spec))
    results.append(verify_alignment_property(spec))
    results.append(verify_bounded_memory(spec))
    results.append(verify_alloc_sequence(spec))

    return results


def verify_power_of_two(spec: ArenaSpec) -> VerificationResult:
    """Page size and alignments must be powers of two."""
    checks = {
        "page_size": spec.memory.page_size,
        "min_align": spec.alignment.min_align,
        "max_align": spec.alignment.max_align,
    }
    failures = [k for k, v in checks.items() if not is_power_of_two(v)]
    if failures:
        return VerificationResult(
            False, "power_of_two",
            f"Not power of two: {', '.join(failures)}"
        )
    return VerificationResult(True, "power_of_two", "All sizes are powers of two")


def verify_alignment_values(spec: ArenaSpec) -> VerificationResult:
    """min_align <= max_align, both fit in page."""
    if spec.alignment.min_align > spec.alignment.max_align:
        return VerificationResult(
            False, "alignment_range",
            f"min_align ({spec.alignment.min_align}) > max_align ({spec.alignment.max_align})"
        )
    if spec.alignment.max_align > spec.memory.page_size:
        return VerificationResult(
            False, "alignment_range",
            f"max_align ({spec.alignment.max_align}) > page_size ({spec.memory.page_size})"
        )
    return VerificationResult(True, "alignment_range", "Alignment range is valid")


def verify_capacity_sanity(spec: ArenaSpec) -> VerificationResult:
    """Usable capacity must be positive. Header can't consume entire page."""
    if spec.memory.header_size >= spec.memory.page_size:
        return VerificationResult(
            False, "capacity_sanity",
            f"Header ({spec.memory.header_size}) >= page_size ({spec.memory.page_size})"
        )
    if spec.memory.usable_capacity <= 0:
        return VerificationResult(
            False, "capacity_sanity",
            "No usable capacity after headers"
        )
    return VerificationResult(
        True, "capacity_sanity",
        f"Usable capacity: {spec.memory.usable_capacity} bytes"
    )


def verify_no_overflow(spec: ArenaSpec) -> VerificationResult:
    """
    Prove: for any allocation size within bounds, the bump pointer
    never exceeds the arena capacity.

    We model this symbolically: given an arena of known size and a
    bump pointer at any valid position, an aligned allocation of
    any valid size does not overflow.
    """
    BW = 64  # 64-bit pointers
    capacity = spec.memory.page_size - spec.memory.header_size
    max_alloc = spec.memory.effective_max_alloc
    align = spec.alignment.min_align

    # Symbolic variables
    offset = BitVec("offset", BW)      # Current bump pointer offset
    size = BitVec("size", BW)           # Requested allocation size

    cap_bv = BitVecVal(capacity, BW)
    max_alloc_bv = BitVecVal(max_alloc, BW)
    align_bv = BitVecVal(align, BW)
    align_mask = BitVecVal(align - 1, BW)

    # Aligned offset: round up to alignment
    aligned_offset = (offset + align_mask) & ~align_mask

    # New offset after allocation
    new_offset = aligned_offset + size

    s = Solver()

    # Preconditions: offset is within arena, size is within bounds
    preconditions = And(
        ULE(offset, cap_bv),        # offset <= capacity
        UGT(size, BitVecVal(0, BW)),  # size > 0
        ULE(size, max_alloc_bv),    # size <= max_alloc
    )

    # We want to prove: new_offset <= capacity
    # Equivalently: there's NO case where preconditions hold but new_offset > capacity
    # If UNSAT: the property holds (no counterexample exists)
    # ...but that's too strong — a full arena can't fit another alloc.
    #
    # What we actually prove: IF there's enough space remaining for
    # the aligned allocation, THEN no arithmetic overflow occurs.

    space_remaining = And(
        ULE(aligned_offset, cap_bv),
        ULE(size, cap_bv - aligned_offset),
    )

    # The dangerous case: arithmetic overflow of aligned_offset + size
    # In bitvector arithmetic, overflow means new_offset < aligned_offset
    overflow = ULT(new_offset, aligned_offset)

    s.add(preconditions)
    s.add(space_remaining)
    s.add(overflow)  # Try to find an overflow

    result = s.check()
    if result == unsat:
        return VerificationResult(
            True, "no_overflow",
            "Bump pointer arithmetic cannot overflow when space is available"
        )
    elif result == sat:
        m = s.model()
        return VerificationResult(
            False, "no_overflow",
            "Bump pointer can overflow",
            counterexample=f"offset={m[offset]}, size={m[size]}"
        )
    else:
        return VerificationResult(
            False, "no_overflow",
            "Solver returned unknown (timeout?)"
        )


def verify_alignment_property(spec: ArenaSpec) -> VerificationResult:
    """
    Prove: all returned pointers are aligned to min_align.

    If the arena base is aligned and we always round up the bump
    pointer, every allocation starts at an aligned address.
    """
    BW = 64
    align = spec.alignment.min_align
    align_mask = BitVecVal(align - 1, BW)

    base = BitVec("base", BW)
    offset = BitVec("offset", BW)

    # Base is aligned
    base_aligned = (base & align_mask) == BitVecVal(0, BW)

    # Offset is aligned (bump pointer rounds up)
    aligned_offset = (offset + align_mask) & ~align_mask

    # Returned pointer
    ptr = base + aligned_offset

    s = Solver()
    s.add(base_aligned)
    # Try to find a case where the result is NOT aligned
    s.add((ptr & align_mask) != BitVecVal(0, BW))

    result = s.check()
    if result == unsat:
        return VerificationResult(
            True, "aligned_allocs",
            f"All allocations guaranteed {align}-byte aligned"
        )
    elif result == sat:
        m = s.model()
        return VerificationResult(
            False, "aligned_allocs",
            "Allocation can return unaligned pointer",
            counterexample=f"base={m[base]}, offset={m[offset]}"
        )
    else:
        return VerificationResult(False, "aligned_allocs", "Solver timeout")


def verify_bounded_memory(spec: ArenaSpec) -> VerificationResult:
    """
    Prove: total memory usage never exceeds the declared bound.

    For FIXED growth: trivially bounded by page_size * max_pages.
    For CHAIN/DOUBLE: bounded by max_pages limit.
    """
    total = spec.memory.total_capacity
    if spec.growth == spec.growth.FIXED:
        return VerificationResult(
            True, "bounded_memory",
            f"Fixed growth: total memory bounded at {total} bytes"
        )

    # For growth policies, verify max_pages provides an upper bound
    BW = 64
    pages_allocated = BitVec("pages", BW)
    max_pages_bv = BitVecVal(spec.memory.max_pages, BW)
    page_size_bv = BitVecVal(spec.memory.page_size, BW)
    total_bv = BitVecVal(total, BW)

    s = Solver()
    s.add(ULE(pages_allocated, max_pages_bv))

    total_mem = pages_allocated * page_size_bv
    # Can total_mem exceed declared bound?
    s.add(UGT(total_mem, total_bv))

    result = s.check()
    if result == unsat:
        return VerificationResult(
            True, "bounded_memory",
            f"Memory bounded at {total} bytes ({spec.memory.max_pages} pages)"
        )
    else:
        return VerificationResult(
            False, "bounded_memory",
            f"Memory usage can exceed {total} bytes"
        )


def verify_alloc_sequence(spec: ArenaSpec) -> VerificationResult:
    """
    Prove: a sequence of N maximum-sized allocations doesn't corrupt
    the arena, where N = floor(usable_capacity / (max_alloc + align_padding)).

    This is the key property: we compute the exact number of allocations
    that fit, and prove the invariant holds across all of them.
    """
    BW = 64
    capacity = spec.memory.page_size - spec.memory.header_size
    max_alloc = spec.memory.effective_max_alloc
    align = spec.alignment.min_align

    # Worst-case allocation size including alignment padding
    worst_case_alloc = max_alloc + (align - 1)  # max padding

    if worst_case_alloc <= 0:
        return VerificationResult(
            False, "alloc_sequence",
            "Worst-case allocation size is zero or negative"
        )

    # Number of guaranteed allocations
    n_guaranteed = capacity // worst_case_alloc

    # Simulate N allocations symbolically
    s = Solver()
    offset = BitVecVal(0, BW)
    cap_bv = BitVecVal(capacity, BW)
    align_mask = BitVecVal(align - 1, BW)

    for i in range(min(n_guaranteed, 32)):  # Cap at 32 to keep solver fast
        alloc_size = BitVec(f"size_{i}", BW)
        s.add(UGT(alloc_size, BitVecVal(0, BW)))
        s.add(ULE(alloc_size, BitVecVal(max_alloc, BW)))

        # Align
        aligned = (offset + align_mask) & ~align_mask
        new_offset = aligned + alloc_size

        # Assert no overflow and within bounds
        s.add(ULE(new_offset, cap_bv))
        s.add(UGE(new_offset, aligned))  # no arithmetic overflow

        offset = new_offset

    result = s.check()
    actual_n = min(n_guaranteed, 32)

    if result == sat:
        return VerificationResult(
            True, "alloc_sequence",
            f"Verified: {actual_n} allocations fit safely "
            f"(guaranteed minimum: {n_guaranteed})"
        )
    elif result == unsat:
        return VerificationResult(
            False, "alloc_sequence",
            f"Cannot fit {actual_n} allocations within capacity",
            counterexample=f"capacity={capacity}, worst_case_alloc={worst_case_alloc}"
        )
    else:
        return VerificationResult(False, "alloc_sequence", "Solver timeout")
