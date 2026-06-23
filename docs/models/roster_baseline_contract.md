# Roster Baseline Contract

**Status:** Implemented in code on `roster-baseline` branch. Stored model rows must be regenerated before production data reflects this contract.

## Purpose

The roster baseline answers:

> Who counts as being on a school's roster outlook when a roster-aware model evaluates need, opportunity, or team strength?

In the current implementation, this is a **feature-bearing modeling baseline**:
players must have a usable player-season feature row before they can affect
Gap Matching, Role Fit, or Team Rating Projection. It is not guaranteed to be a
complete public roster.

It does **not** answer:

> Which players are available/recommendable transfer candidates?

Candidate availability remains owned by `portalpoint.modeling.availability` and `transfer_portal_events`.

## Source Policy

Use one shared modeling layer, `portalpoint.modeling.roster_baseline`, instead of duplicating roster-membership logic inside individual models.

Historical seasons where `S + 1` exists:

- Source: `player_season_stats`.
- If a player appears at a school in `S + 1` and has a player feature row in `S`, that player counts in that school's roster baseline for cycle `S`.
- Same school in `S` and `S + 1` => `returning`.
- Different school in `S + 1` => `changed_school_next_season`.
- Absent in `S + 1` => not included in baseline.

Latest season where `S + 1` does not exist:

- Source: latest `roster_snapshots` / `roster_snapshot_players` when available.
- Snapshot ingest must use BartTorvik `rostercast.php` team-name aliases where
  the DB `schools.name` differs from the source-site parameter (`UConn` ->
  `Connecticut`, `Ole Miss` -> `Mississippi`, etc.).
- Snapshot ingest does not global-name-match freshman rows to players from
  other schools. Those rows remain raw roster members with `player_id = NULL`
  and `returning_status = 'new'` until a proper identity/stat/depth-prior path
  exists.
- Snapshot fuzzy matching requires the raw roster name and candidate player
  name to share first-name and last-name initials before `difflib` scoring.
  This blocks obvious same-last-name or similar-name collisions while preserving
  exact returning/transfer-in matches.
- Matched snapshot players count in the roster baseline.
- Incoming freshmen or other unmatched snapshot players are left out of the
  feature-bearing baseline until a depth-only prior exists.
- If a school has no usable snapshot, fallback to same-season
  `player_season_stats` minus explicit departures from `transfers`, Hoop
  Explorer `transfer_dest = 'NBA'`, and likely senior/graduate eligibility
  exits from `players.class_year` / Hoop Explorer `year_class`.
- The no-usable-snapshot fallback is lower confidence because it cannot add
  incoming freshmen/transfers; run `scripts/ingest_roster_snapshots.py` before
  rerunning roster-aware models.
- Future work should include new/unmatched snapshot players as depth-only priors
  with lower confidence so public roster size and projected depth are represented
  without attaching the wrong historical stat line.

## Direct Consumers

- Gap Matching: builds school gap vectors from this baseline.
- Role Fit / Playing Time: should estimate available minutes, role crowding, and displaced usage from this baseline.
- Team Rating Projection: should start from this baseline before adding/removing candidate scenarios.

## Indirect Consumers

- Destination-adjusted player projection, through role/minutes and team context.
- Recommendation Engine, through gap/role/team-impact outputs plus separate availability filtering.

## Non-Consumers

- Player clustering.
- Team system clustering.
- Scheme Fit.
- Program Fit.
- Neutral player projection.

## API Semantics

`FitScoreResponse` exposes two related but different flags:

- `is_current_school`: raw `player_season_stats` says the player has a row for that school and season.
- `is_roster_baseline_member`: the player counts in the shared roster baseline for that school and season.

These can differ. A player can have a stale same-school stats row but be absent from the latest roster outlook, so `is_current_school = true` and `is_roster_baseline_member = false`.
