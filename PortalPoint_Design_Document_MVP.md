# PortalPoint: Transfer Portal Decision Platform
## Design Document (MVP)

**Date:** May 22, 2026  
**Status:** Design Review  

---

## Executive Summary

PortalPoint is a data-driven recommendation platform helping college basketball players make optimal transfer portal decisions. The MVP tests our core hypothesis: **multi-dimensional fit analysis provides measurably better transfer decisions than subjective evaluation or generic rankings**. We address a $5-10M market opportunity serving 2,500+ annual portal entrants who currently lack analytical tools to evaluate 350+ potential destinations.

**The Problem:** Players face career-defining transfer decisions with fragmented information, limited time, and high uncertainty about playing time, system fit, and future outcomes. Poor decisions cost players opportunities, development, and NIL earnings.

**Our Solution:** Automated fit scoring and personalized recommendations analyzing four dimensions—Gap Matching, Scheme Fit, Playing Opportunity, and Personal Fit—delivered through an intuitive web dashboard.

**Success Metrics:** 
- 40%+ hit rate (actual destination in top-5 recommendations)
- 70%+ of users report platform influenced decision
- NPS > 40
- <3.5 PER points prediction error

---

## 1. Goals & Non-Goals

### Goals (MVP)

**Primary Goal:** Validate that quantitative fit analysis changes player transfer decisions for the better, measured by user adoption, decision alignment, and predictive accuracy.

**Secondary Goals:**
- Demonstrate technical feasibility of real-time fit scoring at scale (2,500 players × 350 schools = 875,000 pairings)
- Establish data pipeline collecting and processing NCAA basketball statistics daily
- Build brand credibility through transparent, accurate predictions validated against actual outcomes
- Create foundation for future expansion (coaches, other sports, premium features)

### Stretch-Goals

- Coach-facing tools (focus: players only)
- NIL valuation estimation (secondary feature, limited data availability)
- Video integration or analysis
- Native mobile apps (responsive web sufficient)
- In-platform messaging between players and coaches
- Multi-sport support beyond men's basketball
- Real-time roster tracking (daily updates sufficient)
- Advanced scenario planning ("what-if" simulations)

---

## 2. User Experience Overview

### Target User: Transfer Portal Players

**Primary Persona:** Junior guard at mid-major program averaging 14 PPG, considering transfer for more playing time and better NIL opportunities. Has limited time to research (2-3 weeks), needs to narrow 350 schools to realistic shortlist of 8-10, wants data-backed confidence in final decision.

**User Journey:**

```
Entry → Profile Creation → Exploration → Comparison → Decision
  ↓          ↓               ↓            ↓            ↓
Login    Set prefs      Browse fits   Side-by-side   Commit
         (location,     Get top-10    Compare 3-4    (off-platform)
         playing time,  recommendations schools
         NIL importance)
```

### Core User Flows

**Flow 1: New User Onboarding (5 minutes)**
1. Create account with email/social login
2. Link to player profile (search by name, verify identity)
3. Set priorities via sliders: Playing Time (10), NIL (8), Location (6), Academics (5)
4. Optional: Add hometown, desired major, regional preferences
5. Dashboard loads with personalized recommendations

**Flow 2: Exploring Recommendations (15-30 minutes)**
1. View top-10 ranked schools with overall fit scores (0-100)
2. Click school to see detailed breakdown:
   - Scheme Fit: 87/100 (your shooting style matches their offense)
   - Opportunity: 72/100 (projected 22-28 mpg, 70% chance to start)
   - Gap Match: 90/100 (they need a shooting guard)
   - Personal Fit: 65/100 (NIL moderate, academics strong, location far)
3. See predicted outcomes: "Expected +3.5 PER improvement, 24 mpg"
4. Review "Similar Transfers": Players like you who went to similar programs
5. Add to shortlist or dismiss

**Flow 3: Comparing Finalists (10-20 minutes)**
1. Select 2-4 schools from shortlist
2. View side-by-side comparison with radar charts, fit breakdowns, projections
3. Adjust priority weights to see how rankings change ("What if I care more about NIL?")
4. Export comparison report as PDF
5. Make decision (off-platform)

---

## 3. System Architecture

### High-Level Components

