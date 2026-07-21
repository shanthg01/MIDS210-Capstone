# Production DB Connectivity Plan

**Status:** Done (2026-07-20). Written 2026-07-13 after a dev incident (search/login/signup all
failing — root cause: local SSH bastion tunnel to RDS had closed, so every DB-backed
endpoint 500'd; `/health` stayed green throughout because it doesn't touch the DB).
See `docs/aws_rds_setup.md` for the current dev-only bastion tunnel setup this plan replaces
for the production path.

**Progress against the numbered items below (2026-07-20): all 6 done.** Item 3 (secrets management)
done for `DATABASE_URL`/`JWT_SECRET`; Tavily/Gemini keys deferred until the news-monitoring PR merges
and their real env var names can be confirmed — not a blocker for this plan. Item 5 (Multi-AZ RDS)
done. Item 6 (bastion break-glass only) done — SSH closed, SSM Session Manager wired up, teammate IAM
access via a `PortalPoint-Dev` group in the infra account. Item 2 (SG scoping) done for the ECS task
SG. Items 1 (ECS Fargate, no tunnel in the request path) and 4 (`/ready` endpoint) done together — real
near-miss caught in the process: the ALB target group's health check was configured against `/ready`
*before* the endpoint actually existed in code, which would have made every ECS task fail health
checks and cycle indefinitely. Caught before the service went live; `/ready` now does a real
`SELECT 1`, returns 503 on DB failure — this is the direct fix for the incident that started this plan.
Foundation work also done: private subnets + NAT gateway + S3 gateway endpoint, ECR repo + working
`Dockerfile`, GitHub Actions OIDC deploy role (`deploy.yml`, now including the ECS `update-service`
step). Full command log: `docs/production_deployment_commands.md`.

## Why the current dev setup doesn't carry over

Dev laptops sit outside the VPC, so RDS (VPC-internal only) is reached through an SSH
bastion + local port-forward tunnel (`portalpoint-bastion.pem` → `127.0.0.1:5433`). This
is a workaround for "my machine isn't in the network," not a pattern to run in production.
In production the app tier itself runs inside AWS — the workaround's reason to exist goes
away.

## Target architecture (per CLAUDE.md tech stack: ECS Fargate + RDS)

1. **No tunnel in the request path.** Backend runs as ECS Fargate tasks inside the same
   VPC as RDS, private subnets, talks to RDS directly on 5432. No internet hop, no bastion,
   no `.pem` key involved in serving traffic.

2. **Security group scoping.** RDS security group allows inbound 5432 only from the ECS
   task security group — not `0.0.0.0/0`, not the whole VPC CIDR. Bastion (if kept) gets
   its own narrow rule, ideally replaced by AWS SSM Session Manager (no open SSH port, no
   long-lived `.pem` key sitting in the repo root like today).

3. **Secrets management, not `.env`.** `DATABASE_URL`, `JWT_SECRET`, AWS creds move to AWS
   Secrets Manager or SSM Parameter Store, injected as ECS task env vars at launch.
   Plaintext `.env` with a live password on disk is acceptable for local dev only.

4. **Real DB-aware readiness check.** This incident's actual blind spot: `/health` returned
   200 the whole time DB was unreachable, so there was no automated signal — a human had to
   notice. Add a `/ready` endpoint that runs `SELECT 1` against the DB. Wire `/ready` (not
   `/health`) into the ECS task health check and the ALB target group health check, so a DB
   outage marks the task unhealthy and triggers auto-restart/alerting instead of silently
   500ing every real request.

5. **Multi-AZ RDS.** Removes the tunnel setup's implicit single point of failure (one
   bastion host, one SSH session) — RDS failover handles instance loss, ECS handles app-tier
   restart.

6. **Bastion retained only for ops break-glass access** (manual queries, migration
   debugging) — not part of the app's runtime connectivity path once this lands.

## Open items before implementation

- ~~Confirm target VPC/subnet layout for ECS tasks~~ ✅ Decided 2026-07-20 — private subnets + NAT
  gateway (+ S3 gateway endpoint), not fully-private-with-interface-endpoints, for cost/simplicity
  at this team's scale.
- ~~Decide Secrets Manager vs. SSM Parameter Store~~ ✅ Decided 2026-07-20 — Secrets Manager (native
  RDS rotation support; per-secret cost is trivial at this secret count).
- ~~Define `/ready` failure semantics~~ ✅ Implemented 2026-07-20 — fails fast (single `SELECT 1`,
  503 on any exception), no retry/backoff. Fine at current traffic; revisit if transient DB blips
  cause target-group flapping under real load.
- CloudWatch alarm wiring for repeated `/ready` failures (paging vs. Slack notification —
  no monitoring channel decided yet). **Still open — this is Phase 6 in `docs/road_to_production.md`.**
