# DIAGRAM 2: Complete Solution Architecture
## Full System Architecture with Infrastructure

```
╔═══════════════════════════════════════════════════════════════════════════╗
║                          CLIENT / USER LAYER                              ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────┐
│  Web Browser    │  │  Mobile App     │  │  Mobile Web     │  │  Desktop    │
│  (React SPA)    │  │  (iOS/Android)  │  │  (Responsive)   │  │  App        │
│                 │  │                 │  │                 │  │  (Electron) │
│  - Players      │  │  - Players      │  │  - Quick Access │  │  - Coaches  │
│  - Coaches      │  │  - Coaches      │  │  - Alerts       │  │  - Analysts │
│  - Analysts     │  │  - Push Notif   │  │                 │  │             │
└─────────────────┘  └─────────────────┘  └─────────────────┘  └─────────────┘
         │                    │                    │                    │
         └────────────────────┴────────────────────┴────────────────────┘
                                      │
                                      ↓
                         ┌─────────────────────────┐
                         │   CloudFront CDN        │
                         │   (Content Delivery)    │
                         │   - Static assets       │
                         │   - Edge caching        │
                         │   - SSL termination     │
                         └─────────────────────────┘
                                      │
                                      ↓
                         ┌─────────────────────────┐
                         │   AWS ALB               │
                         │   (Load Balancer)       │
                         │   - SSL/TLS             │
                         │   - Health checks       │
                         │   - Routing rules       │
                         └─────────────────────────┘
                                      │
                   ┌──────────────────┴──────────────────┐
                   │                                     │
                   ↓                                     ↓

╔═══════════════════════════════════════════════════════════════════════════╗
║                        APPLICATION LAYER (AWS ECS)                        ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────────────────────────────┐
│                          API SERVICE CLUSTER                              │
│                        (Docker Containers on ECS)                         │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  REST API Service (FastAPI)                         [3+ instances]    │
│  │  ────────────────────────────────────────────────────────────── │    │
│  │  Endpoints:                                                      │    │
│  │  • POST /api/fit-scores                                         │    │
│  │  • GET  /api/recommendations/{player_id}                        │    │
│  │  • POST /api/predictions/transfer-success                       │    │
│  │  • GET  /api/projections/team-rating                            │    │
│  │  • GET  /api/players/{player_id}                                │    │
│  │  • GET  /api/teams/{team_id}/roster                             │    │
│  │  • POST /api/compare                                            │    │
│  │                                                                  │    │
│  │  Features:                                                       │    │
│  │  • OAuth 2.0 authentication (JWT tokens)                        │    │
│  │  • Rate limiting (token bucket: 100 req/min)                    │    │
│  │  • Request validation (Pydantic models)                         │    │
│  │  • API versioning (v1, v2)                                      │    │
│  │  • OpenAPI/Swagger documentation                                │    │
│  │  • Prometheus metrics endpoint                                  │    │
│  │  • Health check endpoint                                        │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              │                                            │
│                              ↓                                            │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Model Serving Service                              [2+ instances]    │
│  │  ────────────────────────────────────────────────────────────── │    │
│  │  Models Loaded in Memory:                                       │    │
│  │  • XGBoost fit scorer (pickled model)                           │    │
│  │  • K-Means player clusterer                                     │    │
│  │  • SVD collaborative filter                                     │    │
│  │  • NIL valuation regressor                                      │    │
│  │  • Team rating projection regressor (XGBoost)                   │    │
│  │                                                                  │    │
│  │  Inference:                                                      │    │
│  │  • Batch prediction API                                         │    │
│  │  • Single prediction API (<50ms latency)                        │    │
│  │  • Model versioning (A/B testing)                               │    │
│  │  • Feature vector caching                                       │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              │                                            │
│                              ↓                                            │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  WebSocket Service (Real-Time Updates)              [2 instances]    │
│  │  ────────────────────────────────────────────────────────────── │    │
│  │  • Live portal updates (new entrants)                           │    │
│  │  • Real-time notifications                                      │    │
│  │  • Multi-room support (player rooms, coach rooms)               │    │
│  │  • Connection pooling                                           │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
                                      │
                   ┌──────────────────┴──────────────────┐
                   │                                     │
                   ↓                                     ↓

╔═══════════════════════════════════════════════════════════════════════════╗
║                      BACKGROUND PROCESSING LAYER                          ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────────────────────────────┐
│                        ASYNC WORKERS (Celery)                             │
│                         (Docker on ECS/Fargate)                           │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  ETL Worker Pool                                    [5+ workers] │    │
│  │  ────────────────────────────────────────────────────────────── │    │
│  │  Tasks:                                                          │    │
│  │  • Scrape CBBpy/hoopR daily                                     │    │
│  │  • Scrape VerbalCommits portal updates (hourly)                 │    │
│  │  • Validate and clean new data                                  │    │
│  │  • Load to PostgreSQL (upsert)                                  │    │
│  │  • Compute derived features                                     │    │
│  │  • Update materialized views                                    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  ML Training Worker Pool                             [2 workers] │    │
│  │  ────────────────────────────────────────────────────────────── │    │
│  │  Tasks:                                                          │    │
│  │  • Weekly model retraining                                      │    │
│  │  • Hyperparameter tuning (Bayesian optimization)                │    │
│  │  • Cross-validation                                             │    │
│  │  • Model evaluation and drift detection                         │    │
│  │  • Push to MLflow registry                                      │    │
│  │  • Deploy to model serving if approved                          │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Notification Worker Pool                            [3 workers] │    │
│  │  ────────────────────────────────────────────────────────────── │    │
│  │  Tasks:                                                          │    │
│  │  • Send email (via SendGrid API)                                │    │
│  │  • Send push notifications (via FCM/APNS)                       │    │
│  │  • Generate daily digest reports                                │    │
│  │  • SMS alerts (via Twilio)                                      │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Agentic Workflow Worker Pool                       [2 workers] │    │
│  │  ────────────────────────────────────────────────────────────── │    │
│  │  Tasks:                                                          │    │
│  │  • LLM-powered scouting agent (monitor portal)                  │    │
│  │  • Generate recruitment strategies                              │    │
│  │  • Multi-step reasoning with tool use                           │    │
│  │  • Web searches for player news                                 │    │
│  │  • Database queries for competitive intelligence                │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ↓
                    ┌──────────────────────────────────┐
                    │   RabbitMQ Message Broker        │
                    │   (Task Queue)                   │
                    │   - Task routing                 │
                    │   - Priority queues              │
                    │   - Dead letter queues           │
                    │   - Durable queues               │
                    └──────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║                         DATA ORCHESTRATION LAYER                          ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────────────────────────────┐
│                   Apache Airflow (Workflow Orchestration)                 │
│                            (EC2 or Fargate)                               │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  DAGs (Directed Acyclic Graphs):                                         │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  daily_data_ingestion_dag                                    │        │
│  │  ────────────────────────────────────────────────────────── │        │
│  │  Schedule: Daily @ 2 AM EST                                  │        │
│  │                                                               │        │
│  │  Tasks:                                                       │        │
│  │  1. scrape_cbbpy                                             │        │
│  │  2. scrape_hoopr                                             │        │
│  │  3. scrape_torvik                                            │        │
│  │  4. validate_data                                            │        │
│  │  5. load_to_staging                                          │        │
│  │  6. transform_features                                       │        │
│  │  7. load_to_production                                       │        │
│  │  8. refresh_materialized_views                               │        │
│  │  9. send_success_notification                                │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  hourly_portal_monitoring_dag                                │        │
│  │  ────────────────────────────────────────────────────────── │        │
│  │  Schedule: Hourly during portal season (March-August)        │        │
│  │                                                               │        │
│  │  Tasks:                                                       │        │
│  │  1. scrape_verbalcommits                                     │        │
│  │  2. detect_new_entrants                                      │        │
│  │  3. trigger_agent_analysis (calls agentic worker)            │        │
│  │  4. compute_fit_scores_for_new_players                       │        │
│  │  5. send_alerts_to_coaches                                   │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  weekly_model_training_dag                                   │        │
│  │  ────────────────────────────────────────────────────────── │        │
│  │  Schedule: Sundays @ 3 AM EST                                │        │
│  │                                                               │        │
│  │  Tasks:                                                       │        │
│  │  1. extract_training_data                                    │        │
│  │  2. train_fit_scorer                                         │        │
│  │  3. train_collaborative_filter                               │        │
│  │  4. train_nil_model                                          │        │
│  │  5. train_team_rating_model                                  │        │
│  │  6. evaluate_models                                          │        │
│  │  7. compare_vs_baseline                                      │        │
│  │  8. promote_to_mlflow_production (if improvement > 5%)       │        │
│  │  9. deploy_to_model_serving                                  │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║                         DATA STORAGE LAYER (AWS)                          ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────────────────────────────┐
│  Primary Database: Amazon RDS PostgreSQL 15                              │
│  ────────────────────────────────────────────────────────────────────    │
│  Configuration:                                                           │
│  • Instance: db.r6g.xlarge (4 vCPU, 32 GB RAM)                           │
│  • Multi-AZ deployment (high availability)                                │
│  • Automated backups (7-day retention)                                    │
│  • Read replicas (2x) for query load distribution                        │
│                                                                           │
│  Extensions:                                                              │
│  • TimescaleDB (time-series data for player stats)                       │
│  • pg_vector (vector similarity search for embeddings)                   │
│                                                                           │
│  Key Tables (see Diagram 4 for full schema):                             │
│  • players, player_season_stats, player_archetypes                       │
│  • teams, team_season_stats, team_system_profiles                        │
│  • transfers, historical_transfers                                        │
│  • player_team_fit_scores, recommendations                               │
│  • users, user_preferences, user_feedback                                │
│                                                                           │
│  Indexes:                                                                 │
│  • B-tree: (player_id, season), (team_id, season)                       │
│  • GiST: vector similarity indexes                                       │
│  • Partial: active_users, current_season_players                         │
│                                                                           │
│  Partitioning:                                                            │
│  • player_season_stats partitioned by season (2020, 2021, 2022...)      │
│  • Historical data older than 3 years archived to cold storage           │
└───────────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────────┐
│  Caching Layer: Amazon ElastiCache (Redis 7.0)                           │
│  ────────────────────────────────────────────────────────────────────    │
│  Configuration:                                                           │
│  • Instance: cache.r6g.large (2 vCPU, 13 GB RAM)                         │
│  • Cluster mode enabled (sharding)                                        │
│  • Multi-AZ with automatic failover                                       │
│                                                                           │
│  Cache Keys:                                                              │
│  • player:{player_id}:stats (TTL: 1 hour)                                │
│  • team:{team_id}:roster (TTL: 6 hours)                                  │
│  • fit_score:{player_id}:{team_id} (TTL: 30 minutes)                     │
│  • team_rating_projection:{player_id}:{team_id} (TTL: 1 hour)            │
│  • recommendations:{player_id} (TTL: 1 hour)                             │
│  • session:{session_id} (TTL: 24 hours)                                  │
│  • rate_limit:{user_id}:{minute} (TTL: 60 seconds)                       │
│                                                                           │
│  Usage Patterns:                                                          │
│  • Read-through cache for frequently accessed data                        │
│  • Write-through cache for fit scores (pre-compute + cache)              │
│  • Cache invalidation on data updates (pub/sub)                           │
└───────────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────────┐
│  Data Lake: Amazon S3                                                     │
│  ────────────────────────────────────────────────────────────────────    │
│  Buckets:                                                                 │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  portalpoint-raw-data/                                        │        │
│  │  • Raw scraped data (JSON, CSV)                             │        │
│  │  • Lifecycle: Transition to Glacier after 90 days           │        │
│  │  • Versioning enabled                                        │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  portalpoint-processed-data/                                  │        │
│  │  • Cleaned, validated data                                   │        │
│  │  • Parquet format (columnar, compressed)                     │        │
│  │  • Partitioned by season and data type                       │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  portalpoint-ml-artifacts/                                    │        │
│  │  • Trained model files (.pkl, .joblib, .h5)                 │        │
│  │  • Training datasets                                         │        │
│  │  • Model evaluation reports                                  │        │
│  │  • Versioned by training run ID                             │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  portalpoint-static-assets/                                   │        │
│  │  • Frontend build artifacts (React bundles)                  │        │
│  │  • Images, logos, fonts                                      │        │
│  │  • Served via CloudFront CDN                                │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  portalpoint-backups/                                         │        │
│  │  • Database snapshots                                        │        │
│  │  • Configuration backups                                     │        │
│  │  • Lifecycle: Glacier Deep Archive after 30 days            │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────────┐
│  ML Model Registry: MLflow (Self-Hosted on EC2)                          │
│  ────────────────────────────────────────────────────────────────────    │
│  Components:                                                              │
│  • Tracking Server: Logs experiments, parameters, metrics                │
│  • Model Registry: Versions, stages (staging/production)                 │
│  • Artifact Store: Model files stored in S3                              │
│  • Backend Store: PostgreSQL (metadata)                                   │
│                                                                           │
│  Models Tracked:                                                          │
│  • fit_scorer_xgboost (v1.0, v1.1, v2.0...)                              │
│  • collaborative_filter_svd                                              │
│  • nil_valuation_gbm                                                     │
│  • player_clusterer_kmeans                                               │
│  • bayesian_minutes_predictor                                            │
│  • team_rating_projector_xgboost                                         │
│                                                                           │
│  Each Model Entry:                                                        │
│  • Training metrics (RMSE, R², accuracy)                                 │
│  • Hyperparameters                                                        │
│  • Feature importance                                                     │
│  • Training/validation datasets (S3 URIs)                                │
│  • Deployment status (staging/production/archived)                       │
└───────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║                         EXTERNAL SERVICES LAYER                           ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌──────────┐
│  Anthropic API  │  │  SendGrid       │  │  Twilio         │  │  Sentry  │
│  (Claude LLMs)  │  │  (Email)        │  │  (SMS)          │  │  (Error  │
│                 │  │                 │  │                 │  │  Track)  │
│  • Scouting     │  │  • Alerts       │  │  • Urgent       │  │          │
│    agent        │  │  • Digests      │  │    alerts       │  │  • Error │
│  • Strategy     │  │  • Welcome      │  │  • 2FA codes    │  │    logs  │
│    generation   │  │  • Reports      │  │                 │  │  • APM   │
└─────────────────┘  └─────────────────┘  └─────────────────┘  └──────────┘

┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Auth0          │  │  Stripe         │  │  Firebase       │
│  (OAuth/SSO)    │  │  (Payments)     │  │  (Push Notif)   │
│                 │  │                 │  │                 │
│  • Social login │  │  • Subscription │  │  • FCM (Android)│
│  • JWT tokens   │  │    billing      │  │  • APNs (iOS)   │
│  • MFA          │  │  • Coach plans  │  │  • Web push     │
└─────────────────┘  └─────────────────┘  └─────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║                      MONITORING & OBSERVABILITY LAYER                     ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────────────────────────────┐
│  Prometheus + Grafana (Metrics & Dashboards)                             │
│  ────────────────────────────────────────────────────────────────────    │
│  Metrics Collected:                                                       │
│  • API request rate, latency (p50, p95, p99)                             │
│  • Model inference time                                                   │
│  • Database query performance                                             │
│  • Cache hit/miss rates                                                   │
│  • Task queue lengths                                                     │
│  • Error rates by endpoint                                                │
│                                                                           │
│  Dashboards:                                                              │
│  • System Overview (all services health)                                  │
│  • API Performance (latency, throughput)                                  │
│  • ML Model Performance (prediction accuracy, drift)                      │
│  • Business Metrics (user signups, fit score requests, recommendations)  │
└───────────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────────┐
│  AWS CloudWatch (Logs & Alarms)                                          │
│  ────────────────────────────────────────────────────────────────────    │
│  Log Groups:                                                              │
│  • /aws/ecs/portalpoint-api                                               │
│  • /aws/ecs/portalpoint-workers                                           │
│  • /aws/rds/postgresql                                                    │
│  • /aws/lambda/portal-webhooks                                           │
│                                                                           │
│  Alarms:                                                                  │
│  • High API latency (p95 > 500ms)                                        │
│  • High error rate (5xx > 1%)                                            │
│  • Database CPU > 80%                                                     │
│  • Redis memory > 90%                                                     │
│  • Model accuracy degradation                                             │
│                                                                           │
│  SNS Topics (Alerts):                                                     │
│  • critical-alerts → PagerDuty                                           │
│  • warnings → Slack #engineering                                          │
└───────────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────────┐
│  Datadog (APM & Distributed Tracing)                                     │
│  ────────────────────────────────────────────────────────────────────    │
│  • End-to-end request tracing (frontend → API → DB → model → response)  │
│  • Service dependency maps                                                │
│  • Database query analysis (slow query detection)                         │
│  • Custom business metrics                                                │
│  • Real user monitoring (RUM) for frontend                                │
└───────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║                          CI/CD PIPELINE (GitHub Actions)                  ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────────────────────────────┐
│  Development Workflow                                                     │
│  ────────────────────────────────────────────────────────────────────    │
│                                                                           │
│  Developer Push to GitHub                                                 │
│           ↓                                                               │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  1. Continuous Integration (on every push)                   │        │
│  │  ─────────────────────────────────────────────────────────  │        │
│  │  • Linting (flake8, black, isort)                           │        │
│  │  • Type checking (mypy)                                      │        │
│  │  • Unit tests (pytest, >70% coverage)                       │        │
│  │  • Integration tests                                         │        │
│  │  • Security scan (Bandit, Snyk)                             │        │
│  │  • Build Docker images                                       │        │
│  │  • Push to ECR (dev tag)                                    │        │
│  └─────────────────────────────────────────────────────────────┘        │
│           ↓                                                               │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  2. Deploy to Staging (on merge to develop branch)           │        │
│  │  ─────────────────────────────────────────────────────────  │        │
│  │  • Update ECS task definitions (staging)                     │        │
│  │  • Deploy API service (blue-green deployment)                │        │
│  │  • Run smoke tests                                           │        │
│  │  • Run E2E tests (Playwright)                                │        │
│  │  • Notify Slack #deployments                                 │        │
│  └─────────────────────────────────────────────────────────────┘        │
│           ↓                                                               │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  3. Deploy to Production (on merge to main branch)           │        │
│  │  ─────────────────────────────────────────────────────────  │        │
│  │  • Manual approval required                                  │        │
│  │  • Tag release version (semantic versioning)                 │        │
│  │  • Update ECS task definitions (production)                  │        │
│  │  • Rolling deployment (0% downtime)                          │        │
│  │  • Health checks at each step                                │        │
│  │  • Automatic rollback if health checks fail                  │        │
│  │  • Post-deployment smoke tests                               │        │
│  │  • Update documentation site                                 │        │
│  │  • Notify Slack #deployments + create Jira release           │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║                          SECURITY & COMPLIANCE                            ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────────────────────────────┐
│  Security Measures                                                        │
│  ────────────────────────────────────────────────────────────────────    │
│  • VPC with private subnets (databases not public)                       │
│  • Security groups (least privilege access)                               │
│  • Secrets Manager (API keys, DB credentials)                            │
│  • WAF (Web Application Firewall) on ALB                                 │
│  • DDoS protection (AWS Shield Standard)                                  │
│  • SSL/TLS everywhere (end-to-end encryption)                            │
│  • Regular security audits (Snyk, Dependabot)                            │
│  • GDPR compliant (data retention policies, right to deletion)           │
│  • FERPA compliant (student data protection)                             │
│  • SOC 2 Type II certification (target)                                   │
└───────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║                          DISASTER RECOVERY                                ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────────────────────────────┐
│  Backup & Recovery Strategy                                              │
│  ────────────────────────────────────────────────────────────────────    │
│  • RTO (Recovery Time Objective): 1 hour                                 │
│  • RPO (Recovery Point Objective): 5 minutes                             │
│                                                                           │
│  Database:                                                                │
│  • Automated daily snapshots (7-day retention)                           │
│  • Continuous replication to read replica (Multi-AZ)                     │
│  • Point-in-time recovery (up to 35 days)                                │
│  • Cross-region snapshot copy (DR region: us-west-2)                     │
│                                                                           │
│  Application:                                                             │
│  • Infrastructure as Code (Terraform) in version control                 │
│  • Docker images versioned in ECR                                        │
│  • Configuration in version control                                       │
│  • Runbook for disaster recovery scenarios                               │
│                                                                           │
│  Data Lake:                                                               │
│  • S3 versioning enabled                                                  │
│  • Cross-region replication                                              │
│  • MFA delete protection                                                  │
└───────────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════
                           ARCHITECTURE SUMMARY
═══════════════════════════════════════════════════════════════════════════

COMPUTE:
├─ ECS Fargate: API services, workers (auto-scaling)
├─ EC2: Airflow, MLflow (reserved instances for cost optimization)
└─ Lambda: Webhooks, scheduled tasks (event-driven)

STORAGE:
├─ RDS PostgreSQL: Transactional data, user data, fit scores
├─ ElastiCache Redis: Session store, cache, rate limiting
├─ S3: Data lake (raw/processed), ML artifacts, static assets
└─ EFS: Shared file system (Airflow DAGs, logs)

NETWORKING:
├─ Route 53: DNS management
├─ CloudFront: CDN for static assets
├─ ALB: Load balancing, SSL termination
├─ VPC: Network isolation, private/public subnets
└─ Direct Connect: Dedicated connection (if needed for large data)

MONITORING:
├─ CloudWatch: Logs, metrics, alarms
├─ Prometheus + Grafana: Custom metrics, dashboards
├─ Datadog: APM, distributed tracing
└─ Sentry: Error tracking, performance monitoring

ML OPS:
├─ MLflow: Model versioning, experiment tracking
├─ S3: Model artifact storage
├─ Airflow: Training pipeline orchestration
└─ ECS: Model serving containers

ESTIMATED COSTS (Monthly):
├─ Compute (ECS + EC2): $800-1,200
├─ RDS (db.r6g.xlarge): $400-600
├─ ElastiCache (cache.r6g.large): $200-300
├─ S3 Storage (1TB): $25-50
├─ Data Transfer: $100-200
├─ CloudFront: $50-100
├─ Monitoring Tools: $100-200
├─ External APIs (Anthropic, Auth0, etc.): $200-400
└─ TOTAL: ~$2,000-3,000/month (early stage)

SCALABILITY:
├─ Horizontal: ECS auto-scaling (CPU/memory triggers)
├─ Vertical: RDS instance upgrades, cache size increases
├─ Geographic: Multi-region deployment (future)
└─ Traffic: Can handle 100K+ daily active users with current architecture
```

## Architecture Principles

1. **Microservices**: Separation of concerns (API, model serving, workers)
2. **Stateless Services**: All application state in databases/cache (enables scaling)
3. **Async Processing**: Long-running tasks offloaded to workers
4. **Caching Layers**: Multiple levels (CDN, Redis, database) for performance
5. **Observability**: Comprehensive monitoring at every layer
6. **Infrastructure as Code**: All infrastructure defined in Terraform
7. **Blue-Green Deployments**: Zero-downtime releases
8. **Auto-Scaling**: Dynamic resource allocation based on demand
9. **Security by Design**: Defense in depth, least privilege access
10. **Cost Optimization**: Right-sizing, reserved instances, S3 lifecycle policies
