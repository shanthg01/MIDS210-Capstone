# Production Deployment — CLI Command Reference

Companion to `docs/road_to_production.md`. Concrete commands for each phase, using
AWS CLI v2, Docker CLI, and GitHub CLI. Placeholders in `<ANGLE_BRACKETS>` — everything
else is a real value already confirmed in `docs/status/ARCHITECTURE_STATUS.md`.

**Status (2026-07-21):** Phases 1-4 and all of Phase 6 (including real ElastiCache, 6c) have been run
for real against the live infra account — not just planned. Phase 5 was explicitly skipped by decision
(see `docs/road_to_production.md`). See that doc's per-phase status notes for what's done vs.
outstanding, and `docs/aws_rds_setup.md` for the finalized teammate-facing SSM tunnel workflow (the
canonical doc for that — don't duplicate tunnel instructions here, this file stays the one-time
infra-setup command log). Real near-miss during Phase 3: the ALB target group's health check was
pointed at `/ready` before that endpoint existed in the app at all — caught before the ECS service went
live; `src/portalpoint/main.py` now has a real `SELECT 1`-backed `/ready`. Real prerequisite found
during Phase 4: `npm run build` had never been run before (`npm run dev` doesn't invoke `tsc`) — 92
pre-existing TypeScript errors surfaced and were fixed before the frontend could be built for
deployment at all. **Post-launch incident (2026-07-20):** `alembic upgrade head` was never actually run
against RDS after the `origin/main` merge — see 3b's two IAM gotchas below and `docs/status/STATUS.md`'s
2026-07-20 incident entry for the full trail (broken login/signup + 5+ minute page hangs, both fixed).
**Fixed for good in `deploy.yml`, 2026-07-21 (PR #65 review):** the deploy workflow now runs
`alembic upgrade head` as a one-off ECS task that must exit 0 before `update-service` proceeds — no
longer a manual step someone can forget. Same review also caught `uv.lock` being gitignored and never
committed (Docker builds were broken for any fresh clone/CI, worked locally only by accident).

Known values used below:
- Region: `us-east-1`
- Infra account: `424056758764`
- RDS security group: `sg-0ec78cb4f641ee901`
- Bastion security group: `sg-06d79bdd59fea641a`
- Bastion instance ID: `i-0a6e1bafc1cb6f379`
- RDS endpoint: `portalpoint-db.con8amymqi1e.us-east-1.rds.amazonaws.com`
- S3 bucket (data/models): `portalpoint-data`
- S3 bucket (frontend static site): `portalpoint-frontend`
- CloudFront distribution: `E2HF7HKH8Y1FKD`
- CloudWatch alarm: `portalpoint-unhealthy-targets` → SNS topic `portalpoint-alerts`
- ElastiCache cluster: `portalpoint-cache` (`portalpoint-cache.cprchk.0001.use1.cache.amazonaws.com:6379`)

Run these from a shell with `aws configure` already set to an account with sufficient
IAM permissions (or ask Justin for a deploy-scoped IAM user/role). None of these commands
should be run against shared infra without confirming with the team first — several are
one-way (Multi-AZ conversion triggers a reboot-equivalent failover test).

---

## Phase 1 — Foundation

### 1a. Discover the existing VPC/subnets (RDS already lives here)

```bash
# Get the VPC ID from the known RDS security group
aws ec2 describe-security-groups \
  --group-ids sg-0ec78cb4f641ee901 \
  --query 'SecurityGroups[0].VpcId' --output text

# List subnets in that VPC (identify which are private vs public)
aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=<VPC_ID>" \
  --query 'Subnets[].{Id:SubnetId,AZ:AvailabilityZone,CIDR:CidrBlock,Public:MapPublicIpOnLaunch}' \
  --output table

# Confirm a NAT gateway exists already (if not, create one — see 1b)
aws ec2 describe-nat-gateways \
  --filter "Name=vpc-id,Values=<VPC_ID>" \
  --output table
```

### 1b. Create NAT gateway (only if none exists)

```bash
# Allocate an Elastic IP for the NAT gateway
aws ec2 allocate-address --domain vpc --query 'AllocationId' --output text

# Create the NAT gateway in a public subnet
aws ec2 create-nat-gateway \
  --subnet-id <PUBLIC_SUBNET_ID> \
  --allocation-id <ALLOCATION_ID> \
  --query 'NatGateway.NatGatewayId' --output text

# Add a route in the private route table pointing 0.0.0.0/0 -> NAT gateway
aws ec2 create-route \
  --route-table-id <PRIVATE_ROUTE_TABLE_ID> \
  --destination-cidr-block 0.0.0.0/0 \
  --nat-gateway-id <NAT_GATEWAY_ID>
```

### 1c. S3 gateway endpoint (free — keeps model/artifact traffic off the NAT bill)

```bash
aws ec2 create-vpc-endpoint \
  --vpc-id <VPC_ID> \
  --service-name com.amazonaws.us-east-1.s3 \
  --route-table-ids <PRIVATE_ROUTE_TABLE_ID> \
  --vpc-endpoint-type Gateway
```

### 1d. ECR repository + first image push

```bash
# Create the repo
aws ecr create-repository \
  --repository-name portalpoint-backend \
  --image-scanning-configuration scanOnPush=true

# Authenticate Docker to ECR
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com

# Build (from repo root, after Dockerfile exists)
docker build -t portalpoint-backend:latest .

# Tag and push
docker tag portalpoint-backend:latest \
  <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/portalpoint-backend:latest
docker push <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/portalpoint-backend:latest
```

### 1e. Secrets Manager — create the app secrets

```bash
aws secretsmanager create-secret \
  --name portalpoint/database-url \
  --secret-string "postgresql+asyncpg://portalpoint_app:<PASSWORD>@portalpoint-db.con8amymqi1e.us-east-1.rds.amazonaws.com:5432/portalpoint?ssl=require"

aws secretsmanager create-secret \
  --name portalpoint/jwt-secret \
  --secret-string "<GENERATE_A_LONG_RANDOM_VALUE>"

# Real env var names confirmed from src/portalpoint/agents/news_monitoring/runner.py
# (TAVILY_API_KEY, GOOGLE_API_KEY — not "gemini-api-key", corrected 2026-07-21 after
# checking the actual code once the news-monitoring PR merged in). Created
# 2026-07-21, done — these were originally deferred pending that merge.
aws secretsmanager create-secret \
  --name portalpoint/tavily-api-key \
  --secret-string "<TAVILY_KEY>"

aws secretsmanager create-secret \
  --name portalpoint/google-api-key \
  --secret-string "<GEMINI_KEY>"
```

Note: `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` do **not** need a secret — grant the
ECS task role S3 permissions directly (IAM role, no static keys) instead. See 3b.

### 1f. GitHub Actions OIDC role (no long-lived AWS keys in GitHub secrets)

```bash
# One-time: register GitHub's OIDC provider with your AWS account (skip if already present)
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1

# Create the deploy role (trust policy file shown below)
aws iam create-role \
  --role-name portalpoint-gha-deploy \
  --assume-role-policy-document file://gha-trust-policy.json

# Attach ECR push + ECS deploy permissions (scope down from *FullAccess in real use)
aws iam attach-role-policy --role-name portalpoint-gha-deploy \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser
aws iam attach-role-policy --role-name portalpoint-gha-deploy \
  --policy-arn arn:aws:iam::aws:policy/AmazonECS_FullAccess
```

`gha-trust-policy.json`:
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike": { "token.actions.githubusercontent.com:sub": "repo:<GH_ORG>/<GH_REPO>:ref:refs/heads/main" }
    }
  }]
}
```

Then wire it into a workflow step (no CLI — this is the YAML the OIDC role above enables):
```yaml
- uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: arn:aws:iam::<ACCOUNT_ID>:role/portalpoint-gha-deploy
    aws-region: us-east-1
