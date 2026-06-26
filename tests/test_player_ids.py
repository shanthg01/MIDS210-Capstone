import pytest
from sqlalchemy import text

from portalpoint.db.player_ids import derive_player_id
from portalpoint.modeling.io import get_sync_engine


def test_deterministic():
    assert derive_player_id("abc123") == derive_player_id("abc123")


def test_different_inputs_differ():
    assert derive_player_id("abc123") != derive_player_id("xyz789")


def test_positive_and_fits_63_bits():
    for raw in ("abc123", "0", "z" * 50, "247sports-99999"):
        value = derive_player_id(raw)
        assert 0 <= value < 2**63


@pytest.fixture(scope="module")
def real_barttorvik_ids() -> list[str]:
    engine = get_sync_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT barttorvik_id FROM players WHERE barttorvik_id IS NOT NULL")
        ).fetchall()
    return [r[0] for r in rows]


def test_no_collisions_across_real_player_population(real_barttorvik_ids):
    if not real_barttorvik_ids:
        pytest.skip("no players loaded in this DB")
    derived = [derive_player_id(b) for b in real_barttorvik_ids]
    assert len(derived) == len(set(derived)), "hash collision across real barttorvik_ids"
