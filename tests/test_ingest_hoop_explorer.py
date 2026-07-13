"""Unit tests for ingest_hoop_explorer.py pure matching functions.

No DB or network required — all tests use synthetic inputs. Covers the Issue #53
fix: token-wise fuzzy matching (vs whole-string) and the in-batch player_id
collision guard, both aimed at preventing two different real players from
resolving to the same player_id for a season.
"""
from __future__ import annotations

from scripts.ingest_hoop_explorer import (
    _fuzzy_match_safe,
    _name_tokens,
    _tokenwise_ratio,
)


# ---------------------------------------------------------------------------
# _name_tokens
# ---------------------------------------------------------------------------

class TestNameTokens:
    def test_two_part_name(self):
        assert _name_tokens("tj johnson") == ("tj", "johnson")

    def test_single_token_name(self):
        assert _name_tokens("prince") == ("prince", "prince")

    def test_multi_part_name_uses_first_and_last(self):
        assert _name_tokens("jean pierre de la cruz") == ("jean", "cruz")

    def test_empty_string(self):
        assert _name_tokens("") == ("", "")

    def test_strips_jr_suffix_before_taking_last_token(self):
        # Real bug found live during Issue #53's fix rollout: "D.J. Burns Jr." and
        # "D.J. Stewart Jr." both tokenized to ('d.j.', 'jr.') before this fix,
        # because the naive last-token pick grabbed the suffix instead of the
        # real last name — collapsing two different real players onto one score.
        assert _name_tokens("d.j. burns jr.") == ("d.j.", "burns")

    def test_strips_various_suffixes(self):
        assert _name_tokens("john smith sr.") == ("john", "smith")
        assert _name_tokens("john smith ii") == ("john", "smith")
        assert _name_tokens("john smith iii") == ("john", "smith")

    def test_suffix_only_stripped_when_multiple_tokens_remain(self):
        # A bare "jr" with nothing else shouldn't be stripped down to empty.
        assert _name_tokens("jr") == ("jr", "jr")


# ---------------------------------------------------------------------------
# _tokenwise_ratio — the Issue #53 fix
# ---------------------------------------------------------------------------

class TestTokenwiseRatio:
    def test_identical_names_score_one(self):
        assert _tokenwise_ratio("tj johnson", "tj johnson") == 1.0

    def test_different_first_initial_same_last_name_scores_low(self):
        # Real Issue #53 collision: whole-string difflib ratio for these two is
        # ~0.90 (masked by the shared "johnson" tail), but they are different
        # real players. Token-wise ratio must expose the low first-name match.
        ratio = _tokenwise_ratio("tj johnson", "rj johnson")
        assert ratio < 0.6

    def test_same_first_name_different_last_name_scores_low(self):
        # Real Issue #53 collision #2: "Matthew Mayer" vs "Matthew Moyer" —
        # whole-string ratio ~0.92 despite the last-name token only being ~0.8.
        ratio = _tokenwise_ratio("matthew mayer", "matthew moyer")
        assert ratio < 0.85

    def test_minor_typo_in_last_name_still_scores_reasonably_high(self):
        ratio = _tokenwise_ratio("john smith", "john smithe")
        assert ratio > 0.85

    def test_different_players_sharing_a_suffix_score_low(self):
        # The live Issue #53 rollout bug: before suffix-stripping, both names
        # tokenized to ('d.j.', 'jr.') and scored a false 1.0.
        ratio = _tokenwise_ratio("d.j. burns jr.", "d.j. stewart jr.")
        assert ratio < 0.7


# ---------------------------------------------------------------------------
# _fuzzy_match_safe — token-wise threshold + ambiguity guard
# ---------------------------------------------------------------------------

class TestFuzzyMatchSafe:
    def test_no_candidates_returns_none(self):
        assert _fuzzy_match_safe("tj johnson", [], threshold=0.88) is None

    def test_rejects_different_first_initial_same_last_name(self):
        # This is the exact Issue #53 failure mode the old _fuzzy_match (whole-string)
        # let through at threshold=0.88.
        result = _fuzzy_match_safe("tj johnson", ["rj johnson"], threshold=0.88)
        assert result is None

    def test_rejects_same_first_name_different_last_name(self):
        result = _fuzzy_match_safe("matthew mayer", ["matthew moyer"], threshold=0.88)
        assert result is None

    def test_accepts_genuine_typo(self):
        result = _fuzzy_match_safe("jon smith", ["john smith"], threshold=0.85)
        assert result == "john smith"

    def test_rejects_ambiguous_tie(self):
        # Two candidates score identically (same first-name-token ratio, exact
        # last-name match on both) — too close to call, refuse to guess.
        result = _fuzzy_match_safe(
            "jon smith", ["jan smith", "jen smith"], threshold=0.60
        )
        assert result is None

    def test_picks_clear_winner_when_not_ambiguous(self):
        result = _fuzzy_match_safe(
            "john smith", ["john smith", "someone else entirely"], threshold=0.85
        )
        assert result == "john smith"

    def test_below_threshold_returns_none(self):
        result = _fuzzy_match_safe("john smith", ["mary jones"], threshold=0.85)
        assert result is None