```

---

## Phase 2 — DB connectivity

### 2a. Create the ECS task security group

```bash
aws ec2 create-security-group \
  --group-name portalpoint-ecs-task-sg \
  --description "PortalPoint ECS Fargate tasks" \
  --vpc-id <VPC_ID> \
  --query 'GroupId' --output text
```

### 2b. Scope RDS ingress to that SG only (source-group rule, matches the existing bastion pattern)

```bash
aws ec2 authorize-security-group-ingress \
  --group-id sg-0ec78cb4f641ee901 \
  --protocol tcp --port 5432 \
  --source-group <ECS_TASK_SG_ID>
```

### 2c. Enable Multi-AZ (confirm with team first — triggers a brief failover-test event)

```bash
aws rds describe-db-instances \
  --query 'DBInstances[].{Id:DBInstanceIdentifier,MultiAZ:MultiAZ}' --output table

aws rds modify-db-instance \
  --db-instance-identifier <RDS_INSTANCE_ID> \
  --multi-az \
  --apply-immediately
```

### 2d. Replace bastion SSH with SSM Session Manager (done 2026-07-20)

Real findings from running this against `i-0a6e1bafc1cb6f379`, worth knowing before repeating on
another instance: (1) the bastion had **no IAM instance profile attached at all** — had to create
one from scratch, not just attach a policy to an existing role. (2) The AL2023 AMI didn't ship the
SSM agent preinstalled — `dnf install -y amazon-ssm-agent` was needed before it could register.
(3) The existing SSH ingress rule turned out to be `0.0.0.0/0` — open to the entire internet, not a
scoped per-team rule — so this wasn't just a hygiene improvement, it closed a real exposure.

```bash
# Only needed if the instance has no profile yet — check first:
aws ec2 describe-instances --instance-ids i-0a6e1bafc1cb6f379 \
  --query 'Reservations[0].Instances[0].IamInstanceProfile'

