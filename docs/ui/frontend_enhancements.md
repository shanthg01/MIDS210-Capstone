# Frontend Enhancements — Next Iteration

Brainstormed 2026-06-21. Grounded against current backend schema (`UserPreferences`/`UserFilters` in
[`src/portalpoint/api/schemas/user.py`](../../src/portalpoint/api/schemas/user.py)) and current frontend state
(`frontend/src/pages/SettingsPage.tsx`, `frontend/src/components/FitScoreBar.tsx`).

**Status (2026-06-26): 9 of 9 items done.** #9 turned out to already be satisfied on
re-check — see below. **2026-07-15: 13 more items done** (explainability pass — see bottom section),
plus 6 real bugs found and fixed along the way (not new UI, existing behavior that was wrong).

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

---

## Explainability pass, 2026-07-15 — new iteration

Follow-on to the enhancements above, focused on explainability/navigability/transparency for both
technical and non-technical users, plus a run of real data-accuracy bugs surfaced while building it.

### Explainability UX

11. ✅ **Done.** Onboarding modal trimmed — dropped the mechanism-first "fit scores are computed using
    two independent mechanisms" opening; leads with a plain-language goal statement, defers
    mechanism detail to a link into the new Glossary.
12. ✅ **Done.** New `pages/OverviewPage.tsx` (`/overview`, first nav item) and `pages/GlossaryPage.tsx`
    (`/glossary`, last nav item) — landing page + full definitions reference. Both backed by a new
    single-source-of-truth `frontend/src/constants/definitions.ts` (`FIT_COMPONENTS`, `SUB_METRICS`,
    `SKILLS`, `BOX_SCORE`, `GAP_FEATURES`, `HE_PLAY_TYPES`, etc.) so hover tooltips and the Glossary
    page never drift out of sync.
13. ✅ **Done.** New shared `components/DefinitionTooltip.tsx`, wired onto every fit-component label,
    sub-metric bar, box-score stat, and skill percentile across `FitScoreBar.tsx`, `ProjectionCard.tsx`,
    `FitScorePage.tsx`, and `ComparePage.tsx` — generalizes the inline `<Tooltip><Box sx={cursor:help}>`
    pattern that previously existed in only 2 spots.
14. ✅ **Done.** `ProjectionCard.tsx` chunked — headline value/CI always visible, Box Score and Value
    Drivers in bordered sub-sections, Skill Percentiles (11 bars, the density offender) behind an
    accordion.
15. ✅ **Done.** Key-insight summary strips (`utils/fitInsights.ts`, `utils/compareInsights.ts`) —
    plain-language takeaway ahead of the detailed breakdowns on `FitScorePage`, `PlayerProfilePage`,
    and `ComparePage` (the last one as a verdict panel: which player is the stronger fit and why).
16. ✅ **Done.** `RecommendationCard`'s reasoning line upgraded from a flat stat callout ("Strongest in
    scheme fit (91/100)") to a tiered verdict + standout reason ("Excellent overall fit — stands out
    most in scheme fit (91/100)."), generated server-side in `_build_reasoning()`.

### Real bugs found building the above (not just new UI)

17. ✅ **Fixed.** `ComparePage.tsx`'s header row (`bgcolor: 'grey.50'`) rendered near-invisible —
    `grey.50` isn't redefined in the app's dark palette, so it silently overrode the theme's own
    `MuiTableHead` styling.
18. ✅ **Fixed.** `Projected Box Score` on player deep dives was empty for most players even under the
    "Live" forecast model — Gap C's rate-projection sub-model only produces output for a small
    fraction of forecast rows (root cause not fully pinned to one line, but the fallback-to-empty
    behavior is by design, not a crash).
19. ✅ **Fixed.** Destination-adjusted projection was never called from the frontend — `FitScorePage`
    used the neutral endpoint despite being school-specific everywhere else. Swapped to
    `getPlayerProjection(playerId, schoolId)`; found and fixed a second bug this surfaced —
    destination mode's `projected_box_score` uses `_per_game` keys (real expected-minutes basis),
    not neutral's `_per_40` keys, so the box score silently rendered empty under the new mode too
    until `ProjectionCard.tsx` learned both label conventions.
20. ✅ **Fixed.** Role Fit's Live/Placeholder chip was a blanket flag (`LIVE_COMPONENTS` Set) —
    always "Placeholder" regardless of whether `run_playing_time.py` had actually synced that row.
    New `isComponentLive(component, modelVersion)` checks `fit.model_version` per response.
21. ✅ **Fixed.** Program Fit's 5 sub-bars (NIL/Geographic/Academic/Cultural/NIL Budget) were fully
    fabricated per-request random numbers with no Placeholder marker distinguishing them from real
    data. Removed; replaced with one shared honest description reused across `FitScorePage`,
    `ComparePage`'s tooltip, and a new `SettingsPage` caption.
22. ✅ **Fixed.** Scheme Fit's "Ball Movement Match" sub-bar was mislabeled — it's `mid_range_rate`'s
    match score, no ball-movement signal exists in the model. Renamed to "Mid-Range Match"; the
    always-fake "Usage Match" bar was removed entirely; a real, previously-uncaptured signal
    (`he_scheme_fit`, 6-dim HoopExplorer play-type cosine) is now surfaced as its own "Play Type
    Match" group. Full record: `docs/status/STATUS.md` 2026-07-15 entries, `docs/status/MODEL_STATUS.md`
    M3 section.

### Backend, same pass

23. ✅ **Done.** `/api/recommendations` wired to the real 2-stage engine — was a hardcoded stub since
    the original scaffold. See `docs/models/recommendation_engine_plan.md` §8.
