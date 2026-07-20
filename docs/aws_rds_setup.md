# AWS RDS — Team Setup

Shared PostgreSQL 15 database on AWS RDS. All teammates connect to the same instance — no local Postgres required.

RDS has **no public access and no per-IP allowlist** — it only accepts connections from inside the VPC, specifically from a bastion EC2 host. Everyone reaches it through an **AWS SSM Session Manager port-forwarding tunnel** to the bastion. This replaced an earlier per-teammate static-IP allowlist (broke on network changes), and later replaced the SSH-based bastion tunnel itself (2026-07-20) — **port 22 is now closed entirely** on the bastion, no `.pem` key is used for day-to-day access anymore. SSM gives the same local-tunnel effect as the old `ssh -L` command, but authenticates via IAM instead of an SSH key, and every session is logged in CloudTrail against the IAM identity that opened it.

---

## Quick start (classmates)

1. **Pull latest `main`** and copy the env template:

   ```powershell
   cd MIDS210-Capstone
   Copy-Item .env.example .env
   ```

2. **Get from Justin** (DM / 1Password — never commit or post in Slack):
   - An AWS access key + secret for an IAM user in the **infra account** (`424056758764`), member of the `PortalPoint-Dev` group there — **this is a separate credential from your S3 access key**, even though the names look similar; they live in two different AWS accounts.
   - `portalpoint_app` password