aws iam create-role --role-name portalpoint-bastion-role \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam create-instance-profile --instance-profile-name portalpoint-bastion-profile
aws iam add-role-to-instance-profile \
  --instance-profile-name portalpoint-bastion-profile \
  --role-name portalpoint-bastion-role
aws ec2 associate-iam-instance-profile \
  --instance-id i-0a6e1bafc1cb6f379 \
  --iam-instance-profile Name=portalpoint-bastion-profile

# Attach the SSM managed instance policy
aws iam attach-role-policy \
  --role-name portalpoint-bastion-role \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore

# If the SSM agent was never installed on this AMI, install it via SSH (last time you'll need SSH):
# ssh -i portalpoint-bastion.pem ec2-user@<bastion-public-ip>
# sudo dnf install -y amazon-ssm-agent && sudo systemctl enable --now amazon-ssm-agent

# Confirm the agent is online before touching the SSH rule
aws ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=i-0a6e1bafc1cb6f379" \
  --query 'InstanceInformationList[0].PingStatus' --output text   # must say "Online"

# Install session-manager-plugin locally (once per machine), then verify:
aws ssm start-session --target i-0a6e1bafc1cb6f379

# Only once the above works: remove the SSH ingress rule (was 0.0.0.0/0)
aws ec2 revoke-security-group-ingress \
  --group-id sg-06d79bdd59fea641a \
  --protocol tcp --port 22 --cidr 0.0.0.0/0
```

**Teammate access, post-migration** (not a `ssm start-session` shell — a port-forward tunnel replacing
the old `ssh -L`; see `docs/aws_rds_setup.md` for the full, canonical teammate-facing version):

```bash
aws iam create-group --group-name PortalPoint-Dev   # infra account — separate from the same-named
                                                       # group in Justin's S3 bucket-owner account
aws iam put-group-policy --group-name PortalPoint-Dev \
  --policy-name PortalPointSSMBastionAccess \
  --policy-document file://ssm-bastion-policy.json