```
┌─────────────────────────────────────────────────────────────┐
│                     USER INTERFACE                          │
│              React SPA + Mobile Web                         │
│         (Dashboard, Recommendations, Comparison)            │
└─────────────────────────────────────────────────────────────┘
                            ↓ HTTPS
┌─────────────────────────────────────────────────────────────┐
│                    API GATEWAY (FastAPI)                    │
│   Authentication, Rate Limiting, Request Routing            │
└─────────────────────────────────────────────────────────────┘
                            ↓
        ┌──────────────────┴──────────────────┐
        ↓                                     ↓
┌──────────────────┐              ┌──────────────────────┐
│  RECOMMENDATION  │              │   MODEL SERVING      │
│     SERVICE      │              │     SERVICE          │
│                  │              │                      │
│ • Generate       │←────────────→│ • Fit Score Models   │
│   top-N recs     │              │ • Prediction Models  │
│ • Personalize    │              │ • Clustering Models  │
│ • Rank schools   │              │                      │
└──────────────────┘              └──────────────────────┘
        ↓                                     ↓
┌─────────────────────────────────────────────────────────────┐
│                    DATA LAYER                               │
├──────────────────────┬──────────────────────┬───────────────┤
│  PostgreSQL          │  Redis Cache         │  S3 Storage   │
│  • Player stats      │  • Fit scores        │  • Raw data   │
│  • Team data         │  • Predictions       │  • Models     │
│  • Transfers         │  • Sessions          │  • Backups    │
│  • User prefs        │                      │               │
└──────────────────────┴──────────────────────┴───────────────┘
        ↑                                     ↑
┌──────────────────┐              ┌──────────────────────┐
│   ETL PIPELINE   │              │   ML TRAINING        │
│   (Airflow)      │              │   PIPELINE           │
│                  │              │                      │
│ • Scrape APIs    │              │ • Train models       │
│ • Clean data     │              │ • Evaluate           │
│ • Feature eng    │              │ • Deploy             │
└──────────────────┘              └──────────────────────┘
```

### Key Design Decisions

**Decision 1: Monolithic API vs. Microservices**
- **Choice:** Start monolithic (FastAPI single service), evolve to microservices post-MVP
- **Rationale:** Faster development, simpler deployment, adequate for MVP scale. Split when we hit performance bottlenecks or need independent scaling.

**Decision 2: Real-time vs. Batch Fit Scoring**
- **Choice:** Hybrid approach—pre-compute and cache popular pairings, compute on-demand for long-tail
- **Rationale:** 875K possible pairings × 4 fit components = 3.5M calculations. Pre-compute top 50 schools per player (125K pairings), cache in Redis (30-min TTL). On-demand for edge cases. Balances latency (<200ms) with infrastructure cost.

**Decision 3: Database - SQL vs. NoSQL**
- **Choice:** PostgreSQL (relational)
- **Rationale:** Structured data with clear relationships (players → stats → transfers → schools). Need ACID transactions for user data. JSONB columns handle flexible schema (user preferences) while maintaining relational integrity.

**Decision 4: Client-Side vs. Server-Side Rendering**
- **Choice:** Client-side React SPA
- **Rationale:** Rich interactivity (sliders, filtering, comparisons) works better with client-side state management. Users spend 5-15 minutes exploring—SPA UX superior to page reloads. SEO not critical for logged-in app.

---

## 4. Data Architecture

### Core Entities

**Players**
- Identity: name, position, height, class year, hometown
- External IDs: CBBpy, VerbalCommits (for linking)
- Social: Twitter handle, Instagram, follower count

**Player Season Stats** (partitioned by season)
- Traditional: PPG, RPG, APG, minutes
- Advanced: PER, TS%, usage rate, assist rate
- Style: shot distribution (rim%, 3PT%, mid-range%), assisted FG%
- Quality flags: minutes threshold, data completeness

**Schools**
- Profile: name, conference, location (lat/long), enrollment, setting
- Academic: majors offered, graduation rates, rankings
- NIL: estimated budget/resources, historical deals

**Team Season Stats**
- Performance: offensive/defensive rating, pace, efficiency
- Style: shot distribution, ball movement (assist rate), tempo
- System: offensive/defensive classifications (from clustering)

**Transfers**
- History: player, from_school, to_school, dates (portal entry, commitment)
- Context: pre/post transfer stats, role change
- Outcomes: minutes change, PER change, success score

**Player-Team Fit Scores** (cached, expires hourly)
- Components: gap_match (0-100), scheme_fit (0-100), opportunity (0-100), personal_fit (0-100)
- Composite: overall_fit (weighted average)
- Metadata: computed_at, model_version, user_weights

**Team Rating Projections** (cached, expires hourly)
- Inputs: player_id, team_id, expected_minutes (from Playing Time model)
- Outputs: current_adjEM, projected_adjEM, delta_adjEM, confidence_interval, national_percentile
- Metadata: computed_at, model_version

**User Preferences**
- Priorities: importance weights (playing_time, NIL, academics, location)
- Filters: desired major, regional preferences, min/max school size
- Customization: preferred pace, role (high-usage vs. spot-up)

### Data Flow

```
External APIs → Raw Storage (S3) → Validation → PostgreSQL → Feature Engineering
                                                      ↓
                                            Player/Team Features
                                                      ↓
                                            ML Models (Training)
                                                      ↓
                                            Fit Scores (Cache)
                                                      ↓
                                            Recommendations → User
```

### Partitioning & Indexing Strategy

