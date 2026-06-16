# AWS S3 — Team Setup

Shared S3 bucket for raw ingest data, model artifacts, and (eventually) MLflow artifact storage. **Local-first dev** — notebooks and scripts on your laptop read/write S3 via credentials in `.env`.

---

## Quick start (classmates)

1. **Pull latest `main`** and copy the env template:

   ```powershell
   cd MIDS210-Capstone
   Copy-Item .env.example .env
   ```

2. **Get AWS keys from Justin** (DM / 1Password — never commit or post in Slack). Each teammate has a dedicated IAM user in the shared bucket account.

3. **Add to `.env`** (uncomment and fill in the AWS block):

   ```env
   AWS_ACCESS_KEY_ID=AKIA...
   AWS_SECRET_ACCESS_KEY=...
   AWS_DEFAULT_REGION=us-east-1
   S3_BUCKET=portalpoint-data
   ```

4. **Install notebook deps** (includes boto3 for S3):

   ```powershell
   uv sync
   uv pip install -r notebooks/requirements-notebooks.txt
   ```

   Optional: [AWS CLI](https://aws.amazon.com/cli/) for manual `aws s3` commands.

5. **Smoke test** from repo root:

   ```powershell
   aws s3 ls s3://portalpoint-data/
   ```

   You should see bucket prefixes (or an empty listing if nothing uploaded yet). If you get `Access Denied`, contact Justin to confirm your IAM user is active.

6. **Run notebooks** — start Jupyter from repo root so `.env` and `mlflow_helpers.py` resolve correctly:

   ```powershell
   uv run jupyter notebook
   ```

---

## Bucket layout

```
s3://portalpoint-data/
  raw/barttorvik/YYYY-MM-DD/
  raw/hoop_explorer/YYYY-MM-DD/
  models/player_clustering/
  models/team_clustering/
  models/transfer_success/
  mlflow/                    # MLflow artifacts (when wired to S3)
```

| Prefix | Contents |
|---|---|
| `raw/` | Bronze ingest snapshots (parquet, CSV) |
| `models/` | `.pkl` scalers, K-Means models, centroid CSVs |
| `mlflow/` | Logged run artifacts (plots, sklearn models) |

---

## Access model

| Layer | How it works |
|---|---|
| **AWS Organization** | Justin's account is the org management account; teammate AWS accounts are linked for **credit sharing / billing only**. |
| **S3 access** | IAM users in the **bucket owner account** (Justin provisions). One programmatic access key per person. |
| **Your `.env`** | Keys are **local only** — `.env` is gitignored. |

Teammates do **not** need to configure IAM in their own AWS accounts for S3.

### Permissions (scoped)

Each dev IAM user can:

- `ListBucket`, `GetBucketLocation` on `portalpoint-data`
- `GetObject`, `PutObject`, `DeleteObject` on `portalpoint-data/*`

No console login required. No access to other AWS services or buckets.

### Security

- Block all public access on the bucket
- Default encryption: SSE-S3 (at rest)
- HTTPS required for uploads/downloads (TLS enforced via bucket policy)
- Revoke keys when someone leaves the project — contact Justin

---

## MLflow + S3

MLflow uses **two stores**:

| Store | What it holds | Current setup |
|---|---|---|
| **Tracking** | Run metadata, params, metrics, registry | `sqlite:///mlruns.db` at repo root (local) |
| **Artifacts** | `.pkl`, plots, logged files | Local by default; moving to `s3://portalpoint-data/mlflow/` |

**Do not** set `MLFLOW_TRACKING_URI` to an `s3://` URI — MLflow tracking metadata needs SQLite, Postgres, or an MLflow server URL. S3 is for **artifacts** only.

### Browse existing runs (local)

```powershell
uv pip install mlflow
uv run mlflow ui --backend-store-uri sqlite:///mlruns.db
```

Open http://127.0.0.1:5000

### Upload a model artifact manually

```powershell
aws s3 cp data/models/player_kmeans.pkl s3://portalpoint-data/models/player_clustering/
aws s3 ls s3://portalpoint-data/models/player_clustering/
```

---

## Common commands

```powershell
# List bucket
aws s3 ls s3://portalpoint-data/

# List a prefix
aws s3 ls s3://portalpoint-data/models/

# Upload
aws s3 cp local-file.parquet s3://portalpoint-data/raw/barttorvik/2025-06-14/

# Download
aws s3 cp s3://portalpoint-data/models/player_clustering/player_kmeans.pkl data/models/

# Sync a directory
aws s3 sync data/models/ s3://portalpoint-data/models/player_clustering/ --exclude "*" --include "player_*"
```

---

## Troubleshooting

| Error | Likely cause | Fix |
|---|---|---|
| `Access Denied` | Missing/wrong keys, or IAM user not created yet | Confirm `.env` values; ask Justin to verify your IAM user |
| `NoSuchBucket` | Wrong bucket name or region | Use `portalpoint-data` and `AWS_DEFAULT_REGION=us-east-1` |
| `Unable to locate credentials` | `.env` not loaded or Jupyter started outside repo root | Start Jupyter from repo root; verify `.env` exists |
| MLflow can't find runs | UI pointed at wrong backend | `mlflow ui --backend-store-uri sqlite:///mlruns.db` from repo root |

---

## For Justin (bucket owner)

When onboarding a new teammate:

1. IAM → Users → Create user (e.g. `firstname-portalpoint`)
2. Programmatic access only; add to group `PortalPoint-Dev`
3. Create access key → send securely via DM
4. On departure: deactivate access key and delete user

Policy attached to `PortalPoint-Dev`: `PortalPointS3Access` (scoped to `portalpoint-data` only).

---

## Related docs

- [`.env.example`](../.env.example) — env variable template
- [`README.md`](../README.md) — full stack quick start + MLflow section
- [`status/ARCHITECTURE_STATUS.md`](status/ARCHITECTURE_STATUS.md) — infrastructure status and bucket layout
