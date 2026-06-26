"""Deterministic player_id derivation — replaces SERIAL auto-increment.

barttorvik_id has 100% coverage and zero collisions across the current
player population (validated manually, see CLAUDE.md Process Improvement
TODO #1; test_player_ids.py now re-validates this against the live DB on
every run). Hashing it gives the same player the same id on every machine,
every reingest — the old SERIAL id was a local surrogate that diverged
across environments and caused a real ForeignKeyViolation when a committed
parquet's player_ids didn't exist in a different machine's players table.
"""
import hashlib

# Masks SHA-256's first 8 bytes to 63 bits so the result always fits a
# positive Postgres BIGINT (signed 64-bit; 64th bit would risk negative).
_MASK_63_BIT = 0x7FFF_FFFF_FFFF_FFFF


def derive_player_id(barttorvik_id: str) -> int:
    digest = hashlib.sha256(barttorvik_id.encode()).digest()[:8]
    return int.from_bytes(digest, "big") & _MASK_63_BIT