**Time-Series Tables:** Partition by season (2020, 2021, 2022...) for `player_season_stats`, `team_season_stats`
- Hot data: Current + last season (SSD)
- Cold data: >2 seasons archived to S3

**Critical Indexes:**
- `player_season_stats(player_id, season)` - composite B-tree
- `player_team_fit_scores(overall_fit DESC)` - for ranking
- `players(full_name)` - trigram GIN index for fuzzy search
- `user_preferences(user_id)` - unique, frequently accessed

---

## 5. Machine Learning Pipeline

### Model Architecture

**Model 1: Player Clustering (K-Means)**
- Purpose: Categorize players into 10 archetypes ("3&D Wing", "Stretch 4", etc.)
- Input: 30 statistical features (shooting, creation, defense, physical)
- Process: PCA (30→10 dims) → K-Means (k=10) → Manual labeling
- Output: Archetype assignment per player-season
- Training: Weekly on Sundays, full dataset
- Serving: Pre-computed, stored in database

**Model 2: Team System Clustering (K-Means)**
- Purpose: Classify team offensive/defensive systems
- Input: Pace, shot distribution, ball movement, defensive scheme
- Process: Clustering + rule-based refinement
- Output: System labels ("Fast 3PT Heavy", "Slow Post-Dominant")
- Training: Weekly
- Serving: Pre-computed

**Model 3: Scheme Fit Scorer (Cosine Similarity)**
- Purpose: Match player style to team system
- Input: Player style vector [3PT%, rim%, usage, assisted%, pace] vs. Team system vector
- Process: Cosine similarity → scale to 0-100
- Output: Scheme fit score + component breakdown
- Serving: Computed on-demand, cached (30-min TTL)

**Model 4: Playing Time Predictor (Bayesian Regression)**
- Purpose: Estimate expected minutes with uncertainty
- Input: Player skill, team depth chart, coaching tendencies
- Process: Bayesian hierarchical model (PyMC3) with MCMC sampling
- Output: Expected minutes (mean), confidence interval (10th-90th percentile), starter probability
- Serving: Computed on-demand (100-200ms), cached
- Alternative: Monte Carlo simulation if Bayesian too slow

**Model 5: Transfer Success Predictor (XGBoost)**
- Purpose: Predict post-transfer performance change
- Input: 50+ features (player stats, team characteristics, differentials)
- Targets: Change in PER, change in minutes, starter/bench classification
- Process: Ensemble XGBoost + LightGBM, temporal cross-validation
- Output: Predicted outcomes + SHAP explanations
- Training: Weekly retraining
- Serving: Pre-computed for top prospects, on-demand otherwise

**Model 6: Team Rating Projection (XGBoost)**
- Purpose: Estimate how a player's addition changes a team's season-level adjusted efficiency margin (AdjEM)
- Input: Player archetype, player PER, player expected minutes (from Model 4), team current AdjEM, offensive/defensive rating split, conference strength, role change magnitude
- Process: XGBoost regression trained on historical transfers; uses per-player on/off split data from play-by-play to isolate marginal contribution; temporal cross-validation (train 2020–2023, validate 2024)
- Output: Current team AdjEM, projected team AdjEM with player, delta AdjEM with 80% confidence interval, national percentile, conference rank
- Training: Weekly retraining alongside Model 5
- Serving: Pre-computed for top-50 school candidates per player, cached (1-hour TTL)
- UI surface: "With you on this roster, this team projects at +5.1 AdjEM (top-40 nationally), up from +2.8"
- Dependency: Requires Model 4 (Playing Time) output as input; pipeline must run after minutes prediction is complete

**Model 7: Recommendation Engine (Hybrid)**
- Purpose: Generate personalized top-N school recommendations
- Components:
  - Collaborative filtering (30%): SVD on historical transfer success matrix
  - Content-based filtering (30%): Feature similarity to user preferences
  - Fit scores (40%): Composite from Models 3-6
- Process: Weighted aggregation → rank → filter by hard constraints → top-10
- Serving: Generated on login + on preference update

### Model Lifecycle

```
Data Collection (Daily) → Feature Engineering (Daily) → Model Training (Weekly)
                                                              ↓
                                                        Evaluation
                                                              ↓
                                                    Improvement >5%?
                                                         ↙        ↘
                                                      YES         NO
                                                       ↓           ↓
                                              Deploy to Prod   Keep Current
                                                       ↓
                                              Monitor Performance
                                                       ↓
                                                  Drift >20%?
                                                       ↓
                                              Trigger Retraining
```

**Model Monitoring:**
- Track prediction RMSE on 30-day rolling window
- Alert if accuracy degrades >20% vs. baseline
- Feature drift detection (Kolmogorov-Smirnov test)
- Monthly performance reports to stakeholders

---

## 6. API Design

### Core Endpoints (REST)

