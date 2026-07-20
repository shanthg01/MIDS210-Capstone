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
| Backend | Live on ECS Fargate behind an ALB (`portalpoint-prod` cluster, `portalpoint-backend` service) — health-checked on a real DB-aware `/ready`, not `/health`. No autoscaling policy yet; CORS still pointed at local dev origins pending Phase 4 |
| Frontend | React/Vite, local-only (`npm run dev`), never deployed anywhere |
| Secrets | `DATABASE_URL`/`JWT_SECRET` now in AWS Secrets Manager; Tavily/Gemini keys still deferred (pending news-monitoring PR merge) |
| CI | GitHub Actions `pull_request` pytest gate exists; `deploy.yml` added (builds/pushes to ECR via OIDC on merge to `main`) — ECS deploy step intentionally commented out until Phase 3 |
| Scheduled jobs | None running on a schedule; all ingest/model scripts run manually per dev |
| MLflow | Local SQLite `mlruns.db` per dev; artifacts to shared S3 |
| Monitoring | None — no Prometheus/Grafana/Sentry despite being named in CLAUDE.md's tech stack; `/health` doesn't check DB (root cause of the 2026-07-13 incident that produced the DB connectivity plan) |
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

---

## Phase 4 — Frontend hosting

Currently undecided — no doc addresses this (flagged in CLAUDE.md Process Improvement TODO #11 as a
frontend audit gap, but that's about backend/frontend field drift, not hosting).

1. Choose target: S3+CloudFront (fits the existing AWS-only stack, cheap, static) vs. a managed host
   (Amplify/Vercel — faster to stand up, less consistent with "everything in one AWS account").
   Recommend S3+CloudFront given CLAUDE.md's cloud column is AWS-only everywhere else.
2. Build-time env injection for `VITE_API_BASE_URL` pointed at Phase 3's ALB/domain.
3. CI: add a frontend build+deploy step (separate job from the pytest gate, since `frontend/` has its
   own `package.json`/test setup, not covered by `test.yml` today).
4. Once backend + frontend are both reachable, revisit the frontend gaps already logged in
   `APPLICATION_STATUS.md`/CLAUDE.md TODO #11 (dashboard still stub data, stale `LIVE_COMPONENTS` set,
   etc.) — those are product-correctness gaps, not blockers to standing up hosting, but worth fixing
   before pointing beta users at a public URL.

---

## Phase 5 — Scheduled jobs / ML pipeline in production

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

1. Real DB-aware `/ready` (Phase 2) is the immediate fix for blind-spot monitoring, but CLAUDE.md's
   own design decision ("KS-test feature drift daily, alert if RMSE > baseline × 1.2") has never been
   implemented for any of the 9 models (CLAUDE.md Process Improvement TODO #8) — no Prometheus/Grafana
   exists in the repo despite being named in the tech stack table.
2. Stand up CloudWatch alarms on `/ready` failures first (cheapest, no new infra, per the DB plan's
   own open item) — decide paging vs. Slack before beta, not after an incident.
3. Sentry (named in tech stack, not wired) for backend exception tracking — cheap to add once the
   ECS task exists to run it from.
4. Defer full Prometheus/Grafana + per-model drift monitoring until after beta unless a specific
   incident forces it earlier — matches CLAUDE.md's own stated reasoning (needs infra that doesn't
   exist yet, lower priority than getting anything live).

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
- [ ] 99% uptime during beta (needs Phase 2 `/ready` + Phase 6 alerting to even measure)

---

## Suggested sequencing summary

```text
Phase 1 (foundation: VPC + secrets + Dockerfile + CI deploy step)
   -> Phase 2 (DB connectivity plan, as currently written)
       -> Phase 3 (backend on ECS Fargate)
           -> Phase 4 (frontend hosting)
       -> Phase 5 (scheduled jobs — shares Phase 1's VPC/secrets decisions)
   -> Phase 6 (observability — /ready alarms first, drift monitoring later)
Phase 7 (security/compliance — run in parallel, gates beta independent of infra)
   -> Phase 8 (beta launch)
```

The DB connectivity plan is necessary but not sufficient — it unblocks Phase 3, but Phase 4
(frontend hosting) and Phase 5 (scheduled jobs reaching RDS from outside the VPC) have their own
undecided pieces that reuse Phase 1's choices rather than the DB plan's.
