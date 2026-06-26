"""Catches model.py vs live-DB schema drift — the class of bug found while
validating the player_id migration (user_shortlists.player_id was
`nullable=False` in models.py but nullable in the live DB for weeks before
anything caught it). Extend this file as more drift gets found, rather than
relying on it being noticed by accident again.
"""
from sqlalchemy import text

from portalpoint.modeling.io import get_sync_engine


def _is_nullable(table: str, column: str) -> bool:
    engine = get_sync_engine()
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    return result == "YES"


def test_user_shortlists_player_id_is_not_null():
    assert not _is_nullable("user_shortlists", "player_id")
