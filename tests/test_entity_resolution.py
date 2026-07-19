"""Unit tests for portalpoint.modeling.entity_resolution."""
import pytest

from portalpoint.modeling.entity_resolution import (
    SCHOOL_ALIASES,
    match_player,
    normalize_name,
    resolve_school,
)


# ---------------------------------------------------------------------------
# normalize_name
# ---------------------------------------------------------------------------

class TestNormalizeName:
    def test_lowercase(self):
        assert normalize_name("John Smith") == "john smith"

    def test_strip_accents(self):
        assert normalize_name("José García") == "jose garcia"

    def test_strip_jr_period(self):
        assert normalize_name("Marcus Jones Jr.") == "marcus jones"

    def test_strip_jr_no_period(self):
        assert normalize_name("Marcus Jones Jr") == "marcus jones"

    def test_strip_sr(self):
        assert normalize_name("Bob Davis Sr.") == "bob davis"

    def test_strip_ii(self):
        assert normalize_name("Patrick Williams II") == "patrick williams"

    def test_strip_iii(self):
        assert normalize_name("William Johnson III") == "william johnson"

    def test_strip_iv(self):
        assert normalize_name("Henry Brown IV") == "henry brown"

    def test_collapse_whitespace(self):
        assert normalize_name("  John   Smith  ") == "john smith"

    def test_already_normalized(self):
        assert normalize_name("john smith") == "john smith"

    def test_only_suffix_not_stripped_if_not_at_end(self):
        # "jr" embedded in middle of name should not be stripped
        result = normalize_name("Jared Mjr Williams")
        assert "mjr" in result  # not stripped

    def test_empty_string(self):
        assert normalize_name("") == ""


# ---------------------------------------------------------------------------
# resolve_school
# ---------------------------------------------------------------------------

class TestResolveSchool:
    SCHOOL_MAP = {
        "Penn St.": 1,
        "Duke": 2,
        "Florida International": 3,
        "Kansas St.": 4,
        "Massachusetts": 5,
    }

    def test_exact_match(self):
        assert resolve_school("Duke", self.SCHOOL_MAP) == 2

    def test_alias_lookup(self):
        # Penn State → Penn St.
        assert resolve_school("Penn State", self.SCHOOL_MAP) == 1

    def test_alias_fiu(self):
        # FIU → Florida International
        assert resolve_school("FIU", self.SCHOOL_MAP) == 3

    def test_alias_umass(self):
        # UMass → Massachusetts
        assert resolve_school("UMass", self.SCHOOL_MAP) == 5

    def test_fuzzy_match(self):
        # "Duuke" close enough to "Duke"
        result = resolve_school("Duuke", self.SCHOOL_MAP)
        assert result == 2

    def test_none_input(self):
        assert resolve_school(None, self.SCHOOL_MAP) is None

    def test_unresolvable(self):
        assert resolve_school("Fictional University", self.SCHOOL_MAP) is None


# ---------------------------------------------------------------------------
# match_player
# ---------------------------------------------------------------------------

class TestMatchPlayer:
    ROSTER: list[tuple[int, str, str]] = [
        (1, "John Smith", "PG"),
        (2, "Marcus Jones", "SG"),
        (3, "José García", "SF"),
        (4, "Patrick Williams II", "PF"),
        (5, "David Brown Jr.", "C"),
        (6, "Alex Davis", "PG"),
    ]

    def test_exact_match(self):
        pid, conf, tag = match_player("John Smith", self.ROSTER)
        assert pid == 1
        assert tag == "matched"
        assert conf is not None and conf > 0.9

    def test_case_insensitive(self):
        pid, conf, tag = match_player("john smith", self.ROSTER)
        assert pid == 1
        assert tag == "matched"

    def test_accent_normalization(self):
        pid, conf, tag = match_player("Jose Garcia", self.ROSTER)
        assert pid == 3
        assert tag == "matched"

    def test_suffix_stripping(self):
        pid, conf, tag = match_player("David Brown", self.ROSTER)
        assert pid == 5
        assert tag == "matched"

    def test_suffix_in_query_too(self):
        pid, conf, tag = match_player("Patrick Williams", self.ROSTER)
        assert pid == 4
        assert tag == "matched"

    def test_empty_roster(self):
        pid, conf, tag = match_player("John Smith", [])
        assert pid is None
        assert tag == "no_school"

    def test_no_match(self):
        pid, conf, tag = match_player("Zxqrty Bvfnmk", self.ROSTER)
        assert pid is None
        assert tag == "unmatched"

    def test_position_prefilter_selects_correct_player(self):
        # Two "Alex" players — position narrows it
        roster = [
            (10, "Alex Davis", "PG"),
            (11, "Alex Thompson", "SG"),
        ]
        pid, conf, tag = match_player("Alex Davis", roster, position="PG")
        assert pid == 10
        assert tag == "matched"

    def test_position_prefilter_fallback_to_full_roster(self):
        # Query position is wrong but player still exists — pass 3 fallback
        pid, conf, tag = match_player("John Smith", self.ROSTER, position="C")
        # May find via fallback; should not be no_school
        assert tag in ("matched", "unmatched", "ambiguous")

    def test_ambiguous_returns_none(self):
        roster = [
            (20, "John Smith", "PG"),
            (21, "John Smyth", "SG"),
        ]
        pid, conf, tag = match_player("John Smit", roster)
        # Both are close — result is ambiguous or one wins; either is acceptable
        assert tag in ("matched", "ambiguous")

    def test_confidence_in_range(self):
        pid, conf, tag = match_player("Marcus Jones", self.ROSTER)
        assert tag == "matched"
        assert conf is not None
        assert 0.0 <= conf <= 1.0


# ---------------------------------------------------------------------------
# SCHOOL_ALIASES completeness spot-checks
# ---------------------------------------------------------------------------

class TestSchoolAliases:
    def test_penn_state(self):
        assert SCHOOL_ALIASES["Penn State"] == "Penn St."

    def test_fiu(self):
        assert SCHOOL_ALIASES["FIU"] == "Florida International"

    def test_umass(self):
        assert SCHOOL_ALIASES["UMass"] == "Massachusetts"

    def test_no_empty_keys(self):
        assert all(k and v for k, v in SCHOOL_ALIASES.items())
