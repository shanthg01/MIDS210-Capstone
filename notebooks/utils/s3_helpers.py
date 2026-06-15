"""S3 upload/download helpers for PortalPoint notebooks and scripts.

Loads credentials from .env at repo root — no reliance on the AWS credential
chain, so the right IAM user (Justin's team bucket) is always used regardless
of what's in ~/.aws/credentials.

Usage:
    from s3_helpers import upload, download

    upload('data/models/player_kmeans.pkl', 'models/player_clustering/player_kmeans.pkl')
    download('raw/features/player_features.parquet', 'data/features/player_features.parquet')
"""
from __future__ import annotations

import os
from pathlib import Path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        if (p / "pyproject.toml").exists():
            return p
    raise FileNotFoundError("Cannot locate repo root (pyproject.toml)")


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    dotenv = _find_repo_root() / ".env"
    if not dotenv.exists():
        return env
    for line in dotenv.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ensure_aws_env() -> None:
    """Export AWS credentials from .env into os.environ if not already set.

    Required so that boto3 (and MLflow's internal boto3 calls) pick up the
    right IAM user credentials regardless of what ~/.aws/credentials contains.
    """
    env = _load_env()
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"):
        if key not in os.environ and key in env:
            os.environ[key] = env[key]


def get_bucket() -> str:
    """Return S3_BUCKET from .env or os.environ."""
    return os.environ.get("S3_BUCKET") or _load_env().get("S3_BUCKET", "portalpoint-data")


def get_s3_client():
    """Return a boto3 S3 client authenticated via .env credentials."""
    import boto3  # type: ignore

    ensure_aws_env()
    env = _load_env()
    return boto3.client(
        "s3",
        aws_access_key_id=env.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=env.get("AWS_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY"),
        region_name=env.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


def upload(
    local_path: str | Path,
    s3_key: str,
    bucket: str | None = None,
    verbose: bool = True,
) -> str:
    """Upload a local file to S3. Returns the s3:// URI."""
    bucket = bucket or get_bucket()
    client = get_s3_client()
    client.upload_file(str(local_path), bucket, s3_key)
    uri = f"s3://{bucket}/{s3_key}"
    if verbose:
        print(f"  → {uri}")
    return uri


def download(
    s3_key: str,
    local_path: str | Path,
    bucket: str | None = None,
) -> Path:
    """Download an S3 object to a local path. Creates parent dirs as needed."""
    bucket = bucket or get_bucket()
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    client = get_s3_client()
    client.download_file(bucket, s3_key, str(local_path))
    return local_path