aws iam create-user --user-name <firstname>-portalpoint-infra
aws iam add-user-to-group --group-name PortalPoint-Dev --user-name <firstname>-portalpoint-infra
aws iam create-access-key --user-name <firstname>-portalpoint-infra   # send secret via secure DM
```

---

## Phase 3 — Backend hosting (ECS Fargate + ALB)

### 3a. ECS cluster

```bash
aws ecs create-cluster --cluster-name portalpoint-prod
```

### 3b. Task execution + task role

**Two real gotchas found the hard way on 2026-07-20 — both silent until a task actually tries to
start, both fixed below, don't skip either:**

1. **Secrets Manager `valueFrom` must use the full ARN (with the random suffix), not the partial
   ARN.** IAM's authorization check for `secretsmanager:GetSecretValue` evaluates against the
   literal ARN string ECS uses in the API call — not the resolved secret — so a policy resource
   like `...:secret:portalpoint/jwt-secret-*` never matches a partial ARN `...:secret:portalpoint/jwt-secret`
   (no trailing hyphen for the wildcard to extend from). Get the real ARNs first:
   ```bash
   aws secretsmanager describe-secret --secret-id portalpoint/database-url --query ARN --output text
   aws secretsmanager describe-secret --secret-id portalpoint/jwt-secret --query ARN --output text
   ```
   Use those full values (e.g. `...:secret:portalpoint/jwt-secret-CxF6qe`) in `task-def.json`'s
   `secrets[].valueFrom` in 3c — not the partial form shown in earlier drafts of this doc.

2. **`AmazonECSTaskExecutionRolePolicy` (the AWS-managed policy attached below) does NOT include
   `logs:CreateLogGroup`** — only `CreateLogStream`/`PutLogEvents` — despite `task-def.json` (3c)
   setting `awslogs-create-group: true`. Needs a small additional inline policy:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
       "Resource": "arn:aws:logs:us-east-1:424056758764:log-group:/ecs/portalpoint-backend:*"
     }]
   }
   ```
   (save as `logs-policy.json`, gitignored — same pattern as the other one-time policy files)

Without either fix, `describe-services` shows `Running: 0` forever with
`ResourceInitializationError` events (secrets) or `failed to create Cloudwatch log group` — the
task never even starts the app, so `/ready` never gets a chance to matter.

```bash
# Execution role — lets ECS pull the image, read secrets, and write logs
aws iam create-role --role-name portalpoint-ecs-execution \
  --assume-role-policy-document file://ecs-trust-policy.json
aws iam attach-role-policy --role-name portalpoint-ecs-execution \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam put-role-policy --role-name portalpoint-ecs-execution \
  --policy-name portalpoint-secrets-read \
  --policy-document file://secrets-read-policy.json
aws iam put-role-policy --role-name portalpoint-ecs-execution \
  --policy-name portalpoint-logs-write \
  --policy-document file://logs-policy.json

# Task role — the running app's own AWS permissions (S3 read/write to portalpoint-data,
# replaces static AWS_ACCESS_KEY_ID/SECRET entirely)
aws iam create-role --role-name portalpoint-ecs-task \
  --assume-role-policy-document file://ecs-trust-policy.json
aws iam put-role-policy --role-name portalpoint-ecs-task \
  --policy-name portalpoint-s3-access \
  --policy-document file://s3-access-policy.json
```

