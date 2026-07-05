# Frontend Enhancements — Next Iteration

Brainstormed 2026-06-21. Grounded against current backend schema (`UserPreferences`/`UserFilters` in
[`src/portalpoint/api/schemas/user.py`](../../src/portalpoint/api/schemas/user.py)) and current frontend state
(`frontend/src/pages/SettingsPage.tsx`, `frontend/src/components/FitScoreBar.tsx`).

**Status (2026-06-26): 9 of 9 items done.** #9 turned out to already be satisfied on
re-check — see below.

## Customizability (weighting + override/factor specs)

Backend schema for most of this already exists (`UserFilters`) but isn't wired to UI — `SettingsPage.tsx` has a
greyed-out "Phase 2" placeholder for it.

1. ✅ **Done.** Recruiting Filters panel wired — `recruiting_regions`, `conferences`, `positions`,
   `target_archetypes`, `nil_budget_min/max` all live in `SettingsPage.tsx` as real multi-selects.
2. ✅ **Done.** `min_stats` is now a typed `list[StatThreshold]` (`StatKey` enum in `schemas/user.py`), with a
   repeatable stat+min-value row builder in Settings — and actually wired into `GET /players/search`'s `min_stat`
   query params (via `PlayerSearchPage.tsx`), not just stored inert.
3. ✅ **Done** (as a side effect of #1) — `target_archetypes` uses the real `ARCHETYPE_LABELS` chip-select, not
   free text.
4. ✅ **Done.** "Prioritize" (info-blue, Tune icon) vs "Eliminate" (warning-orange, Filter icon) section headers +
   color-accented borders in `SettingsPage.tsx`.
5. ✅ **Done.** New `GET /api/schools/roster-gap` endpoint (first read consumer of `roster_state_features`) surfaces
   the caller's biggest open-minutes position as a one-click "Add to filters" suggestion in Settings.
6. ✅ **Done.** New `user_preference_profiles` table + CRUD/activate endpoints; dropdown switcher + "Save current
   as new…" in Settings. Activating a profile copies its fields into the existing single `UserPreference` row —
   zero changes to `fit_scores.py`'s consumption of it.

Note: NIL filter (#1) still sits dead until `nil_valuations` populates (Open Design Question #6 in root
`CLAUDE.md`) — built, flagged inert in the UI, not hidden.

## Other enhancements worth pursuing

7. ✅ **Done.** `ProjectionCard.tsx` on `PlayerProfilePage.tsx` — value/100 + CI, per-40 box score, skill percentile
   bars, top value-driver chips, via the new `GET /players/{id}/projection` client.
8. ✅ **Done.** Hand-rolled SVG `FitRadarChart.tsx` (skipped D3 — overkill for a static 4-axis polygon) wired into
   `FitScorePage.tsx`'s `OverallPanel`, alongside the existing numeric grid. Live/stub distinction carried via
   filled vs. open vertex markers.
9. ✅ **Already done — original claim was stale.** Re-checked the actual code: `ComparePage.tsx` already calls
   `getShortlist()` directly and lets you pick 2-4 players from it to compare (`togglePlayer`/`handleCompare`,
   `frontend/src/pages/ComparePage.tsx`). The "no path from shortlist → compare" gap described here didn't
   reflect the current codebase. Minor remaining UX nit (not a gap): `PipelinePage.tsx` has no compare-selection
   UI of its own, so picking happens on the Compare page, not carried over from Pipeline — left as-is, not worth
   building for the duplication it'd remove.
10. ✅ **Done.** `OnboardingWizard.tsx` — fires once per browser per user (`pp_onboarded_<userId>` in localStorage),
    mounted in `AppLayout.tsx`. Surfaces the Prioritize/Eliminate split plus live default weights, "Customize now"
    jumps to Settings.

## Suggested sequencing (historical)

Built in this order: 1 → 7 → 4 → 8 → 10 → 2 → 5 → 6. Items 2/5/6 needed real backend work (typed schema, new
`schools.py` router, new table + migration) — see the implementation plan this was built from for the full
backend design.