**Authentication**
```
POST /api/auth/signup
POST /api/auth/login
POST /api/auth/logout
```

**Player Profile**
```
GET  /api/players/{player_id}
GET  /api/players/search?name={name}
POST /api/players/{player_id}/claim  # Link user account to player profile
```

**Recommendations**
```
GET  /api/recommendations?user_id={id}
  Response: {
    "recommendations": [
      {
        "rank": 1,
        "school_id": 42,
        "school_name": "Gonzaga",
        "overall_fit": 87.5,
        "components": {
          "gap_match": 90,
          "scheme_fit": 92,
          "opportunity": 78,
          "personal_fit": 65
        },
        "reasoning": "Strong scheme fit - your shooting matches their offense..."
      },
      ...
    ],
    "generated_at": "2026-05-22T10:30:00Z"
  }
```

**Fit Scores**
```
GET  /api/fit-scores?player_id={pid}&school_id={sid}
  Response: {
    "overall_fit": 87.5,
    "gap_match": 90,
    "scheme_fit": 92,
    "opportunity": 78,
    "personal_fit": 65,
    "breakdown": {
      "scheme": {
        "three_point_match": 95,
        "pace_match": 88,
        "usage_match": 92
      },
      "opportunity": {
        "projected_minutes": 24.3,
        "confidence_interval": [18.5, 29.1],
        "starter_probability": 0.72
      }
    }
  }
```

**Predictions**
```
GET  /api/predictions?player_id={pid}&school_id={sid}
  Response: {
    "predicted_per_change": 3.5,
    "predicted_minutes": 24.3,
    "predicted_role": "starter",
    "confidence": 0.68,
    "similar_transfers": [...]
  }
```

**Team Rating Projection**
```
GET  /api/projections/team-rating?player_id={pid}&school_id={sid}
  Response: {
    "current_adjEM": 2.8,
    "projected_adjEM": 5.1,
    "delta_adjEM": 2.3,
    "confidence_interval": [0.8, 3.7],
    "national_percentile": 68,
    "conference_rank": 4,
    "context": "Top-40 nationally, up from top-80 without you"
  }
```

**User Preferences**
```
GET  /api/users/{user_id}/preferences
PUT  /api/users/{user_id}/preferences
  Body: {
    "importance_weights": {
      "playing_time": 10,
      "nil": 8,
      "academics": 6,
      "location": 7
    },
    "filters": {
      "desired_major": "Business",
      "regions": ["Midwest", "East Coast"]
    }
  }
```

**Shortlist**
```
GET    /api/users/{user_id}/shortlist
POST   /api/users/{user_id}/shortlist/{school_id}
DELETE /api/users/{user_id}/shortlist/{school_id}
```

**Comparison**
```
POST /api/compare
  Body: {
    "player_id": 123,
    "school_ids": [42, 87, 103]
  }
  Response: {
    "schools": [...],
    "comparison_matrix": {...},
    "trade_offs": [...]
  }
```

### Performance Requirements

- **Latency:** p95 < 200ms for fit scores, < 500ms for recommendations
- **Throughput:** 100 requests/second (adequate for 1,000 DAU)
- **Availability:** 99%+ during portal season (April-August)
- **Rate Limiting:** 100 requests/minute per user (token bucket)

---

## 7. Data Science Approach (Technical)

### Feature Engineering Pipeline

**Player Features (30+ dimensions):**
- Shooting: TS%, eFG%, 3PT%, FT%, shot distribution
- Creation: Usage rate, assist rate, assisted FG%, isolation frequency
- Impact: PER, BPM, win shares, on/off splits
- Defense: Steal rate, block rate, defensive rating
- Consistency: Game-to-game variance, trending (improving vs. declining)
- Physical: Height, weight, wingspan (when available)

**Team Features (20+ dimensions):**
- Performance: Offensive/defensive rating, net rating, SOS
- Tempo: Pace, possessions per game
- Style: Shot distribution, ball movement (assist rate), turnover rate
- Four factors: eFG%, TOV%, ORB%, FT rate (offense and defense)
- System classifications: Cluster IDs + labels

**Transfer Features:**
- Context: Conference change, school tier change, role change
- Differentials: Pace change, system mismatch magnitude
- Opportunity: Positional depth, competition level

**Team Rating Projection Features:**
- Team baseline: Current season AdjEM, offensive/defensive rating split (from toRvik)
- Player marginal impact: Per-player on/off differential from play-by-play (net rating on vs. off court)
- Role context: Expected minutes (from Model 4), depth chart position, competition level at position
- Roster context: Sum of minutes vacated by graduating/departing players at that position
- Conference adjustment: SOS-adjusted net rating to normalize cross-conference comparisons

### Fit Scoring Methodology

**Gap Matching (0-100):**
- Does player's archetype match team's identified needs? (from roster analysis)
- Bonus: Fills high-priority gap (+50), unique skill team lacks (+15)
- Penalty: Redundant archetype already on roster (-20)