`ecs-trust-policy.json` (same for both roles):
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "ecs-tasks.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
```

### 3c. Register the task definition

```bash
aws ecs register-task-definition --cli-input-json file://task-def.json
```

`task-def.json` (trimmed):
```json
{
  "family": "portalpoint-backend",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/portalpoint-ecs-execution",
  "taskRoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/portalpoint-ecs-task",
  "containerDefinitions": [{
    "name": "backend",
    "image": "<ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/portalpoint-backend:latest",
    "portMappings": [{ "containerPort": 8000 }],
    "secrets": [
      { "name": "DATABASE_URL", "valueFrom": "arn:aws:secretsmanager:us-east-1:<ACCOUNT_ID>:secret:portalpoint/database-url" },
      { "name": "JWT_SECRET", "valueFrom": "arn:aws:secretsmanager:us-east-1:<ACCOUNT_ID>:secret:portalpoint/jwt-secret" }
    ],
    "environment": [
      { "name": "S3_BUCKET", "value": "portalpoint-data" },
      { "name": "AWS_DEFAULT_REGION", "value": "us-east-1" }
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/portalpoint-backend",
        "awslogs-region": "us-east-1",
        "awslogs-stream-prefix": "backend"
      }
    }
  }]
}
```

### 3d. ALB + target group (health check on `/ready`, once that endpoint exists)

```bash
aws elbv2 create-load-balancer \
  --name portalpoint-alb \
  --subnets <PUBLIC_SUBNET_ID_1> <PUBLIC_SUBNET_ID_2> \
  --security-groups <ALB_SG_ID> \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text

aws elbv2 create-target-group \
  --name portalpoint-tg \
  --protocol HTTP --port 8000 \
  --vpc-id <VPC_ID> \
  --target-type ip \
  --health-check-path /ready \
  --health-check-interval-seconds 15 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3 \
  --query 'TargetGroups[0].TargetGroupArn' --output text

aws elbv2 create-listener \
  --load-balancer-arn <ALB_ARN> \
  --protocol HTTP --port 80 \
  --default-actions Type=forward,TargetGroupArn=<TARGET_GROUP_ARN>
```

### 3e. ECS service

```bash
aws ecs create-service \
  --cluster portalpoint-prod \
  --service-name portalpoint-backend \
  --task-definition portalpoint-backend \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<PRIVATE_SUBNET_ID_1>,<PRIVATE_SUBNET_ID_2>],securityGroups=[<ECS_TASK_SG_ID>],assignPublicIp=DISABLED}" \
  --load-balancers targetGroupArn=<TARGET_GROUP_ARN>,containerName=backend,containerPort=8000
```

### 3f. Deploy a new image after a code change (repeat of 1d push, then force new deployment)

```bash
aws ecs update-service \
  --cluster portalpoint-prod \
  --service portalpoint-backend \
  --force-new-deployment
```

---

## Phase 4 — Frontend hosting (S3 + CloudFront)

**Live URL: https://d331zwrxbrp79d.cloudfront.net**

**Status (2026-07-20): done, real distribution live (`E2HF7HKH8Y1FKD`).** Went with the full CLI path
below (OAC + two-origin distribution config) rather than the console shortcut originally suggested —
worked fine scripted. Real prerequisite hit first: `npm run build` had never been run before this
session (`npm run dev` doesn't invoke `tsc`), surfacing 92 pre-existing TypeScript errors that had to be
fixed before the build would even produce a `dist/` — see the frontend-build-fix commits on `main`.

### 4a. S3 bucket for the static build

```bash
aws s3 mb s3://portalpoint-frontend --region us-east-1
aws s3api put-public-access-block \
  --bucket portalpoint-frontend \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

### 4b. Build and upload

```bash
cd frontend
npm run build
aws s3 sync dist/ s3://portalpoint-frontend --delete
```

### 4c. Origin Access Control + CloudFront distribution

Done via CLI, not the console — see `cloudfront-config.json` and `s3-bucket-policy.json` (both
gitignored, account-specific) for the full two-origin config: default behavior serves S3
(`CachingOptimized`), `/api/*` forwards to the ALB (`CachingDisabled` + `AllViewerExceptHostHeader` so
`Authorization` headers pass through), `CustomErrorResponses` reroute 403/404 → `/index.html` for
React Router.

