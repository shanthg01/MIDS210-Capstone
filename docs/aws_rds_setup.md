# AWS RDS — Team Setup

Shared PostgreSQL 15 database on AWS RDS. All teammates connect to the same instance — no local Postgres required.

---

## Quick start (classmates)

1. **Pull latest `main`** and copy the env template:

   ```powershell
   cd MIDS210-Capstone
   Copy-Item .env.example .env
   ```

2. **Get the `portalpoint_app` password from Justin** (DM / 1Password — never commit or post in Slack).

3. **Edit `.env`** — replace `<password>` with the real password:

   ```env
   DATABASE_URL=postgresql+asyncpg://portalpoint_app:<password>@portalpoint-db.con8amymqi1e.us-east-1.rds.amazonaws.com:5432/portalpoint?ssl=require
   ```

4. **Get your IP added to the security group** — RDS port 5432 is restricted to allowlisted IPs. Send Justin your public IP (find it at https://checkip.amazonaws.com) so he can add it.

5. **Start local infrastructure** (Redis only — Postgres is now on RDS):

   ```powershell
   docker compose up -d redis
   ```

6. **Run the app:**

   ```powershell
   uv run uvicorn portalpoint.main:app --reload
   ```

7. **Smoke test:**

   Open `http://localhost:8000/health` — should return `{"status":"ok"}`.

---

## Connection details

| Property | Value |
|---|---|
| Host | `portalpoint-db.con8amymqi1e.us-east-1.rds.amazonaws.com` |
| Port | `5432` |
| Database | `portalpoint` |
| Runtime user | `portalpoint_app` |
| Engine | PostgreSQL 15 |
| SSL | Required (`?ssl=require` for asyncpg / `?sslmode=require` for psycopg2) |
| Region | `us-east-1` |

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
| `Connection refused` / timeout | Your IP not in security group | Send Justin your IP from https://checkip.amazonaws.com |
| `fe_sendauth: no password supplied` | Password missing or empty in `.env` | Check `.env` has real password, not `<password>` placeholder |
| `SSL connection required` | Missing `?ssl=require` in URL | Add `?ssl=require` to `DATABASE_URL` in `.env` |
| `role "portalpoint_app" does not exist` | Wrong user in URL | Use `portalpoint_app`, not `postgres` or `portalpoint_master` |

---

## Security

- **Never commit `.env`** — it contains the database password.
- **Never commit the master password** (`portalpoint_master`) anywhere — it is admin-only.
- RDS has encryption at rest (KMS) and enforces TLS for all connections.
- If you suspect credentials are compromised, contact Justin immediately to rotate.

---

## For Justin (RDS admin)

### Add a teammate's IP to the security group

```bash
aws ec2 authorize-security-group-ingress \
  --group-id <portalpoint-rds-sg-id> \
  --protocol tcp \
  --port 5432 \
  --cidr <teammate-ip>/32
```

### Remove a departed teammate's IP

```bash
aws ec2 revoke-security-group-ingress \
  --group-id <portalpoint-rds-sg-id> \
  --protocol tcp \
  --port 5432 \
  --cidr <teammate-ip>/32
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