**Scheme Fit (0-100):**
- Cosine similarity between player style vector and team system vector
- Weighted by user preferences (e.g., if "pace matters most", weight pace dimension 2x)
- Component breakdown for transparency

**Opportunity (0-100):**
- Bayesian prediction: Expected minutes → scale to 0-100 (0 min = 0, 30+ min = 100)
- Adjustments: Starter bonus (+10 if P(starter) > 0.7), uncertainty penalty (-10 if wide CI)
- User input: Self-assessment ("I'm better than their starter") shifts prediction

**Personal Fit (0-100):**
- NIL: Regression model (player stats + school market → predicted value) → scale to 0-100
- Geographic: Distance from hometown → linear scale (0 mi = 100, 1500+ mi = 0)
- Academic: Binary (major offered?) + school ranking → weighted average
- Cultural: Match on school size, setting preferences
- Aggregate via user-weighted average (user specifies importance of each factor)

**Composite Score:**
```
overall_fit = (gap × w_gap) + (scheme × w_scheme) + 
              (opportunity × w_opp) + (personal × w_personal)

Default weights: w_scheme=0.30, w_opp=0.25, w_gap=0.20, w_personal=0.25
User-adjustable via preference sliders
```

### Model Training & Evaluation

**Training Data:**
- Historical transfers: 2020-2024 seasons (~10,000 transfers)
- Temporal split: Train on 2020-2023, validate on 2024
- Cross-validation: 5-fold TimeSeriesSplit

**Evaluation Metrics:**
- Prediction RMSE: Target <3.5 PER points, <7 minutes
- Team rating projection RMSE: Target <2.0 AdjEM points vs. actual season outcomes
- Recommendation hit rate: Target >40% (actual destination in top-5)
- Classification accuracy: Target >70% (starter/bench)
- Improvement vs. baseline: Beat "no change" predictions by 25%+

**Model Versioning:**
- MLflow tracks experiments, parameters, metrics
- Production models tagged with version (e.g., "fit_scorer_v2.1")
- A/B testing framework for comparing model variants
- Promotion criteria: New model must improve accuracy by >5%

---

## 8. Infrastructure & Deployment

### Technology Stack

**Frontend:**
- React 18 (TypeScript)
- Redux Toolkit (state management)
- D3.js + Recharts (visualizations)
- Material-UI (component library)

**Backend:**
- FastAPI (Python 3.11+)
- PostgreSQL 15 (primary database)
- Redis 7.0 (caching, session store)
- Celery + RabbitMQ (async tasks)

**ML/Data:**
- scikit-learn, XGBoost, PyMC3
- pandas, NumPy (data processing)
- MLflow (model management)
- Apache Airflow (orchestration)

**Infrastructure:**
- AWS (compute, storage)
- Docker (containerization)
- GitHub Actions (CI/CD)
- CloudWatch + Prometheus (monitoring)

### Deployment Architecture

**Environments:**
- Development: Local Docker Compose
- Staging: AWS ECS (single instance)
- Production: AWS ECS (auto-scaling 2-5 instances)

**Database:**
- AWS RDS PostgreSQL (db.t3.medium for MVP)
- Automated daily backups (7-day retention)
- Read replica for analytics queries

**Caching:**
- ElastiCache Redis (cache.t3.small)
- TTL: Fit scores (30 min), predictions (1 hour), recommendations (1 hour)

**Storage:**
- S3: Raw data, model artifacts, backups
- CloudFront: Static asset CDN

**Deployment Pipeline:**
```
Push to GitHub → Run Tests → Build Docker Image → Push to ECR →
→ Deploy to Staging → Smoke Tests → Manual Approval → 
→ Deploy to Production (Rolling) → Health Checks → Monitor
```

**Rollback Strategy:**
- Automatic rollback if health checks fail
- Manual rollback via ECS task definition version

### Scalability Considerations

**Current Capacity (MVP):**
- 1,000 daily active users
- 100 requests/second
- 1M fit score calculations/day (mostly cached)

**Scale Triggers:**
- CPU >70% sustained → add ECS tasks
- Cache hit rate <80% → increase Redis memory
- DB connections >80% → add read replicas

**Future Scaling Path:**
- Microservices: Split recommendation service from API service
- Async processing: Move heavy computations to background workers
- Database sharding: Partition by season when >100GB
- CDN: Cache API responses for popular queries

---

## 9. Testing Strategy

### Testing Timeline

**Week 12-14:** Beta testing with 20-30 mock? players

### Validation Methods

**1. Predictive Accuracy (Quantitative)**
- Holdout test: Spring 2026 portal class
- Generate predictions in real-time as players enter portal
- Measure RMSE vs. actual outcomes (post-season)
- Compare to baselines (naive "no change" prediction, rankings)
- **Success:** RMSE <3.5 PER, hit rate >40%, p<0.05 improvement

