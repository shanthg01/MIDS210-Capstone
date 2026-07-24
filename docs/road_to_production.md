# Road to Production Roadmap

**Status:** Draft, 2026-07-13. Written to contextualize `docs/production_db_connectivity_plan.md`
against every other productionalization workstream (hosting, secrets, CI/CD, monitoring, scheduled
jobs) so DB connectivity isn't solved in isolation from things that depend on the same decisions
(VPC layout, secrets store, ECS).

**Sources reconciled:** `docs/production_db_connectivity_plan.md`, `docs/status/ARCHITECTURE_STATUS.md`,
`docs/status/APPLICATION_STATUS.md`, CLAUDE.md (Process Improvement TODOs, tech stack, MVP criteria),
`.github/workflows/test.yml` (CI already exists — PR-gated pytest against Postgres+Redis service
containers), `docker-compose.yml` (no app Dockerfile yet, local-db profile only).

---

## Phase 0 — Where we are today

| Layer | State |
|---|---|
| DB | Shared AWS RDS Postgres 15, VPC-internal, reached from dev laptops via AWS SSM Session Manager port-forwarding (migrated off the SSH bastion tunnel 2026-07-20 — SSH port 22 closed entirely) |
| Backend | Live on ECS Fargate behind an ALB (`portalpoint-prod` cluster, `portalpoint-backend` service) — health-checked on a real DB-aware `/ready`, not `/health`. No autoscaling policy yet; CORS still pointed at local dev origins (Phase 4 landed, but the CORS finalization sub-item was not revisited) |
| Frontend | Live at **https://d331zwrxbrp79d.cloudfront.net** — static build in S3 (`portalpoint-frontend`) behind CloudFront (`E2HF7HKH8Y1FKD`), OAC-only bucket access, `/api/*` routed to the ALB at the CDN layer. Deploy is still a manual `npm run build` + `aws s3 sync` + invalidation — no CI step yet |
| Secrets | `DATABASE_URL`/`JWT_SECRET` now in AWS Secrets Manager; Tavily/Gemini keys still deferred (pending news-monitoring PR merge) |
| CI | GitHub Actions `pull_request` pytest gate exists; `deploy.yml` builds/pushes to ECR via OIDC and force-redeploys ECS on merge to `main`. No equivalent frontend CI job |
| Scheduled jobs | None running on a schedule; all ingest/model scripts run manually per dev — **explicitly deferred (Phase 5 skipped 2026-07-20 by decision, not forgotten)** |
| MLflow | Local SQLite `mlruns.db` per dev; artifacts to shared S3 |
| Monitoring | One real alarm: CloudWatch on ALB `UnHealthyHostCount` → SNS (Phase 6 item 2, done 2026-07-20). No Prometheus/Grafana/Sentry/drift detection — deliberately deferred, not started |
| Program Fit / NCAA-FERPA legal review | Not started (CLAUDE.md Open Design Question #2) |

Nothing here is deployed. "Production" currently means "shared RDS + shared S3," not a running service.

---

## Phase 1 — Foundation (blocks everything below)

**Status (2026-07-20): mostly done.** Private subnets + NAT gateway + S3 gateway endpoint created,
ECR repo + working multi-stage `Dockerfile` built and verified locally, Secrets Manager holds
`DATABASE_URL`/`JWT_SECRET`, GitHub Actions OIDC deploy role + `deploy.yml` wired up (builds/pushes to
ECR on merge to `main`). Not done: Tavily/Gemini secrets (deferred pending news-monitoring PR), and the
ECS deploy step in `deploy.yml` is deliberately commented out (no cluster/service exists — that's
Phase 3). Full command log: `docs/production_deployment_commands.md`.

These decisions are shared inputs to DB connectivity, backend hosting, and scheduled jobs alike —
sequence them first so later phases aren't redone.

1. **Pick compute target.** CLAUDE.md's tech stack says ECS Fargate; `production_db_connectivity_plan.md`
   assumes this too. Confirm before writing Dockerfiles/task defs — no competing option is currently
   in a doc, so treat as decided unless you want to revisit.
2. **VPC/subnet layout** (open item in the DB plan): private subnets + NAT vs. fully-private with VPC
   endpoints for S3/ECR. This decision also determines how GitHub Actions-triggered scheduled jobs
   reach RDS (Phase 5) and whether the bastion is kept at all (Phase 2). ✅ Done — private subnets +
   NAT gateway + S3 gateway endpoint (chose NAT over fully-private-with-endpoints for cost/simplicity
   at this scale).
3. **Secrets store**: AWS Secrets Manager vs. SSM Parameter Store (open item in the DB plan). Same
   store should back `DATABASE_URL`, `JWT_SECRET`, AWS creds, and (later) `TAVILY_API_KEY`/Gemini key
   used by the news-monitoring agent — decide once, reuse everywhere, don't let ECS and GitHub
   Actions cron end up on two different secret-injection mechanisms. ✅ Decided — Secrets Manager
   (native RDS rotation support, trivial per-secret cost at this scale). `DATABASE_URL`/`JWT_SECRET`
   created; Tavily/Gemini deferred.
4. **Containerize the app.** No Dockerfile exists yet for backend or frontend. Needed before ECS
   Fargate is possible at all — write `Dockerfile` (backend, FastAPI/uvicorn) and confirm frontend
   build output (Phase 4 decides where it's served from). ✅ Backend `Dockerfile` done, multi-stage,
   built and verified against the real RDS instance locally. One real bug found along the way:
   `player_projection.py` resolves `find_repo_root()` at import time (transitively imported by
   `players.py`), which needs `pyproject.toml` present in the image even though it's otherwise unused
   at runtime — fixed by copying it into the runtime stage. Frontend Dockerfile not yet started
   (Phase 4).
5. **CI deploy step.** `test.yml` already runs pytest on PRs — extend it (or add a second workflow)
   to build/push the backend image to ECR on merge to `main`, gated on the same test job. ✅ Done —
   `deploy.yml`, OIDC role-based (no long-lived AWS keys in GitHub secrets), `test.yml` reused via
   `workflow_call` so a broken build never reaches ECR.

---

## Phase 2 — DB connectivity (the existing plan, sequenced in)

This is `docs/production_db_connectivity_plan.md` verbatim, placed in context: it depends on Phase 1's
VPC and secrets decisions, and it's a prerequisite for Phase 3 (backend can't run in ECS talking
directly to RDS until the SG/subnet work here is done).

**Status (2026-07-20): all 6 items done.** Items 2 (SG scoping), 5 (Multi-AZ), and 6 (bastion
break-glass only, SSH closed) landed first. Item 3 (secrets) done for `DATABASE_URL`/`JWT_SECRET`;
Tavily/Gemini deferred (see Phase 1). Items 1 (ECS Fargate, no tunnel in the request path) and 4
(`/ready`) landed with Phase 3 — see that section for a real near-miss caught before this could be
called done. Real finding along the way: the bastion's SSH port was open to `0.0.0.0/0` — the whole
internet, not just per-teammate — closed as part of item 6, not merely narrowed.

1. ECS Fargate tasks in the same VPC/private subnets as RDS — no bastion in the request path.
2. RDS security group scoped to the ECS task SG only (not `0.0.0.0/0`, not full VPC CIDR).
3. `DATABASE_URL`/`JWT_SECRET`/AWS creds move to the Phase 1 secrets store, injected as ECS task
   env vars.
4. Add `/ready` (real `SELECT 1` check) — wire into ECS task health check + ALB target group health
   check, not `/health`. This is the direct fix for the 2026-07-13 incident.
5. Multi-AZ RDS.
6. Bastion retained for ops break-glass only (ideally replaced by SSM Session Manager — no open SSH
   port, no `.pem` in the repo root).

Open items already flagged in that doc (VPC layout, Secrets Manager vs SSM, `/ready` failure
semantics, CloudWatch alarm routing) are Phase 1/6 dependencies — resolve there, not independently.

**Follow-up (2026-07-24): item 5's instance size walked back.** Cost Explorer flagged RDS as the
dominant line item ($115.34 MTD, $238.79 forecasted). `portalpoint-db` had been upsized to
`db.r6g.large` + Multi-AZ alongside the Multi-AZ work above, for a real reason at the time —
CloudWatch `FreeableMemory` before 2026-07-20 showed the prior instance genuinely memory-starved
(~100-200MB free). But two unrelated fixes landed after that upsize and removed load from the DB
engine itself without the instance size being revisited: the in-VPC ECS execution path for heavy
modeling scripts (bypassed the SSM tunnel's ~0.76 MB/s bandwidth ceiling, which had been the real
batch-job bottleneck, not DB compute) and several bulk-upsert fixes (`execute_values`/
`execute_batch`, `COPY`+bulk `UPDATE...FROM`) that cut DB-side round-trips for the large
M3/Gap Matching/M4/M5/M6 writes. A fresh 14-day CloudWatch check (CPU avg 2.5-6%, brief spikes to
30-62% never sustained; ~11GB/16GB memory free even during active-query windows; max 5-14 DB
connections in nearly every hourly window) confirmed the instance was comfortably idle relative to
its size. Downsized to `db.m6g.large` (general-purpose, non-burstable — avoids burst-credit
exhaustion risk from batch jobs that a `t4g` class would carry) and reverted Multi-AZ to
Single-AZ (accepted: no real production SLA yet, so losing automatic AZ-failover is a reasonable
tradeoff for the cost cut). Applied and verified live — `Class: db.m6g.large`, `MultiAZ: False`,
`Status: available`. Full before/after evidence and reasoning: see memory
`rds_rightsizing_2026_07_24.md` (or ask Claude Code to recall it). Not yet done: a few more days of
`m6g.large` CloudWatch data before considering `t4g` for further savings.

---

## Phase 3 — Backend hosting

**Status (2026-07-20): items 1-2 done, with a real near-miss caught before calling it complete.**
The target group's health check was pointed at `/ready` per the plan — but `/ready` had never
actually been built despite being flagged as the direct fix for the original incident since
`docs/production_db_connectivity_plan.md` was written. If the ECS service had gone live without
catching this, every task would have failed health checks (404) and cycled indefinitely. Added a real
`SELECT 1`-backed `/ready` to `main.py`, rebuilt/pushed the image, force-redeployed. `deploy.yml`'s
ECS `update-service` step is now uncommented (was deliberately deferred in Phase 1 until the cluster/
service existed). Items 3 (autoscaling) and 4 (CORS finalization) not started — item 4 is genuinely
blocked on Phase 4's frontend URL; item 3 is just not done yet.

1. ✅ ECS Fargate service behind an ALB, task definition pulling the Phase 1 image from ECR.
2. ✅ Target group health check → `/ready` (Phase 2 item 4) — real endpoint now exists, not just
   configured as a health-check path.
3. Autoscaling policy — even a minimal one (CPU-based, min=1/max=2) beats a single uvicorn process
   with no restart-on-crash behavior beyond ECS's own task replacement. Not started.
4. CORS/allowed-origins config needs the Phase 4 frontend URL decided before this can be finalized.
   Not started.

**Post-launch incident, same day (2026-07-20):** first real production incident, found by manually
testing the live site rather than by the CloudWatch alarm (the task was healthy the whole time — this
wasn't an uptime problem). Two real, separate bugs: (1) `alembic upgrade head` had never actually been
run against RDS after the Phase-3-adjacent `origin/main` merge landed 12 new migrations in code — broke
login/signup (`users.is_admin` didn't exist yet). Running it hit a second gotcha — some tables (this
time `playing_time_projections`) are owned by a DB user other than `portalpoint_app`, so DDL needs
`portalpoint_master`, a recurring pattern already flagged once before (`coaches` table, 2026-07-16 —
see `ARCHITECTURE_STATUS.md`). (2) Dashboard/FitScorePage/Compare hung 5+ minutes — a missing index
let `SELECT MAX(season) FROM player_team_fit_scores` run as a genuine ~200-300s full scan on every
request, and it piled up 15+ concurrent copies of itself because the query's Redis cache was never
actually reachable in production (`REDIS_URL` missing from `task-def.json` — silent fail-open, not an
error). Fixed with a new indexed migration (`b3f8e21a6c94`); Redis itself deliberately left broken at
this point, deferred pending a scope decision (fail-open code change vs. real ElastiCache). **Real gap
this surfaced:** `deploy.yml` doesn't run `alembic upgrade head` as part of deployment — still a manual
step nobody remembered this time. Full trail in `docs/status/STATUS.md`'s 2026-07-20 incident entry.

**PR #65 review + Redis resolved, 2026-07-21:** review turned up two more real bugs (`uv.lock`
gitignored and never committed, breaking Docker builds on any fresh clone/CI; `deploy.yml`'s missing
migration step, fixed with a pre-deploy ECS one-off `alembic upgrade head` task that must succeed before
the service updates) — both fixed. On Redis: chose real ElastiCache over the fail-open alternative.
Once live, fixing it required three more real, sequential bugs found by actually trying to use the
news-monitoring agent's "Run Now" button: a missing `is_admin` grant (403, invisible in the UI), missing
`TAVILY_API_KEY`/`GOOGLE_API_KEY` secrets (deferred since Phase 1, never circled back — silent non-
exception failure, nothing in CloudWatch), and a real bug conflating "the pipeline crashed" with "the
pipeline correctly declined to guess at an ambiguous player match" under one `errors`/`success=False`
signal — split into `errors` vs. a new `review_needed` category so the UI stops saying "Run failed" for
a run that actually worked. Full trail in `docs/status/STATUS.md`'s 2026-07-20/07-21 entries.

---

## Phase 4 — Frontend hosting

**Live URL: https://d331zwrxbrp79d.cloudfront.net**

**Status (2026-07-20): items 1-2 done manually; item 3 (CI) not started; item 4 not revisited.**
S3+CloudFront chosen (matches CLAUDE.md's AWS-only cloud column). Real build-fix prerequisite found
along the way: `npm run build` had never been run before this work — `npm run dev` doesn't invoke
`tsc`, so 92 pre-existing TypeScript errors across 17 files surfaced for the first time (all fixed;
see the frontend-build-fix commits). No `VITE_API_BASE_URL` env var was needed in the end — CloudFront
routes `/api/*` to the ALB at the CDN layer, so the frontend keeps calling relative `/api/*` paths
unchanged, same as local dev.

1. ✅ S3+CloudFront. Bucket `portalpoint-frontend` (private, `BlockPublicAcls` etc., no public bucket
   policy — access is via Origin Access Control only, scoped to the specific CloudFront distribution's
   ARN in the bucket policy condition). Distribution `E2HF7HKH8Y1FKD`: default cache behavior serves S3
   (`CachingOptimized`), `/api/*` behavior forwards to the ALB origin (`CachingDisabled` +
   `AllViewerExceptHostHeader` so the JWT `Authorization` header reaches the API). `CustomErrorResponses`
   reroute 403/404 → `/index.html` (200) for React Router client-side routes.
2. ✅ No env injection needed — see above. (Originally planned as `VITE_API_BASE_URL`; turned out
   unnecessary once `/api/*` routing lived at the CDN layer instead of the frontend config.)
3. CI frontend build+deploy step — **not started.** Deploys today are a manual
   `npm run build` → `aws s3 sync dist/ s3://portalpoint-frontend --delete` →
   `aws cloudfront create-invalidation` sequence. Real gap: nothing catches a future `tsc` regression
   before it reaches this manual step.
4. Frontend gaps from `APPLICATION_STATUS.md`/CLAUDE.md TODO #11 — **not revisited.** Still open,
   unrelated to hosting being live.

---

## Phase 5 — Scheduled jobs / ML pipeline in production

**Status (2026-07-20): explicitly skipped, by decision — not forgotten, not blocked.** Nothing
currently depends on automated freshness: ingest/model reruns are manual and working, and there are no
beta users waiting on next-day-fresh recommendations. Revisit when either (a) a real user needs
fresher-than-manual data, or (b) the news-monitoring agent needs to run continuously during the portal
window (CLAUDE.md: March-August — currently in that window, so this is the one candidate worth a
second look before the others).

**Update (2026-07-23): the manual/on-demand half of this got built for real** (`scripts/run_in_ecs.sh`,
`docs/production_deployment_commands.md` Phase 4b) — running `run_playing_time.py`'s remaining schools
as a one-off ECS Fargate task, not scheduled, but proving the exact execution path (image, roles,
network, MLflow persistence via EFS) that scheduled jobs would also need. Real motivation: a local SSM
tunnel has a hard ~0.6-0.8 MB/s throughput ceiling that made a heavy batch script take 2.3-2.6+ hours
per 75-school chunk; the same chunk in-VPC took ~25-30 minutes. Item 2 below (DB access path for
scheduled jobs) is now half-answered for the ECS-task variant — GitHub Actions runners specifically
still can't reach RDS directly, that part remains open. Item 4 (shared MLflow store) also has a working
answer now (EFS-mounted SQLite), just not yet the "hosted server" version this item originally
envisioned — fine for today's single-writer-at-a-time usage.

1. CLAUDE.md's stated preference: GitHub Actions cron over Airflow until orchestration complexity
   justifies it (`daily_data_ingestion_dag`, `hourly_portal_monitoring_dag`, `weekly_model_training_dag`
   are all currently just docstring-level DAG names, not implemented).
2. Scheduled jobs need the same DB access path as ECS (Phase 2) — GitHub Actions runners are outside
   the VPC, same as dev laptops today, so this needs either a runner-side tunnel (undesirable — same
   footgun as the incident that started this) or a narrowly-scoped public RDS proxy/endpoint. Decide
   this alongside Phase 1's VPC layout, don't bolt it on after.
3. `run_news_monitoring.py` (news-monitoring agent) is the first real candidate for scheduled
   execution — it already has a schedule-agnostic CLI built for this.
4. MLflow tracking metadata is local SQLite per dev today — move to a shared hosted store (Postgres-
   backed MLflow server, could reuse RDS) before any scheduled retrain job needs to log runs
   centrally (CLAUDE.md Architecture Open Question #4).

---

## Phase 6 — Observability

**Status (2026-07-20): item 2 done (the cheap, high-value piece); items 1/3/4 explicitly skipped.**

1. Real DB-aware `/ready` (Phase 2) is the immediate fix for blind-spot monitoring, but CLAUDE.md's
   own design decision ("KS-test feature drift daily, alert if RMSE > baseline × 1.2") has never been
   implemented for any of the 9 models (CLAUDE.md Process Improvement TODO #8) — no Prometheus/Grafana
   exists in the repo despite being named in the tech stack table. Skipped, unchanged.
2. ✅ CloudWatch alarm (`portalpoint-unhealthy-targets`) on the target group's `UnHealthyHostCount`
   (`GreaterThanOrEqualToThreshold 1`, 2 evaluation periods) → SNS topic `portalpoint-alerts` → email
   subscription. This is the direct automated signal the original incident lacked — no more relying on
   a human noticing.
3. Sentry — skipped, not wired. Cheap to add later; not done now.
4. Full Prometheus/Grafana + per-model drift monitoring — skipped, matches CLAUDE.md's own stated
   reasoning (needs infra that doesn't exist yet, lower priority pre-beta).

---

## Phase 7 — Security / compliance gate

Must clear before onboarding real program users, independent of infra readiness:

1. NCAA/FERPA compliance review for player data (CLAUDE.md Open Design Question #2) — legal, not
   engineering, but blocks beta regardless of how ready the infra is.
2. Confirm `portalpoint-bastion.pem` and `pp_midsommer2026!` (currently in CLAUDE.md/docs in plaintext
   for dev convenience) are rotated/removed from any doc that becomes public once this repo or its
   docs are shared beyond the team.
3. Audit log (`audit_log` table exists in schema) — confirm it's actually written to before beta, not
   just modeled.

---

## Phase 8 — Beta launch checklist

Per CLAUDE.md's MVP Success Criteria — this is the acceptance gate the phases above are building
toward, not a new workstream:

- [ ] 2,500+ portal players in DB with complete stats (already met — ~4,083 players)
- [ ] All core endpoints < 500ms (needs real hosting, Phase 3, to measure honestly)
- [ ] 3 of 4 fit components live — met (Gap Matching, Scheme Fit, Role Fit real; Program Fit descoped)
- [ ] 10+ beta programs complete full workflow (needs Phase 3+4 live, Phase 7 legal clearance)
- [ ] 99% uptime during beta (Phase 2 `/ready` + Phase 6's CloudWatch alarm now give a real automated
  signal for this — measuring is possible, no actual uptime track record exists yet)

---

## Suggested sequencing summary

```text
Phase 1 ✅ (foundation: VPC + secrets + Dockerfile + CI deploy step)
   -> Phase 2 ✅ (DB connectivity plan, as currently written)
       -> Phase 3 ✅ (backend on ECS Fargate — autoscaling + CORS finalization still open)
           -> Phase 4 ✅ (frontend hosting — CI deploy step still manual)
       -> Phase 5 ⏭ (scheduled jobs — explicitly skipped, revisit if a real freshness need appears)
   -> Phase 6 ◐ (observability — /ready CloudWatch alarm done, drift monitoring still deferred)
Phase 7 ❌ (security/compliance — not started, gates beta independent of infra)
   -> Phase 8 (beta launch — not reached; infra is ready, legal/compliance and product gaps remain)
```

**Where this actually stands (2026-07-20):** Phases 1-4 are live — real DB connectivity, a real backend
on ECS Fargate, a real frontend on S3+CloudFront. Phase 5 was a deliberate skip, not a gap. Phase 6 has
its one high-value piece (the CloudWatch alarm that directly answers the original incident) with the
rest intentionally deferred. What's left before beta is Phase 7 (legal/compliance, not engineering) and
the pre-existing product gaps tracked elsewhere (CLAUDE.md TODO #11, Program Fit, transfer success
model) — not infrastructure.
