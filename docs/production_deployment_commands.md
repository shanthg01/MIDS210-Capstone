# Production Deployment — CLI Command Reference

Companion to `docs/road_to_production.md`. Concrete commands for each phase, using
AWS CLI v2, Docker CLI, and GitHub CLI. Placeholders in `<ANGLE_BRACKETS>` — everything
else is a real value already confirmed in `docs/status/ARCHITECTURE_STATUS.md`.

Known values used below:
- Region: `us-east-1`
- RDS security group: `sg-0ec78cb4f641ee901`
- Bastion security group: `sg-06d79bdd59fea641a`
- RDS endpoint: `portalpoint-db.con8amymqi1e.us-east-1.rds.amazonaws.com`
- S3 bucket: `portalpoint-data`

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

aws secretsmanager create-secret \
  --name portalpoint/tavily-api-key \
  --secret-string "<TAVILY_KEY>"

aws secretsmanager create-secret \
  --name portalpoint/gemini-api-key \
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

### 2d. Replace bastion SSH with SSM Session Manager (break-glass only, no open port 22)

```bash
# Attach the SSM managed instance policy to the bastion's instance role
aws iam attach-role-policy \
  --role-name <BASTION_INSTANCE_ROLE> \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore

# Connect without SSH once the SSM agent checks in (no .pem, no open 22)
aws ssm start-session --target <BASTION_INSTANCE_ID>

# Once SSM access is confirmed working, remove the SSH ingress rule
aws ec2 revoke-security-group-ingress \
  --group-id sg-06d79bdd59fea641a \
  --protocol tcp --port 22 --cidr <CURRENT_ALLOWED_CIDR>
```

---

## Phase 3 — Backend hosting (ECS Fargate + ALB)

### 3a. ECS cluster

```bash
aws ecs create-cluster --cluster-name portalpoint-prod
```

### 3b. Task execution + task role

```bash
# Execution role — lets ECS pull the image and read secrets
aws iam create-role --role-name portalpoint-ecs-execution \
  --assume-role-policy-document file://ecs-trust-policy.json
aws iam attach-role-policy --role-name portalpoint-ecs-execution \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam put-role-policy --role-name portalpoint-ecs-execution \
  --policy-name portalpoint-secrets-read \
  --policy-document file://secrets-read-policy.json

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

### 4c. CloudFront distribution — recommend the console for the initial distribution
(origin-access-control + path-based `/api/*` behavior routing to the ALB is easiest to get
right interactively). Once created, note the distribution ID for future invalidations:

```bash
aws cloudfront create-invalidation \
  --distribution-id <DISTRIBUTION_ID> \
  --paths "/*"
```

---

## Phase 5 — Scheduled jobs (ECS Scheduled Tasks via EventBridge, not GH Actions cron)

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