**2. User Impact (Behavioral + Survey)**
- Track: Decision alignment (final choice in top-3?), engagement (session time), feature usage
- Survey: Pre/post confidence (Likert scale), time savings (hours), NPS
- **Success:** 70%+ report platform influenced decision, NPS >40, confidence improvement p<0.05

**3. Expert Validation (Qualitative)**
- Blind test: 5-7 coaches rate recommendations (1-5 scale)
- Interviews: "What did we get right/wrong? Would you use this?"
- **Success:** >70% expert agreement, avg rating ≥4/5

**4. A/B Testing (Product Optimization)**
- Test variants: Weighting schemes, recommendation algorithms, UI presentations
- Metrics: User satisfaction, hit rate, engagement
- **Success:** Identify statistically significant winner (p<0.05)

### Monitoring & Alerting

**Key Metrics:**
- Prediction RMSE (rolling 30-day window)
- API latency (p50, p95, p99)
- Error rates (4xx, 5xx)
- Cache hit rate
- User engagement (DAU, session duration)

**Alerts:**
- RMSE >baseline × 1.2 → trigger retraining
- API p95 >500ms → investigate performance
- Error rate >1% → page on-call
- DB CPU >80% → scale up

---

## 10. Open Questions & Risks

### Open Questions

**Q1: How do we handle players with limited statistical history?**
- New transfers with <10 games played
- Proposed: Rely more on physical attributes, high school data, lower confidence scores

**Q2: What's the right balance of automation vs. human judgment?**
- Should we allow override fit scores?
- Proposed: Display scores as decision support, not mandates; users maintain agency

**Q3: How do we verify player identity to prevent impersonation?**
- Email verification sufficient or need manual review?
- Proposed: Email verification for MVP, phone verification for premium

**Q4: What if predicted outcomes are wildly inaccurate for specific player?**
- Edge cases where model fails
- Proposed: Confidence intervals + "This prediction is uncertain" warnings when CI is wide

### Risks & Mitigations

**Risk 1: Data Quality Issues**
- **Impact:** High (bad data → bad predictions → user distrust)
- **Mitigation:** Automated validation pipelines, cross-source verification, manual spot checks, user reporting of errors

**Risk 2: Model Accuracy Below Expectations**
- **Impact:** High (inaccurate predictions reduce value proposition)
- **Mitigation:** Extensive backtesting, conservative confidence intervals, transparent accuracy reporting, continuous improvement

**Risk 3: Low User Adoption**
- **Impact:** High (no users = failed MVP)
- **Mitigation:** User research validates pain points, early beta with influencers (analysts, media), freemium model lowers barrier, compelling demos

**Risk 4: Legal/Compliance Issues (NCAA, FERPA)**
- **Impact:** Medium (could block launch)
- **Mitigation:** Consult legal counsel, use only public data, transparent ToS, player data control (deletion rights)

**Risk 5: Competition from Established Platforms**
- **Impact:** Medium (EvanMiya, 247Sports could add similar features)
- **Mitigation:** First-mover advantage, superior UX, rapid iteration, network effects (more users → more data → better models)

**Risk 6: Infrastructure Costs Exceed Budget**
- **Impact:** Low (can scale down if needed)
- **Mitigation:** Start small (t3.medium instances), auto-scaling with caps, monitor spend weekly, optimize before scaling

---

## 11. Success Criteria

### MVP Launch Criteria (Go/No-Go)

**Functional:**
- All core endpoints operational (<500ms latency)
- Fit scores compute accurately (spot-checked by domain expert)
- Recommendations generate for 100% of test users
- User authentication and preferences work end-to-end

**Data Quality:**
- Database contains 2,500+ current portal players
- 95%+ of players have complete statistical profiles
- Historical transfer data covers 2020-2024 (validation set)

**Performance:**
- API handles 100 requests/second without errors
- 99% uptime during beta testing period
- Cache hit rate >70%

**User Validation:**
- 10+ beta users complete full workflow (signup → recommendations → comparison → decision)
- Positive feedback from majority (>60% would recommend to others)
- No critical bugs blocking core functionality

### 6-Month Success Metrics

**User Adoption:**
- 50+ active users (players in portal season)
- 40% monthly retention
- 70%+ of users access recommendation feature

**Predictive Accuracy:**
- Recommendation hit rate >40%
- Transfer success prediction RMSE <3.5 PER
- Beat baseline approaches by 25%+

**User Satisfaction:**
- NPS >40
- 70%+ report platform influenced decision
- Average session duration >5 minutes

**Business:**
- Validated value proposition (users willing to pay in future)
- Foundation for coach-facing tier established
- Path to revenue identified ($5K-15K annual subscriptions)

---

## 12. Timeline & Milestones

