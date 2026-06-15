"""Shared MLflow helpers for PortalPoint model notebooks."""
from __future__ import annotations

from pathlib import Path

import mlflow
from mlflow import MlflowClient


def _find_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in [p, *p.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    raise FileNotFoundError("Could not find repo root (pyproject.toml)")


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = _find_repo_root() / ".env"
    if not p.exists():
        return env
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env

def get_artifact_root() -> str | None:
    """S3 artifact root when S3_BUCKET is set; else local default."""
    bucket = _load_env().get("S3_BUCKET", "").strip()
    if bucket and not bucket.startswith("#"):
        return f"s3://{bucket}/mlflow"
    return None

def get_tracking_uri() -> str:
    """Read MLFLOW_TRACKING_URI from .env; fall back to SQLite at repo root."""
    env = _load_env()
    uri = env.get("MLFLOW_TRACKING_URI", "")
    if not uri or uri.startswith("#") or uri.startswith("file:"):
        db_path = _find_repo_root() / "mlruns.db"
        uri = f"sqlite:///{db_path}"
    return uri


def setup_mlflow(experiment_name: str) -> MlflowClient:
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
