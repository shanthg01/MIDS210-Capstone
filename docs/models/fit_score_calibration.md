# Fit Score Calibration (`fit-cal-v1`)

## Canonical score

PortalPoint first computes one non-personalized weighted composite:

```text
weighted_composite =
    0.25 * scheme_fit
  + 0.30 * gap_match
  + 0.25 * role_fit
  + 0.20 * team_impact_fit
```

Program Fit is descoped and is not displayed or aggregated. Team Impact is
the Team Rating Projection model's `delta_adj_em` signal.

The weighted composite determines ranking. It is then passed through the same
school-relative normal-score calibration to produce the displayed and
persisted `overall_fit`. This prevents the average of four components from
collapsing back into a narrow band while preserving the weighted ordering.

User Settings do not redefine `overall_fit`. They produce a separately named
`personalized_fit`, so a player retains the same canonical score across users.

## Shared component scale

The raw component models have incompatible distributions. For each destination
school, portal candidates form the calibration reference population. Average
empirical ranks (ties receive the same rank) are mapped through a normal-score
transform:

```text
calibrated = clip(50 + 20 * normal_inverse_cdf(percentile), 10, 90)
```

Interpretation:

| Candidate percentile | Calibrated score |
|---:|---:|
| 5th | 17 |
| 25th | 37 |
| 50th | 50 |
| 75th | 63 |
| 95th | 83 |

Thus, 50 means an average available candidate for that destination school. The
calibration is not position-specific; Gap Match and Role Fit already model
position and roster context.

Gap v4 previously shrank uncertainty toward 15. `fit-cal-v1` calibrates its
stored `raw_gap_match` and applies confidence once, toward neutral 50.

## Confidence and missing data

Each component has a confidence in `[0, 1]`. Uncertain signals are shrunk toward
neutral rather than treated as poor fit:

```text
confidence_adjusted = 50 + confidence * (calibrated - 50)
```

`overall_confidence` is the canonical weighted average of component
confidences. The API also returns component confidences and explicit data-
quality flags for stale Scheme Fit, low-confidence Gap Match, missing Role
projection, and missing Team Rating projection.

## Persistence and run order

Raw `scheme_fit`, `gap_match`, and `role_fit` remain unchanged for diagnostics.
The calibration job writes additive calibrated columns, Team Impact,
`overall_fit`, confidence metadata, and `calibration_version`.

```bash
uv run python scripts/run_fit_calibration.py --season 2027 --dry-run
uv run python scripts/run_fit_calibration.py --season 2027
```

Run calibration after Scheme Fit, Gap Matching, Playing Time, and Team Rating
Projection. Deploying the migration/code and running a shared database backfill
are deliberately separate operational steps.