> **Starting point:** AWS provisioning, repo/CI scaffolding, data sources confirmed, basic schema in place, and EDA underway.

### Team Structure

| Member | Role | Focus Areas |
|--------|------|-------------|
| **Engineer A** | Data Engineering | ETL pipelines, Airflow DAGs, feature store, AWS infrastructure, monitoring |
| **Engineer B** | ML — Player-Side | Player clustering, scheme fit scorer, transfer success predictor, NIL valuation, recommendation engine |
| **Engineer C** | ML + Backend | Team system clustering, playing time predictor, team rating projection, FastAPI serving layer |
| **Engineer D** | Frontend & Product | React SPA, UX design, API contract, beta coordination, end-to-end testing |

---

### Phase 1: EDA & Feature Engineering (Weeks 1–3)

| Week | A — Data Engineering | B — ML (Player) | C — ML + Backend | D — Frontend + Product |
|------|---------------------|-----------------|-----------------|------------------------|
| **1** | ETL finalization; automated nightly ingestion live; on/off split extraction pipeline | Player feature engineering: TS%, usage rate, shot distribution from PBP | Team feature engineering: pace, ratings, four factors, on/off differential joins | API contract (OpenAPI spec); React project scaffold; UX wireframes for core flows |
| **2** | Transfer outcome features; Airflow DAG skeleton; data quality monitoring | Player feature validation; PCA exploration (pre-clustering) | Team system feature validation; team rating feature pipeline | Backend API skeleton (FastAPI): auth, player search, user preferences |
| **3** | Airflow daily ingestion DAG live; MLflow server setup | Player clustering (K-Means + PCA) → archetype labels finalized | Team system clustering → system labels; scheme fit scorer (cosine similarity) live | Auth endpoints live; player profile claim; OpenAPI docs published |

**Milestone (end of Week 3):** Feature pipelines automated; player archetypes + team system labels deployed; API authentication live; frontend and backend running in parallel from here.

---

### Phase 2: Model Training (Weeks 4–7)

| Week | A — Data Engineering | B — ML (Player) | C — ML + Backend | D — Frontend + Product |
|------|---------------------|-----------------|-----------------|------------------------|
| **4** | Model artifact versioning (S3 + MLflow); CI/CD model promotion pipeline | Transfer success predictor: feature selection, temporal CV setup | Playing time predictor (PyMC3 Bayesian) training and tuning | Frontend: recommendations view scaffold; fit score display component |
| **5** | Weekly training DAG live (Sundays); data drift monitoring | Transfer success: XGBoost + LightGBM ensemble, SHAP integration | Team rating projection (XGBoost) — runs after playing time output available in same sprint | School detail page; fit score breakdown panel; team rating projection display |
| **6** | Redis caching layer; cache invalidation pub/sub | NIL valuation model (GBM); collaborative filter (SVD) training | Recommendation engine: hybrid SVD + content-based + fit score ensemble | User preference sliders; priority weight adjustment; live re-ranking on weight change |
| **7** | AWS ECS containers live; Airflow hourly portal monitoring DAG | Model evaluation vs. baselines; hit rate validation on 2024 holdout | Model serving service (FastAPI); all models loaded; fit score + team rating endpoints live | Full dashboard: top-10 recommendations; end-to-end user flow complete |

**Milestone (end of Week 7):** All models trained and registered in MLflow; model serving live; full recommendation flow functional from login to school detail.

---

### Phase 3: Integration & Polish (Weeks 8–10)

| Week | A — Data Engineering | B — ML (Player) | C — ML + Backend | D — Frontend + Product |
|------|---------------------|-----------------|-----------------|------------------------|
| **8** | Load testing; cache hit rate tuning; pre-compute top-50 schools per player | Confidence interval display; SHAP explanation text generation | Comparison tool API (`POST /api/compare`); shortlist endpoints | Comparison UI (side-by-side); radar charts (D3.js); trade-off summary |
| **9** | Grafana/Prometheus dashboards; CloudWatch alarms configured | RMSE validation on holdout; accuracy reports for stakeholders | Frontend–backend integration hardening; edge case handling | PDF export; mobile-responsive polish; similar transfers display |
| **10** | Performance optimization; SLA validation (p95 < 200ms for fit scores) | Expert validation prep: sample recommendations for coach review | End-to-end API test suite (pytest + integration tests) | Beta onboarding flow; account creation; player profile claim UX |

**Milestone (end of Week 10):** All user flows complete; p95 latency targets met; ready for beta.

---

### Phase 4: Beta Testing & Launch (Weeks 11–12)

