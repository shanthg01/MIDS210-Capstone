# Role Fit / Playing Time Model Plan

This document is intentionally consolidated into the canonical Playing Time / Rotation plan:

```text
docs/models/playing_time_rotation_model_plan.md
```

Role Fit is not a standalone model. It is the coach-facing 0-100 score derived from the
Playing Time / Rotation model's opportunity outputs:

```text
expected_minutes
expected_minutes_share
expected_usage
usage_role
usage_role_confidence
displaced_minutes
minutes_uncertainty
data_quality_flags
```

Implementation work should use `playing_time_rotation_model_plan.md` as the source of truth for:

- training and inference set construction,
- `playing_time_projections` table output,
- Role Fit scoring,
- player/team clustering usage,
- minutes conventions,
- validation requirements,
- downstream contract with destination-adjusted player projections.

Keep this file only as a compatibility pointer for older links.
