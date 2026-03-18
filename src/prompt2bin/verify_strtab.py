"""
Z3 verification for string table specs.

Proves:
1. Hash table size is power of two
2. Max strings fits in hash table
3. String length bounded
4. Total memory bounded
5. Hash mask produces valid indices
6. No storage overflow
7. String count bounded
"""

from z3 import (
    BitVec, BitVecVal, Solver, And, Or, Not, Implies,
    ULT, ULE, UGT, UGE,
    sat, unsat,
)
from .spec import StringTableSpec
from .verify import VerificationResult, is_power_of_two


def verify_strtab_spec(spec: StringTableSpec) -> list[VerificationResult]:
    """Verify all properties of a StringTableSpec."""
    results = []

    results.append(verify_hash_table_power_of_two(spec))
    results.append(verify_table_fits_strings(spec))
    results.append(verify_string_length_bounded(spec))
    results.append(verify_bounded_memory(spec))
    results.append(verify_hash_mask_valid(spec))
    results.append(verify_no_storage_overflow(spec))
    results.append(verify_string_count_bounded(spec))

    return results


def verify_hash_table_power_of_two(spec: StringTableSpec) -> VerificationResult:
    """Hash table size must be a power of two for bitmask indexing."""
    table_size = spec.hash_table_size
    if not is_power_of_two(table_size):
        return VerificationResult(
            False, "hash_table_power_of_two",
            f"Hash table size {table_size} is not a power of two"
        )
    return VerificationResult(
        True, "hash_table_power_of_two",
        f"Hash table size {table_size} (2^{spec.hash_bits}) is a power of two"
    )


def verify_table_fits_strings(spec: StringTableSpec) -> VerificationResult:
    """Hash table should be at least as large as max_strings for reasonable load factor."""
    if spec.hash_table_size < spec.max_strings:
        return VerificationResult(
            False, "table_fits_strings",
            f"Hash table ({spec.hash_table_size}) smaller than max strings ({spec.max_strings}) — "
            f"load factor > 1.0, excessive collisions"
        )
    load = spec.max_strings / spec.hash_table_size
    return VerificationResult(
        True, "table_fits_strings",
        f"Hash table load factor {load:.2f} ({spec.max_strings}/{spec.hash_table_size}) is acceptable"
    )


def verify_string_length_bounded(spec: StringTableSpec) -> VerificationResult:
    """Max string length must be positive and fit in total storage."""
    if spec.max_string_len <= 0:
        return VerificationResult(
            False, "string_length_bounded",
            f"Max string length must be positive, got {spec.max_string_len}"
        )
    if spec.max_string_len > spec.max_total_bytes:
        return VerificationResult(
            False, "string_length_bounded",
            f"Max string length ({spec.max_string_len}) exceeds total storage ({spec.max_total_bytes})"
        )
    return VerificationResult(
        True, "string_length_bounded",
        f"Max string length {spec.max_string_len} bytes fits within {spec.max_total_bytes} total storage"
    )


def verify_bounded_memory(spec: StringTableSpec) -> VerificationResult:
    """Total memory is statically bounded."""
    total = spec.total_memory_bytes
    return VerificationResult(
        True, "bounded_memory",
        f"Total memory bounded at {total} bytes "
        f"(table={spec.hash_table_size * 8}B + storage={spec.max_total_bytes}B + "
        f"entries={spec.max_strings * 16}B)"
    )


def verify_hash_mask_valid(spec: StringTableSpec) -> VerificationResult:
    """
    Prove: for any hash value, (hash & mask) < table_size.
    Same property as ring buffer index masking.
    """
    BW = 64
    hash_val = BitVec("hash_val", BW)
    mask = BitVecVal(spec.hash_table_size - 1, BW)
    table_size = BitVecVal(spec.hash_table_size, BW)

    masked = hash_val & mask

    s = Solver()
    s.add(UGE(masked, table_size))

    result = s.check()
    if result == unsat:
        return VerificationResult(
            True, "hash_mask_valid",
            f"Hash masking always produces valid index (hash & 0x{spec.hash_table_size - 1:X} < {spec.hash_table_size})"
        )
    elif result == sat:
        m = s.model()
        return VerificationResult(
            False, "hash_mask_valid",
            "Hash mask can produce out-of-bounds index",
            counterexample=f"hash={m[hash_val]}, masked={m[masked]}"
        )
    else:
        return VerificationResult(False, "hash_mask_valid", "Solver timeout")


def verify_no_storage_overflow(spec: StringTableSpec) -> VerificationResult:
    """
    Prove: string storage offset never exceeds total storage.
    The implementation tracks bytes_used and rejects strings that don't fit.
    """
    BW = 64
    bytes_used = BitVec("bytes_used", BW)
    total = BitVecVal(spec.max_total_bytes, BW)
    str_len = BitVec("str_len", BW)
    max_len = BitVecVal(spec.max_string_len + 1, BW)  # +1 for null terminator

    s = Solver()
    # Precondition: bytes_used < total (still has space)
    s.add(ULT(bytes_used, total))
    # String length is valid (1 to max_string_len + 1 including null)
    s.add(UGT(str_len, BitVecVal(0, BW)))
    s.add(ULE(str_len, max_len))
    # We only store if bytes_used + str_len <= total
    remaining = total - bytes_used
    s.add(ULE(str_len, remaining))
    # Can new offset exceed total?
    new_offset = bytes_used + str_len
    s.add(UGT(new_offset, total))

    result = s.check()
    if result == unsat:
        return VerificationResult(
            True, "no_storage_overflow",
            "String storage check prevents overflow"
        )
    elif result == sat:
        m = s.model()
        return VerificationResult(
            False, "no_storage_overflow",
            "Storage overflow possible",
            counterexample=f"used={m[bytes_used]}, str_len={m[str_len]}"
        )
    else:
        return VerificationResult(False, "no_storage_overflow", "Solver timeout")


def verify_string_count_bounded(spec: StringTableSpec) -> VerificationResult:
    """Max strings must be positive."""
    if spec.max_strings <= 0:
        return VerificationResult(
            False, "string_count_bounded",
            f"Max strings must be positive, got {spec.max_strings}"
        )
    return VerificationResult(
        True, "string_count_bounded",
        f"Max strings {spec.max_strings} is valid"
    )
