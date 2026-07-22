"""Shared MLflow helpers for PortalPoint modeling pipeline (notebooks + scripts)."""
from __future__ import annotations

import os
from dataclasses import dataclass

import mlflow
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

from portalpoint.modeling.io import find_repo_root, load_env

CHAMPION_ALIAS = "champion"


@dataclass(frozen=True)
class PromotionResult:
    """Outcome of maybe_promote — str()-compatible for existing log call sites."""

    message: str
    delta_pct: float | None = None
    promoted: bool = False

    def __str__(self) -> str:
        return self.message


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
        pass
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
    alias: str = CHAMPION_ALIAS,
) -> PromotionResult:
    """Register new model version; promote to `alias` (default `"champion"`)
    if improvement > threshold vs. whatever currently holds that alias.

    First version always gets the alias (no baseline to beat). Returns a
    PromotionResult describing the outcome (also usable as str).

    Migrated 2026-06-25 from MLflow's stages API (`get_latest_versions(...,
    stages=["Production"])` / `transition_model_version_stage`) to the
    alias-based registry API (`get_model_version_by_alias` /
    `set_registered_model_alias`) — stages are deprecated since MLflow 2.9
    and will be removed in a future major release (confirmed via real
    `FutureWarning`s on this session's actual runs, not a hypothetical).
    Versions that are *not* promoted simply don't hold the alias — there's
    no "Staging" equivalent to set, since nothing in this codebase ever read
    that label besides this function's own returned string.
    """
    model_uri = f"runs:/{run_id}/{artifact_path}"
    mv = mlflow.register_model(model_uri, model_name)

    try:
        champion = client.get_model_version_by_alias(model_name, alias)
    except MlflowException:
        champion = None

    if champion is None:
        client.set_registered_model_alias(model_name, alias, mv.version)
        return PromotionResult(
            message=f"first_production — {model_name} v{mv.version} → @{alias}",
            promoted=True,
        )

    champion_metrics = client.get_run(champion.run_id).data.metrics
    champion_value = champion_metrics.get(metric_name, 0.0)

    if champion_value == 0.0:
        delta = float("inf")
    elif higher_is_better:
        delta = (new_value - champion_value) / abs(champion_value)
    else:
        delta = (champion_value - new_value) / abs(champion_value)

    if delta > threshold:
        client.set_registered_model_alias(model_name, alias, mv.version)
        return PromotionResult(
            message=f"promoted — {model_name} v{mv.version} → @{alias} (Δ={delta:+.1%})",
            delta_pct=float(delta),
            promoted=True,
        )
    return PromotionResult(
        message=f"staging — {model_name} v{mv.version} stays below @{alias} (Δ={delta:+.1%})",
        delta_pct=float(delta),
        promoted=False,
    )