| Week | A — Data Engineering | B — ML (Player) | C — ML + Backend | D — Frontend + Product |
|------|---------------------|-----------------|-----------------|------------------------|
| **11** | Production monitoring; on-call rotation; backup verification | Beta accuracy spot-checks; model retraining if drift detected | Bug fixes from beta; API hardening; rate limiting validation | Beta: 20–30 players onboarded; expert validation (5–7 coaches); UX iteration |
| **12** | Post-launch pipeline monitoring; alert tuning | Model drift detection live; post-season accuracy eval scheduled | Rolling production deployment; health checks; rollback procedure verified | **Public launch** (portal season); user comms; feedback collection |

**Milestone (end of Week 12):** Public launch with 99%+ uptime target; monitoring live; post-season accuracy evaluation scheduled for September.

---

### Phase 5: Iteration (Week 13+)

- Ongoing: User feedback loops, weekly model retraining, feature improvements based on beta learnings
- September 2026: Formal accuracy evaluation — hit rate, RMSE vs. targets, NPS survey

---

### Critical Path

```
[Weeks 1–3]  EDA & Feature Engineering — all 4 tracks run simultaneously
                        ↓
[Weeks 4–5]  Core Model Training — B: player models || C: team models (parallel)
                        ↓
[Week 5]     Team Rating Projection (C) — depends on Playing Time output (same sprint)
                        ↓
[Week 6]     NIL + Collaborative Filter (B) || Recommendation Engine (C) — parallel
                        ↓
[Week 7]     Model Evaluation (B) + Model Serving Service (C) — parallel
                        ↓
[Weeks 8–10] Integration + Polish — all tracks converge
                        ↓
[Week 11]    Beta Testing + Expert Validation
                        ↓
[Week 12]    Public Launch
```

### Key Dependencies (Cannot Parallelize)

- Playing Time model (C, Week 4–5) must complete before Team Rating Projection trains (C, Week 5) — within same sprint, sequential within C's track
- All models must be registered in MLflow (end of Week 7) before production deployment (Week 12)
- API endpoints must stabilize (end of Week 8) before full frontend integration hardening (Week 9–10)
- Beta feedback (Week 11) must be resolved before public launch (Week 12)

---

## 13. Future Roadmap (Post-MVP)

### Phase 2: Coach-Facing Tools (Months 4-6)
- Roster gap analysis dashboard
- Bulk player recommendations (top 50 fits for your program)
- Recruitment pipeline tracking
- Competitive intelligence (what are rivals doing?)
- Pricing: $5K-15K annual subscriptions

### Phase 3: Advanced Features (Months 7-12)
- NIL valuation estimation (regression model + market data)
- Agent/advisor tools (manage multiple player clients)
- Video integration (link to Synergy, YouTube highlights)
- Historical transfer database API (for media, researchers)
- Mobile native apps (iOS, Android)

---

## Appendix A: Glossary

**PER (Player Efficiency Rating):** Comprehensive efficiency metric summarizing per-minute statistical production

**TS% (True Shooting %):** Shooting efficiency accounting for 2PT, 3PT, and free throws

**Usage Rate:** Percentage of team possessions used by a player while on court

**Archetype:** Player category based on statistical profile (e.g., "3&D Wing", "Stretch 4")

**Fit Score:** 0-100 metric quantifying player-team compatibility across multiple dimensions

**Hit Rate:** Percentage of recommendations where actual outcome appears in top-N predictions

**RMSE (Root Mean Squared Error):** Standard metric for prediction accuracy (lower = better)

**NPS (Net Promoter Score):** User satisfaction metric (% promoters - % detractors)

**MVP (Minimum Viable Product):** Simplest version validating core value proposition

**Portal Season:** Peak transfer activity period (April-August for spring portal, October-November for fall)

---

## Appendix B: Key Assumptions

**Data Assumptions:**
1. Public data (CBBpy, hoopR, toRvik) is sufficient for MVP—no proprietary data needed
2. Historical transfer outcomes contain signal for predicting future outcomes
3. Play-by-play data accurately represents player style and system fit
4. Missing data <20% for players with >10 games played

**Model Assumptions:**
1. Transfer success is modelable from quantitative factors (not purely random or based on unmeasurable intangibles)
2. Four fit components (gap, scheme, opportunity, personal) capture majority of decision-relevant factors
3. Historical patterns generalize to future transfers (portal dynamics haven't fundamentally changed)
4. Users trust algorithmic recommendations if explainability is high

**User Assumptions:**
1. Players value data-driven insights and will change behavior based on recommendations
2. Users understand 0-100 scoring and can interpret fit breakdowns
3. Players have 2-3 weeks to research, can dedicate 30-60 minutes to platform
4. Decision-makers (players, families) have smartphone/computer access

**Market Assumptions:**
1. Players will use free tools even if coaches don't (initially)
2. Demonstrated player-side value creates bottom-up demand for coach-facing tools
3. Mid-major programs willing to pay $5K-15K annually for analytics tools
4. Transfer portal continues growing (not regulatory rollback)

---

*Document Version: 1.0*  
*Last Updated: May 22, 2026* 