```bash
# 1. OAC
aws cloudfront create-origin-access-control \
  --origin-access-control-config Name=portalpoint-oac,SigningProtocol=sigv4,SigningBehavior=always,OriginAccessControlOriginType=s3 \
  --query 'OriginAccessControl.Id' --output text

# 2. ALB DNS name (second origin)
aws elbv2 describe-load-balancers --names portalpoint-alb --query 'LoadBalancers[0].DNSName' --output text

# 3. Create the distribution (cloudfront-config.json has both origins + behaviors filled in)
aws cloudfront create-distribution \
  --distribution-config file://cloudfront-config.json \
  --query '{Id:Distribution.Id,ARN:Distribution.ARN,Domain:Distribution.DomainName}'

# 4. Grant that specific distribution read access to the bucket (s3-bucket-policy.json's
#    Condition scopes to the distribution ARN from step 3 — not a public bucket policy)
aws s3api put-bucket-policy --bucket portalpoint-frontend --policy file://s3-bucket-policy.json

# 5. Poll until deployed
aws cloudfront get-distribution --id E2HF7HKH8Y1FKD --query 'Distribution.Status' --output text
```

Invalidate after every future deploy (CloudFront caches aggressively):

```bash
aws cloudfront create-invalidation \
  --distribution-id E2HF7HKH8Y1FKD \
  --paths "/*"
```

**Not done:** no CI step for this — build/sync/invalidate is still a manual 3-command sequence per
deploy, unlike the backend's `deploy.yml`.

---

## Phase 5 — Scheduled jobs (ECS Scheduled Tasks via EventBridge, not GH Actions cron)

**Status (2026-07-20): explicitly skipped by decision — nothing below has been run.** No automated
freshness need exists yet (manual ingest reruns are fine for now); revisit if a real user needs
fresher-than-manual data or the news-monitoring agent needs continuous operation during the portal
window. See `docs/road_to_production.md` Phase 5.

GitHub-hosted runners can't reach the private RDS instance — run scheduled ingest/model
jobs as ECS tasks on the same image instead.

```bash
# Register a task def per script (or one def, override the container command per rule)
aws ecs register-task-definition --cli-input-json file://task-def-ingest.json

# Create the schedule (example: nightly ingest at 2 AM EST = 07:00 UTC)
aws events put-rule \
  --name portalpoint-nightly-ingest \
  --schedule-expression "cron(0 7 * * ? *)"

aws events put-targets \
  --rule portalpoint-nightly-ingest \
  --targets '[{
    "Id": "portalpoint-ingest-task",
    "Arn": "arn:aws:ecs:us-east-1:<ACCOUNT_ID>:cluster/portalpoint-prod",
    "RoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/portalpoint-events-ecs-role",
    "EcsParameters": {
      "TaskDefinitionArn": "arn:aws:ecs:us-east-1:<ACCOUNT_ID>:task-definition/portalpoint-ingest",
      "LaunchType": "FARGATE",
      "NetworkConfiguration": {
        "awsvpcConfiguration": {
          "Subnets": ["<PRIVATE_SUBNET_ID_1>"],
          "SecurityGroups": ["<ECS_TASK_SG_ID>"],
          "AssignPublicIp": "DISABLED"
        }
      }
    }
  }]'
```

---

## Phase 6 — Observability

**Status (2026-07-21): 6a/6b (the alarm) and 6c (real ElastiCache, added after the plan's original
Cache deferral) both done; everything else in `docs/road_to_production.md` Phase 6 (Sentry,
Prometheus/Grafana, drift detection) explicitly skipped.**
`TARGET_GROUP_ARN_SUFFIX`/`ALB_ARN_SUFFIX` are not the full ARNs used elsewhere in this doc — CloudWatch
dimensions want the ARN suffix (everything after the account ID), e.g.
`targetgroup/portalpoint-tg/<id>` and `app/portalpoint-alb/<id>`; get them via
`aws elbv2 describe-target-groups`/`describe-load-balancers` and trim.

