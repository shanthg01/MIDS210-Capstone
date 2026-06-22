"""Shared MLflow helpers for PortalPoint modeling pipeline (notebooks + scripts)."""
from __future__ import annotations

import os

import mlflow
from mlflow import MlflowClient

from portalpoint.modeling.io import find_repo_root, load_env


def ensure_aws_env() -> None:
    """Export AWS credentials from .env into os.environ if not already set.

    boto3 (used by MLflow for S3 artifact writes) reads from os.environ,
    not from .env. This ensures the right IAM user is used regardless of
    what's in ~/.aws/credentials.
    """
    env = load_env()
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"):
        if key not in os.environ and key in env:
            os.environ[key] = env[key]


def get_artifact_root() -> str | None:
    """S3 artifact root when S3_BUCKET is set; else local default."""
    bucket = load_env().get("S3_BUCKET", "").strip()
    if bucket and not bucket.startswith("#"):
        return f"s3://{bucket}/mlflow"
    return None

def get_tracking_uri() -> str:
    """Read MLFLOW_TRACKING_URI from .env; fall back to SQLite at repo root.

    A relative `sqlite:///mlruns.db` resolves against the *process* CWD, which
    differs between notebooks (cwd=notebooks/models) and scripts (cwd=repo
    root) — they'd silently track to two different files. Anchor relative
    sqlite paths to the repo root so both land in the same store.
    """
    env = load_env()
    uri = env.get("MLFLOW_TRACKING_URI", "")
    if not uri or uri.startswith("#") or uri.startswith("file:"):
        db_path = find_repo_root() / "mlruns.db"
        return f"sqlite:///{db_path}"
    if uri.startswith("sqlite:///") and not uri[len("sqlite:///"):].startswith(("/", "\\")) and ":" not in uri[len("sqlite:///"):]:
        rel_path = uri[len("sqlite:///"):]
        return f"sqlite:///{find_repo_root() / rel_path}"
    return uri


def setup_mlflow(experiment_name: str) -> MlflowClient:
    ensure_aws_env()
    mlflow.set_tracking_uri(get_tracking_uri())
    client = MlflowClient()
    artifact_root = get_artifact_root()

    exp = client.get_experiment_by_name(experiment_name)
    if exp is None:
        if artifact_root:
            client.create_experiment(experiment_name, artifact_location=artifact_root)
        else:
            client.create_experiment(experiment_name)
    else:
        # artifact_location is immutable once an experiment is created (no such
        # thing as MlflowClient.update_experiment) — can't patch a pre-existing
        # local-artifact experiment onto S3 here. Just use it as-is.
        mlflow.set_experiment(experiment_name)

    return client


def maybe_promote(
    client: MlflowClient,
    model_name: str,
    run_id: str,
    artifact_path: str,
    metric_name: str,
    new_value: float,
    higher_is_better: bool = True,
    threshold: float = 0.05,
) -> str:
    """Register new model version; promote to Production if improvement > threshold.

    First version always goes to Production (no baseline to beat).
    Returns a string describing the outcome.
    """
    model_uri = f"runs:/{run_id}/{artifact_path}"
    mv = mlflow.register_model(model_uri, model_name)

    prod_versions = client.get_latest_versions(model_name, stages=["Production"])
    if not prod_versions:
        client.transition_model_version_stage(model_name, mv.version, "Production")
        return f"first_production — {model_name} v{mv.version} → Production"

    prod_metrics = client.get_run(prod_versions[0].run_id).data.metrics
    prod_value = prod_metrics.get(metric_name, 0.0)

    if prod_value == 0.0:
        delta = float("inf")
    elif higher_is_better:
        delta = (new_value - prod_value) / abs(prod_value)
    else:
        delta = (prod_value - new_value) / abs(prod_value)

    if delta > threshold:
        client.transition_model_version_stage(model_name, mv.version, "Production")
        return f"promoted — {model_name} v{mv.version} → Production (Δ={delta:+.1%})"
    else:
        client.transition_model_version_stage(model_name, mv.version, "Staging")
        return f"staging — {model_name} v{mv.version} → Staging (Δ={delta:+.1%})"
