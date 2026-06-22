# Frontend Enhancements — Next Iteration

Brainstormed 2026-06-21. Grounded against current backend schema (`UserPreferences`/`UserFilters` in
[`src/portalpoint/api/schemas/user.py`](../../src/portalpoint/api/schemas/user.py)) and current frontend state
(`frontend/src/pages/SettingsPage.tsx`, `frontend/src/components/FitScoreBar.tsx`).

## Customizability (weighting + override/factor specs)

Backend schema for most of this already exists (`UserFilters`) but isn't wired to UI — `SettingsPage.tsx` has a
greyed-out "Phase 2" placeholder for it.

1. **Wire the existing Recruiting Filters panel** — `recruiting_regions`, `conferences`, `positions`,
   `target_archetypes`, `nil_budget_min/max` all exist server-side, unused in UI. Lowest-effort, highest-visibility
   win — replace the greyed box with real multi-selects.
2. **Stat-threshold builder for `min_stats`** — currently a freeform `dict | None`, no shape enforced. Needs real
   UI: pick stat (3PT%, usage%, mins%) + min value, multiple rows. Tighten the schema (typed fields, not raw dict)
   before building UI on top of it — raw dict will rot.
3. **Archetype picker tied to M1 labels** — `target_archetypes` field exists but raw strings; surface
   `ARCHETYPE_LABELS` from `modeling/player_clustering.py` as a real chip-select ("3&D Wing", "Stretch Big", etc.)
   instead of free text.
4. **Hard filter vs soft weight — make the distinction visible.** Filters (`UserFilters`) eliminate candidates;
   weights (`fit_weights`/`importance_weights`) re-rank survivors. Both currently live in Settings with no visual
   separation — worth a UI section break ("Eliminate" vs "Prioritize") so coaches understand they're different
   mechanisms.
5. **Surface roster gap as a suggested target, not blank text entry.** `roster_state_features` already computes
   departing/returning minutes+usage by position — show "Your biggest hole: backup PG minutes" and let user accept
   or override, instead of a coach guessing what gap to fill manually. Connects customization directly to model
   output.
6. **Saved weight profiles** — one global `fit_weights`/`importance_weights` per user today. Real use case: a coach
   scouting a wing vs. filling a specific roster hole wants different weight sets. Named presets ("Wing search,"
   "Backup PG search") > single static slider set.

Note: NIL filter (#1) will sit dead until `nil_valuations` populates (Open Design Question #6 in root `CLAUDE.md`)
— build the UI but flag it as inert, don't let it silently no-op.

## Other enhancements worth pursuing

7. **Radar/spider chart for fit breakdown.** Current `FitScoreBar` is linear bars only — D3 is in the stack per
   `CLAUDE.md` but unused. A 4-component radar reads faster than 4 stacked bars, and the existing
   `LIVE_COMPONENTS`/placeholder distinction should carry into the chart (dashed for stub, solid for live).
8. **Shortlist comparison view.** Shortlist exists (`users.py` get/add/remove) and a separate `ComparePage` exists
   for ad-hoc compares — no path from shortlist → compare selected players directly. Small wiring gap, real
   workflow gap.
9. **Onboarding for first-time weight setup.** Defaults apply silently (`_DEFAULTS` in `users.py`) — a new program
   never sees they're using defaults. A first-login wizard ("set your priorities") turns the customizability
   feature into something users actually discover.

## Suggested sequencing

1 → 4 → 7 first: cheapest backend-ready win, then the conceptual fix that makes the customization model legible,
then the visualization that makes the 4-component score worth having.
