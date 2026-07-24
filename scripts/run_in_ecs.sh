#!/usr/bin/env bash
# scripts/run_in_ecs.sh — run a modeling script as a one-off ECS Fargate task
# inside the VPC, bypassing the local SSM tunnel's bandwidth ceiling (~0.6-0.8
# MB/s in both directions, confirmed 2026-07-22/23 via timed real fetches on
# run_playing_time.py — no SQL/code-level fix gets around it, the tunnel itself
# is the bottleneck). Mirrors .github/workflows/deploy.yml's migration-task
# derivation: pulls the live portalpoint-backend service's network config and
# task definition, registers a sibling "portalpoint-backend-modeling" family
# (same image/cpu/mem/roles, regular portalpoint_app DB secret — these are
# DML-only scripts, no DDL, unlike the migrate task's master-user swap), adds
# the EFS-mounted MLflow tracking store, then run-task with a command override.
#
# Uses Python instead of jq for the task-def transform — jq isn't installed in
# this project's Git Bash environment (same workaround already used elsewhere
# in this repo's ops scripts).
#
# Requires PORTALPOINT_MLFLOW_FS_ID / PORTALPOINT_MLFLOW_AP_ID env vars set to
# the EFS filesystem/access-point IDs created for the MLflow tracking store —
# see docs/production_deployment_commands.md.
#
# IMPORTANT: this always runs the image tagged `:ecs-modeling` in ECR, not
# `:latest` — `:latest` gets silently overwritten by any merge to `main`
# (deploy.yml rebuilds and pushes it, real incident 2026-07-23). Before running
# this script, always build+push BOTH tags so the modeling image reflects your
# current local changes:
#   docker build -t portalpoint-backend:local .
#   docker tag portalpoint-backend:local <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/portalpoint-backend:ecs-modeling
#   docker push <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/portalpoint-backend:ecs-modeling
#
# Usage:
#   export PORTALPOINT_MLFLOW_FS_ID=fs-xxxxxxxx
#   export PORTALPOINT_MLFLOW_AP_ID=fsap-xxxxxxxx
#   ./scripts/run_in_ecs.sh scripts/run_playing_time.py --target-season 2027 --source-season 2026

# MSYS/Git Bash rewrites leading-slash args (e.g. /ecs/portalpoint-backend,
# /mnt/mlflow) into Windows paths before they reach the aws CLI — same gotcha
# hit interactively earlier tonight. Disabling path conversion for this whole
# script's aws/python invocations avoids it everywhere at once.
export MSYS_NO_PATHCONV=1

set -euo pipefail

CLUSTER="portalpoint-prod"
SERVICE="portalpoint-backend"
FAMILY="portalpoint-backend-modeling"
FS_ID="${PORTALPOINT_MLFLOW_FS_ID:?Set PORTALPOINT_MLFLOW_FS_ID (EFS filesystem id)}"
AP_ID="${PORTALPOINT_MLFLOW_AP_ID:?Set PORTALPOINT_MLFLOW_AP_ID (EFS access point id)}"
# The live API service's cpu/memory (512/1024) is sized for request handling, not
# a batch job materializing a ~491K-row wide dataframe per chunk plus a TreeSHAP
# explainer — a real OOM kill (exit 137) confirmed this the hard way on the first
# real chunk. Override with generous headroom rather than inherit the API's sizing.
MODELING_CPU="${PORTALPOINT_MODELING_CPU:-4096}"
MODELING_MEMORY="${PORTALPOINT_MODELING_MEMORY:-30720}"

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <script.py> [args...]" >&2
  exit 1
fi

echo "Fetching live service network config..."
NETWORK_CONFIG=$(aws ecs describe-services \
  --cluster "$CLUSTER" --services "$SERVICE" \
  --query 'services[0].networkConfiguration' --output json)

echo "Fetching live task definition..."
LIVE_TASK_DEF=$(aws ecs describe-task-definition \
  --task-definition "$SERVICE" --query 'taskDefinition')

MODELING_TASK_DEF=$(FAMILY="$FAMILY" FS_ID="$FS_ID" AP_ID="$AP_ID" MODELING_CPU="$MODELING_CPU" MODELING_MEMORY="$MODELING_MEMORY" uv run python -c "
import json
import os
import sys

live = json.load(sys.stdin)
fs_id = os.environ['FS_ID']
ap_id = os.environ['AP_ID']

container_defs = live['containerDefinitions']
for c in container_defs:
    if c.get('name') == 'backend':
        # Pin to a dedicated tag, not whatever :latest the live service currently
        # references -- confirmed real incident (2026-07-23): a teammate's PR merge
        # to main triggered deploy.yml mid-session, which rebuilt from main's
        # Dockerfile (missing this session's local scripts/ addition) and silently
        # overwrote :latest. A future merge could do the same at any moment;
        # :ecs-modeling is pushed only by scripts/run_in_ecs.sh callers themselves.
        image = c['image']
        c['image'] = image.rsplit(':', 1)[0] + ':ecs-modeling'
        c['mountPoints'] = c.get('mountPoints', []) + [{
            'sourceVolume': 'mlflow',
            'containerPath': '/mnt/mlflow',
            'readOnly': False,
        }]
        c['environment'] = [
            e for e in c.get('environment', []) if e['name'] != 'MLFLOW_TRACKING_URI'
        ] + [{'name': 'MLFLOW_TRACKING_URI', 'value': 'sqlite:////mnt/mlflow/mlruns.db'}]

out = {
    'family': os.environ['FAMILY'],
    'networkMode': live['networkMode'],
    'requiresCompatibilities': live['requiresCompatibilities'],
    'cpu': os.environ['MODELING_CPU'],
    'memory': os.environ['MODELING_MEMORY'],
    'executionRoleArn': live['executionRoleArn'],
    'taskRoleArn': live['taskRoleArn'],
    'containerDefinitions': container_defs,
    'volumes': [{
        'name': 'mlflow',
        'efsVolumeConfiguration': {
            'fileSystemId': fs_id,
            'transitEncryption': 'ENABLED',
            'authorizationConfig': {'accessPointId': ap_id, 'iam': 'ENABLED'},
        },
    }],
}
json.dump(out, sys.stdout)
" <<< "$LIVE_TASK_DEF")

echo "Registering $FAMILY task definition..."
aws ecs register-task-definition --cli-input-json "$MODELING_TASK_DEF" >/dev/null

COMMAND_JSON=$(uv run python -c "
import json
import sys
print(json.dumps(['python'] + sys.argv[1:]))
" "$@")

echo "Starting task: python $*"
TASK_ARN=$(aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$FAMILY" \
  --launch-type FARGATE \
  --network-configuration "$NETWORK_CONFIG" \
  --overrides "{\"containerOverrides\":[{\"name\":\"backend\",\"command\":$COMMAND_JSON}]}" \
  --query 'tasks[0].taskArn' --output text)

echo "Task ARN: $TASK_ARN"
echo "Waiting for task to stop (this blocks until the script exits)..."
aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK_ARN"

EXIT_CODE=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --query 'tasks[0].containers[0].exitCode' --output text)

echo "--- Task logs (last 20m) ---"
aws logs tail /ecs/portalpoint-backend --since 20m || true

if [ "$EXIT_CODE" != "0" ]; then
  echo "Task failed with exit code $EXIT_CODE" >&2
  exit 1
fi
echo "Task completed successfully (exit code 0)."
