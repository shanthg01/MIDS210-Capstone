# AWS RDS — Team Setup

Shared PostgreSQL 15 database on AWS RDS. All teammates connect to the same instance — no local Postgres required.

RDS has **no public access and no per-IP allowlist** — it only accepts connections from inside the VPC, specifically from a bastion EC2 host. Everyone reaches it through an SSH tunnel via the bastion. This replaced an earlier per-teammate static-IP allowlist, which broke every time someone changed networks (home wifi vs. office vs. coffee shop).

---

## Quick start (classmates)

1. **Pull latest `main`** and copy the env template:

   ```powershell
   cd MIDS210-Capstone
   Copy-Item .env.example .env
   ```

2. **Get from Justin** (DM / 1Password — never commit or post in Slack):
   - `portalpoint-bastion.pem` (SSH private key)
   - Bastion public IP
   - `portalpoint_app` password

3. **Open the SSH tunnel** — leave this running in its own terminal the whole time you're working:

   ```powershell
   ssh -i portalpoint-bastion.pem -L 5433:portalpoint-db.con8amymqi1e.us-east-1.rds.amazonaws.com:5432 ec2-user@<bastion-public-ip> -N
   ```

   `-N` means no remote shell, just port forwarding — no output is expected, that's normal. `localhost:5433` on your machine now forwards to RDS port 5432 through the bastion.

4. **Edit `.env`** — replace `<password>` with the real password (host stays `localhost`, port `5433` — the tunnel, not RDS directly):

   ```env
   DATABASE_URL=postgresql+asyncpg://portalpoint_app:<password>@localhost:5433/portalpoint?ssl=require
   ```

5. **Start local infrastructure** (Redis only — Postgres is now on RDS):

   ```powershell
   docker compose up -d redis
   ```

6. **Run the app** (tunnel from step 3 must still be running):

   ```powershell
   uv run uvicorn portalpoint.main:app --reload
   ```

7. **Smoke test:**

   Open `http://localhost:8000/health` — should return `{"status":"ok"}`.

---

## Connection details

| Property | Value |
|---|---|
| RDS host (behind bastion, not directly reachable) | `portalpoint-db.con8amymqi1e.us-east-1.rds.amazonaws.com` |
| RDS port | `5432` |
| Local tunnel host/port (what `.env` actually points at) | `localhost:5433` |
| Bastion public IP | get from Justin |
| Bastion SSH user | `ec2-user` (Amazon Linux 2023) |
| Bastion key | `portalpoint-bastion.pem` (get from Justin, never commit) |
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
| `Connection refused` (port 5433, your machine) | Tunnel not running | Start the `ssh -L 5433:...` command from step 3, leave it running in its own terminal |
| `Connection refused` on the RDS hostname itself, any port | Pointed `DATABASE_URL` at RDS directly instead of `localhost:5433` | RDS has no direct/public access — always connect through the tunnel |
| `Permission denied (publickey)` on `ssh` | Wrong/missing `.pem` key, or wrong permissions on it | Get `portalpoint-bastion.pem` from Justin; on Windows `icacls` isn't required but the path must be correct in the `-i` flag |
| `fe_sendauth: no password supplied` | Password missing/empty in `.env`, or running a parallel/non-interactive client that didn't get the password | Check `.env` has real password; for ad-hoc CLI tests, pass `PGPASSWORD` env var instead of relying on an interactive prompt |
| `SSL connection required` | Missing `?ssl=require` in URL | Add `?ssl=require` to `DATABASE_URL` in `.env` |
| `role "portalpoint_app" does not exist` | Wrong user in URL | Use `portalpoint_app`, not `postgres` or `portalpoint_master` |

---

## Security

- **Never commit `.env`** — it contains the database password.
- **Never commit `portalpoint-bastion.pem`** — it's the bastion SSH key.
- **Never commit the master password** (`portalpoint_master`) anywhere — it is admin-only.
- RDS has encryption at rest (KMS), enforces TLS for all connections, and is not reachable except through the bastion's security group.
- If you suspect credentials or the bastion key are compromised, contact Justin immediately to rotate.

---

## For Justin (RDS admin)

### Bastion access model

RDS security group (`sg-0ec78cb4f641ee901`) only allows port 5432 from the bastion's security group (`sg-06d79bdd59fea641a`) as a **source-group rule**, not a CIDR — this is what makes the whole setup IP-independent: teammates' networks can change freely, only the bastion's SG membership matters.

```bash
aws ec2 authorize-security-group-ingress \
  --group-id sg-0ec78cb4f641ee901 \
  --protocol tcp \
  --port 5432 \
  --source-group sg-06d79bdd59fea641a
```

Onboard a teammate by sharing the existing `portalpoint-bastion.pem` key (not a new IAM/SG change per person). Offboard by rotating the key — generate a new keypair, update the bastion's authorized key, redistribute to remaining teammates.

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
