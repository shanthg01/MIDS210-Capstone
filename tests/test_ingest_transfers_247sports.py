"""Unit tests for ingest_transfers_247sports.py pure matching functions.

No DB or network required — all tests use synthetic inputs.
"""
from __future__ import annotations

import pytest

from scripts.ingest_transfers_247sports import _match_player, _normalize_name


# ---------------------------------------------------------------------------
# _normalize_name
# ---------------------------------------------------------------------------

class TestNormalizeName:
    def test_lowercase(self):
        assert _normalize_name("John Smith") == "john smith"

    def test_strips_jr_period(self):
        assert _normalize_name("Curtis Williams Jr.") == "curtis williams"

    def test_strips_jr_no_period(self):
        assert _normalize_name("Marcus Jones Jr") == "marcus jones"

    def test_strips_sr(self):
        assert _normalize_name("Darius Brown Sr.") == "darius brown"

    def test_strips_ii(self):
        assert _normalize_name("Michael Green II") == "michael green"

    def test_strips_iii(self):
        assert _normalize_name("James Thompson III") == "james thompson"

    def test_removes_accents(self):
        assert _normalize_name("José García") == "jose garcia"

    def test_collapses_extra_spaces(self):
        assert _normalize_name("  John   Smith  ") == "john smith"

    def test_no_change_plain_name(self):
        assert _normalize_name("john smith") == "john smith"

    def test_empty_string(self):
        assert _normalize_name("") == ""


# ---------------------------------------------------------------------------
# _match_player
# ---------------------------------------------------------------------------

class TestMatchPlayer:
    # (player_id, full_name, position)
    ROSTER = [
        (1, "John Smith", "SG"),
        (2, "Michael Johnson", "PF"),
        (3, "David Lee", "PG"),
        (4, "Chris Williams", "SF"),
        (5, "Curtis Williams", "PF"),
    ]

    def test_exact_match(self):
        pid, conf, tag = _match_player("John Smith", self.ROSTER)
        assert pid == 1
        assert tag == "matched"
        assert conf > 0.9

    def test_case_insensitive(self):
        pid, _, tag = _match_player("john smith", self.ROSTER)
        assert pid == 1 and tag == "matched"

    def test_strips_suffix_before_match(self):
        # "Curtis Williams Jr." should match "Curtis Williams" when position disambiguates
        # (without position, "Chris Williams" also scores high → ambiguous)
        pid, conf, tag = _match_player("Curtis Williams Jr.", self.ROSTER, position_247="PF")
        assert pid == 5 and tag == "matched"

    def test_unmatched_returns_none(self):
        pid, conf, tag = _match_player("Completely Unknown Player", self.ROSTER)
        assert pid is None and tag == "unmatched"

    def test_empty_roster_returns_no_school(self):
        pid, conf, tag = _match_player("John Smith", [])
        assert pid is None and tag == "no_school"

    def test_ambiguous_without_position(self):
        # "Chris Williams" and "Curtis Williams" are similar — without position, ambiguous
        roster = [(4, "Chris Williams", "SF"), (5, "Curtis Williams", "PF")]
        pid, _, tag = _match_player("Carl Williams", roster)
        # Should be ambiguous or unmatched — not a confident single match
        assert tag in ("ambiguous", "unmatched")

    def test_position_filter_resolves_ambiguity(self):
        # Two similar names but different positions — position_247 picks the right one
        roster = [(4, "Chris Williams", "SF"), (5, "Curtis Williams", "PF")]
        pid, conf, tag = _match_player("Chris Williams", roster, position_247="SF")
        assert pid == 4 and tag == "matched"

    def test_position_filter_no_position_match_falls_back_to_full_roster(self):
        # position_247="C" but no C on roster — should still match on full roster
        pid, _, tag = _match_player("John Smith", self.ROSTER, position_247="C")
        assert pid == 1 and tag == "matched"

    def test_accent_normalization_matches(self):
        roster = [(10, "José García", "PG")]
        pid, _, tag = _match_player("Jose Garcia", roster)
        assert pid == 10 and tag == "matched"

    def test_relaxed_threshold_catches_close_miss(self):
        # "Jon Smith" vs "John Smith" — within relaxed threshold (0.75) but maybe not 0.82
        roster = [(1, "Jonathan Smith", "SG")]
        pid, conf, tag = _match_player("Jon Smith", roster)
        # Should match at relaxed threshold
        assert tag == "matched"
        assert pid == 1

    def test_position_exact_match_preferred(self):
        # Two players with same name, different position — 247's position picks right one
        roster = [(10, "Marcus Brown", "PG"), (11, "Marcus Brown", "SG")]
        pid, _, tag = _match_player("Marcus Brown", roster, position_247="PG")
        assert pid == 10 and tag == "matched"