3. **One-time local setup:**
   - Install the [AWS CLI v2](https://aws.amazon.com/cli/) if you don't have it.
   - Install the [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) — required for `aws ssm start-session` to work at all; without it you'll get `SessionManagerPlugin is not found`.
   - Configure a named profile so you don't clobber any existing default profile from S3 setup:
     ```powershell
     aws configure --profile portalpoint-infra
     # Access Key ID / Secret Access Key: from Justin
     # Region: us-east-1
     # Output format: json
     ```

4. **Open the tunnel** — leave this running in its own terminal the whole time you're working:

   ```powershell
   aws ssm start-session --profile portalpoint-infra `
     --target i-0a6e1bafc1cb6f379 `
     --document-name AWS-StartPortForwardingSessionToRemoteHost `
     --parameters '{"host":["portalpoint-db.con8amymqi1e.us-east-1.rds.amazonaws.com"],"portNumber":["5432"],"localPortNumber":["5433"]}'
   ```

   Same effect as the old `ssh -L`: `127.0.0.1:5433` on your machine now forwards to RDS port 5432, just tunneled through SSM instead of SSH. Ctrl+C to close it.

5. **Edit `.env`** — replace `<password>` with the real password (use `127.0.0.1`, not `localhost` — avoids an IPv6 bind issue where tunnels on some systems bind only to the IPv4 loopback, but `localhost` resolves to `::1`):

   ```env
   DATABASE_URL=postgresql+asyncpg://portalpoint_app:<password>@127.0.0.1:5433/portalpoint?ssl=require
   ```

6. **Start local infrastructure** (Redis only — Postgres is now on RDS):

   ```powershell
   docker compose up -d redis
   ```

7. **Run the app** (tunnel from step 4 must still be running):

   ```powershell
   uv run uvicorn portalpoint.main:app --reload
   ```

8. **Smoke test:**

   Open `http://localhost:8000/health` — should return `{"status":"ok"}`.

---

## Connection details

| Property | Value |
|---|---|
| RDS host (behind bastion, not directly reachable) | `portalpoint-db.con8amymqi1e.us-east-1.rds.amazonaws.com` |
| RDS port | `5432` |
| Local tunnel host/port (what `.env` actually points at) | `127.0.0.1:5433` |
| Bastion instance ID | `i-0a6e1bafc1cb6f379` |
| Bastion access | AWS SSM Session Manager only — **no SSH, port 22 is closed**. Needs an IAM identity in the `PortalPoint-Dev` group (infra account `424056758764`) plus the `session-manager-plugin` installed locally |
| Database | `portalpoint` |
| Runtime user | `portalpoint_app` |
| Engine | PostgreSQL 15 |
| SSL | Required (`?ssl=require` for asyncpg / `?sslmode=require` for psycopg2) — still negotiated over the tunnel, the tunnel only forwards TCP |
| Region | `us-east-1` |

**Running the app inside Docker** (e.g. a containerized script): use `host.docker.internal:5433` instead of `localhost:5433` — a container has its own network namespace and `localhost` inside it doesn't reach your host machine's tunnel.

---

## Migrations

Schema is managed by Alembic. When a new migration lands on `main`:

```bash
uv run alembic upgrade head
```

This applies the migration against the shared RDS instance — runs are idempotent, safe to re-run.

Do **not** run `alembic downgrade` on the shared instance without coordinating with the team.

---

## Notes for scripts and notebooks

`src/portalpoint/modeling/io.get_sync_engine()` auto-converts the asyncpg URL to psycopg2 format (including `ssl=require` → `sslmode=require`) — no manual change needed in scripts or notebooks. Just make sure `.env` has the correct `DATABASE_URL`.

---

## Troubleshooting

| Error | Likely cause | Fix |
|---|---|---|
| `Connection refused` (port 5433, your machine) | Tunnel not running | Start the `aws ssm start-session ...` command from step 4, leave it running in its own terminal |
| `SessionManagerPlugin is not found` | Plugin not installed locally | Install it — see step 3; the AWS CLI alone isn't enough for `start-session` |
| `An error occurred (TargetNotConnected)` | SSM agent on the bastion isn't registered/online | Check `aws ssm describe-instance-information --filters "Key=InstanceIds,Values=i-0a6e1bafc1cb6f379" --query 'InstanceInformationList[0].PingStatus'` — should be `Online`; if not, this is an infra-side problem, ping the account admin, not something fixable from your machine |
| `AccessDeniedException` calling `ssm:StartSession` | Your IAM user isn't in the `PortalPoint-Dev` group in the infra account, or the group policy hasn't been attached | Confirm with Justin/account admin that your access key is provisioned there — separate from your S3 key |
| Port 5433 already in use | Local Docker `db` container still running from before migration | `docker compose stop db` — or start with `docker compose up -d redis` (not `up -d`, which would try to start `db` too — it now requires `--profile local-db`) |
| `Connection refused` on the RDS hostname itself, any port | Pointed `DATABASE_URL` at RDS directly instead of `127.0.0.1:5433` | RDS has no direct/public access — always connect through the tunnel |
| `fe_sendauth: no password supplied` | Password missing/empty in `.env`, or running a parallel/non-interactive client that didn't get the password | Check `.env` has real password; for ad-hoc CLI tests, pass `PGPASSWORD` env var instead of relying on an interactive prompt |
| `SSL connection required` | Missing `?ssl=require` in URL | Add `?ssl=require` to `DATABASE_URL` in `.env` |
| `role "portalpoint_app" does not exist` | Wrong user in URL | Use `portalpoint_app`, not `postgres` or `portalpoint_master` |

---

## Security

- **Never commit `.env`** — it contains the database password.
- **Never commit AWS access keys** — same handling as the S3 keys, DM/1Password only.
- **Never commit the master password** (`portalpoint_master`) anywhere — it is admin-only.
- RDS has encryption at rest (KMS), enforces TLS for all connections, and is not reachable except through the bastion's security group.
- The bastion's SSH port (22) is **closed** (`0.0.0.0/0` ingress revoked 2026-07-20) — the only access path is SSM Session Manager, authenticated via IAM, logged per-identity in CloudTrail. There is no `.pem` key in the day-to-day access path anymore (`portalpoint-bastion.pem` is retained only for ops break-glass use, and even that should move to SSM `start-session` without port forwarding rather than SSH where possible).
- If you suspect an AWS access key is compromised, contact Justin/the account admin immediately to deactivate it.

---

## For the account admin

### Bastion access model

RDS security group (`sg-0ec78cb4f641ee901`) only allows port 5432 from the bastion's security group (`sg-06d79bdd59fea641a`) as a **source-group rule**, not a CIDR — this is what makes the whole setup IP-independent: teammates' networks can change freely, only the bastion's SG membership matters. This part is unchanged by the SSH→SSM migration.

```bash
aws ec2 authorize-security-group-ingress \
  --group-id sg-0ec78cb4f641ee901 \
  --protocol tcp \
  --port 5432 \
  --source-group sg-06d79bdd59fea641a
```

**Onboarding a teammate (current process, replaces the old shared-`.pem` model):**

```bash
aws iam create-user --user-name <firstname>-portalpoint-infra
aws iam add-user-to-group --group-name PortalPoint-Dev --user-name <firstname>-portalpoint-infra
aws iam create-access-key --user-name <firstname>-portalpoint-infra
```

`create-access-key` prints the secret exactly once — send both values via secure DM. `PortalPoint-Dev` here is a group **in the infra account (`424056758764`)**, distinct from the group of the same name in Justin's S3 bucket-owner account — group names don't span accounts, so this is a separate group with the `PortalPointSSMBastionAccess` policy attached (see `ssm-bastion-policy.json`, gitignored — not a secret, but not app config either).

**Offboarding:** `aws iam delete-access-key` + `aws iam delete-user` for that person's infra-account user. No shared secret to rotate for everyone else — this is the real advantage over the old shared-`.pem` model, where offboarding meant reissuing a keypair for the whole team.

### Old per-IP rules (superseded)

Any leftover individual `<ip>/32` rules on the RDS security group from before the bastion migration should be revoked — they're redundant now and just extra exposed surface:

```bash
aws ec2 revoke-security-group-ingress \
  --group-id sg-0ec78cb4f641ee901 \
  --protocol tcp \
  --port 5432 \
  --cidr <old-teammate-ip>/32
```

### Create a snapshot (before major migrations)

```bash
aws rds create-db-snapshot \
  --db-instance-identifier portalpoint-db \
  --db-snapshot-identifier portalpoint-db-pre-migration-$(date +%Y%m%d)
```

---

## Related docs

- [`.env.example`](../.env.example) — env variable template
- [`docs/aws_s3_setup.md`](aws_s3_setup.md) — S3 setup (model artifacts, raw data)
- [`docs/status/ARCHITECTURE_STATUS.md`](status/ARCHITECTURE_STATUS.md) — infrastructure status and migration history