### 6a. CloudWatch alarm on unhealthy target count

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name portalpoint-unhealthy-targets \
  --namespace AWS/ApplicationELB \
  --metric-name UnHealthyHostCount \
  --dimensions Name=TargetGroup,Value=<TARGET_GROUP_ARN_SUFFIX> Name=LoadBalancer,Value=<ALB_ARN_SUFFIX> \
  --statistic Average --period 60 --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --evaluation-periods 2 \
  --alarm-actions <SNS_TOPIC_ARN>
```

### 6b. SNS topic for alerts (if none exists)

```bash
aws sns create-topic --name portalpoint-alerts --query 'TopicArn' --output text
aws sns subscribe --topic-arn <SNS_TOPIC_ARN> --protocol email --notification-endpoint <ALERT_EMAIL>
```

### 6c. Real ElastiCache Redis (done 2026-07-21, resolves the Cache deferral above)

Reuses the existing private subnets + ECS task SG (Phase 1/2) — no new networking beyond a subnet
group + a cache-specific SG. **Real gotcha found here:** the cache SG needs to allow inbound 6379 from
**both** the ECS task SG (app traffic) *and* the bastion SG (debugging via an SSM tunnel, same
mechanism as the RDS tunnel) — only adding the first one produces an instant "connection refused" on
any tunnel attempt (the tell that it's a local/SG problem, not a remote timeout).

```bash
aws elasticache create-cache-subnet-group \
  --cache-subnet-group-name portalpoint-cache-subnets \
  --cache-subnet-group-description "PortalPoint ElastiCache" \
  --subnet-ids <PRIVATE_SUBNET_ID_1> <PRIVATE_SUBNET_ID_2>

aws ec2 create-security-group \
  --group-name portalpoint-cache-sg \
  --description "PortalPoint ElastiCache Redis" \
  --vpc-id <VPC_ID> \
  --query 'GroupId' --output text

aws ec2 authorize-security-group-ingress \
  --group-id <CACHE_SG_ID> --protocol tcp --port 6379 --source-group <ECS_TASK_SG_ID>
aws ec2 authorize-security-group-ingress \
  --group-id <CACHE_SG_ID> --protocol tcp --port 6379 --source-group sg-06d79bdd59fea641a

aws elasticache create-cache-cluster \
  --cache-cluster-id portalpoint-cache \
  --engine redis \
  --cache-node-type cache.t3.micro \
  --num-cache-nodes 1 \
  --cache-subnet-group-name portalpoint-cache-subnets \
  --security-group-ids <CACHE_SG_ID>

# Wait for Status: available, then get the endpoint
aws elasticache describe-cache-clusters --cache-cluster-id portalpoint-cache --show-cache-node-info \
  --query 'CacheClusters[0].{Status:CacheClusterStatus,Endpoint:CacheNodes[0].Endpoint}'
```

Add to `task-def.json`'s `environment` (plain value — no auth token on this cluster, not a secret):
```json
{ "name": "REDIS_URL", "value": "redis://<CACHE_ENDPOINT>:6379" }
```
Then `register-task-definition` + `update-service --force-new-deployment` as usual.

**Debugging via the same tunnel pattern as RDS** (needed to inspect stored cache keys directly, e.g.
the news-monitoring agent's run-status records):
```bash
aws ssm start-session --target i-0a6e1bafc1cb6f379 \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters file://ssm-cache-tunnel-params.json   # {"host":["<cache-endpoint>"],"portNumber":["6379"],"localPortNumber":["6380"]}
```

---

## Local verification commands (run before touching any shared infra)

```bash
# Build and run the container locally against the tunnel, before ever pushing to ECR
docker build -t portalpoint-backend:local .
docker run -p 8000:8000 \
  -e DATABASE_URL="postgresql+asyncpg://portalpoint_app:<PW>@host.docker.internal:5433/portalpoint?ssl=require" \
  -e JWT_SECRET="local-test-secret" \
  portalpoint-backend:local

curl http://localhost:8000/health
curl http://localhost:8000/ready   # once the endpoint exists
```
