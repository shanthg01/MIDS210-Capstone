# DIAGRAM 1: Three-Layer Architecture
## User Interactions → Fit Components → Data Science Layer

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           LAYER 1: USER INTERACTIONS                         │
│                     (What Programs/Coaches Want to Do)                       │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│ "Show me portal      │  │ "Which players fit   │  │ "How does player X   │
│  players that fit    │  │  our system?"        │  │  affect our team     │
│  our system"         │  │                      │  │  efficiency?"        │
└──────────────────────┘  └──────────────────────┘  └──────────────────────┘

┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│ "Compare our top     │  │ "What's this player  │  │ "Rate our current    │
│  portal targets      │  │  worth to our NIL    │  │  recruiting          │
│  side-by-side"       │  │  budget?"            │  │  pipeline"           │
└──────────────────────┘  └──────────────────────┘  └──────────────────────┘

┌──────────────────────┐  ┌──────────────────────┐
│ "How much will       │  │ "How good will our   │
│  player X improve    │  │  team be next        │
│  our team?"          │  │  season?"            │
└──────────────────────┘  └──────────────────────┘

                                    ↓ ↓ ↓
                     Fit Components + Team Impact Projection

┌─────────────────────────────────────────────────────────────────────────────┐
│                      LAYER 2: FOUR FIT COMPONENTS                            │
│                  (How We Evaluate Player-Team Match)                         │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   COMPONENT 1   │  │   COMPONENT 2   │  │   COMPONENT 3   │  │   COMPONENT 4   │
│                 │  │                 │  │                 │  │                 │
│  Player/Team    │  │   Scheme Fit    │  │   Role Fit      │  │  Program Fit    │
│  Gap Matching   │  │                 │  │                 │  │                 │
│                 │  │                 │  │                 │  │                 │
│  Does player    │  │  Does player's  │  │  Will player    │  │  NIL budget,    │
│  fill a team    │  │  style match    │  │  thrive in role │  │  geographic     │
│  need?          │  │  team system?   │  │  we need them?  │  │  reach, eligib. │
│                 │  │                 │  │                 │  │                 │
│  Score: 0-100   │  │  Score: 0-100   │  │  Score: 0-100   │  │  Score: 0-100   │
└─────────────────┘  └─────────────────┘  └─────────────────┘  └─────────────────┘
        │                     │                     │                     │
        │                     │                     │                     │
        └─────────────────────┴─────────────────────┴─────────────────────┘
                                      │
                                      ↓
                        ┌─────────────────────────────┐
                        │  COMPOSITE FIT SCORE        │
                        │  Weighted Average (0-100)   │
                        │  User-Defined Weights       │
                        └─────────────────────────────┘

                                    ↓ ↓ ↓
                            Each Component Requires

┌─────────────────────────────────────────────────────────────────────────────┐
│              LAYER 3: DATA SCIENCE & ENGINEERING PIPELINE                    │
│                 (How We Build Each Component)                                │
└─────────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║                    FOR COMPONENT 1: GAP MATCHING                          ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────┐
│ DATA SOURCES                                                            │
├─────────────────────────────────────────────────────────────────────────┤
│ • barttorvik: AdjEM, Four Factors, player ratings, tempo (primary)     │
│ • hoopR: Event-level PBP (2.9M rows/season), 5 spatial shot zones                    │
│ • Hoop-Explorer: Supplemental player/team data (secondary)             │
│ • Roster data: Team composition                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ FEATURE ENGINEERING (Classical Statistics)                              │
├─────────────────────────────────────────────────────────────────────────┤
│ • Aggregate play-by-play → per-100 possession stats                    │
│ • Calculate: TS%, Usage Rate, Shot Distribution, Creation Rate          │
│ • Tempo adjustments, positional adjustments                             │
│ • Output: player_season_stats table (30+ features)                      │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ UNSUPERVISED ML: Player Clustering (K-Means)                           │
├─────────────────────────────────────────────────────────────────────────┤
│ 1. PCA: 30 features → 10 principal components                          │
│ 2. K-Means: Cluster players (k=8)                                      │
│ 3. Manual labeling: offensive role archetypes                           │
│ 4. Output: player_archetypes table                                      │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ RULE-BASED: Roster Gap Analysis                                         │
├─────────────────────────────────────────────────────────────────────────┤
│ • Compare team roster composition vs. ideal distribution                │
│ • Identify positional gaps (need 2 guards, have 1)                      │
│ • Identify skill gaps (need shooter, have none)                         │
│ • USER INPUT: Coach strategy preference (small ball vs. traditional)    │
│ • Output: roster_gap_analysis table                                     │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ SIMILARITY SCORING: Cosine Similarity                                   │
├─────────────────────────────────────────────────────────────────────────┤
│ • Player skill vector vs. Team need vector                              │
│ • Redundancy penalty (already have this archetype)                      │
│ • Gap filling bonus (fills critical need)                               │
│ • OUTPUT: Gap Match Score (0-100)                                       │
└─────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║                    FOR COMPONENT 2: SCHEME FIT                            ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────┐
│ DATA SOURCES (barttorvik + hoopR + Hoop-Explorer + Team-Level Data)    │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ FEATURE ENGINEERING: Style Profiles                                     │
├─────────────────────────────────────────────────────────────────────────┤
│ PLAYER:                                                                  │
│ • Shot distribution (3PT rate, rim rate, mid-range)                     │
│ • Creation vs. spot-up (assisted FG%)                                   │
│ • Ball dominance (usage rate)                                           │
│ • Pace preference (transition frequency)                                │
│                                                                          │
│ TEAM:                                                                    │
│ • Offensive system (pace, 3PT rate, ball movement)                      │
│ • Defensive system (man/zone, switching)                                │
│ • Historical consistency (does coach change system?)                    │
│ • USER INPUT: Coach system override ("We run motion offense")           │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ UNSUPERVISED ML: System Classification (K-Means)                       │
├─────────────────────────────────────────────────────────────────────────┤
│ • Cluster teams by offensive/defensive style                            │
│ • Label: "Fast-Paced 3PT Heavy", "Slow Post-Dominant", etc.            │
│ • Rule-based refinement for edge cases                                  │
│ • Output: team_system_profiles table                                    │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ SIMILARITY SCORING: Cosine Similarity                                   │
├─────────────────────────────────────────────────────────────────────────┤
│ • Player style vector [3PT rate, rim rate, mid rate] (3-dim base; scheme-cos-v3, all-pairs) │
│ • Team system vector [team_three_rate, team_rim_rate, team_mid_rate] (same 3-dim)    │
│ • Cosine similarity → convert to 0-100 scale                            │
│ • USER INPUT: Player preferences adjust weighting                       │
│   ("I prefer fast pace" → weight pace dimension 2x)                     │
│ • Component breakdown for explainability                                │
│ • OUTPUT: Scheme Fit Score (0-100)                                      │
└─────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║                  FOR COMPONENT 3: ROLE FIT                     ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────┐
│ DATA SOURCES                                                            │
├─────────────────────────────────────────────────────────────────────────┤
│ • Current roster data (all players, their stats, class year)           │
│ • Historical minutes distribution by coach                              │
│ • Coaching tendencies (rotation size, transfer integration speed)      │
│ • USER INPUT: Coach depth chart override (drag-and-drop UI)            │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ HEURISTIC ALGORITHM: Depth Chart Generation                            │
├─────────────────────────────────────────────────────────────────────────┤
│ • Sort players by position and minutes played                           │
│ • Highest MPG = starter, descending = backups                           │
│ • Coach manual override takes precedence                                │
│ • Validation against actual playing time                                │
│ • Output: team_depth_charts table                                       │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ BAYESIAN HIERARCHICAL MODEL: Minutes Prediction                        │
├─────────────────────────────────────────────────────────────────────────┤
│ TECHNIQUE: PyMC3 Bayesian Regression                                    │
│                                                                          │
│ Prior: Team's typical minutes distribution                              │
│   • Starters: μ=28 mpg, σ=5                                            │
│   • Backups: μ=12 mpg, σ=4                                             │
│                                                                          │
│ Likelihood: Minutes adjustment based on skill differential              │
│   • skill_diff = transfer_player_PER - current_player_PER              │
│   • minutes_adjustment ~ Normal(skill_diff * 2, σ=3)                    │
│                                                                          │
│ Posterior: MCMC sampling (2000 iterations)                              │
│   • Expected minutes (mean of posterior)                                │
│   • Confidence interval (10th-90th percentile)                          │
│   • P(starter) = P(minutes > 20)                                        │
│                                                                          │
│ USER INPUT: Player self-assessment ("I'm better than current starter") │
│   • Adjusts prediction ±5 mpg based on insider knowledge                │
│                                                                          │
│ Alternative: Monte Carlo Simulation (10,000 runs)                       │
│   • Simulates injuries, development surprises, foul trouble             │
│   • Aggregates into expected value + confidence bounds                  │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ SCORING: Minutes → Opportunity Score                                    │
├─────────────────────────────────────────────────────────────────────────┤
│ • Linear scaling: 0 minutes = 0, 30+ minutes = 100                     │
│ • Starter bonus: +10 points if P(starter) > 0.7                        │
│ • Uncertainty penalty: -10 if confidence interval > 15 mpg              │
│ • OUTPUT: Opportunity Score (0-100)                                     │
└─────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║              FOR COMPONENT 4: PROGRAM FIT                    ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────┐
│ DATA SOURCES                                                            │
├─────────────────────────────────────────────────────────────────────────┤
│ • Program NIL budget and historical deal data                           │
│ • Player social media metrics (followers, market profile)               │
│ • Player academic eligibility and major/credit requirements             │
│ • Geographic data (player hometown, program recruiting range)           │
│ • Program profile (conference, setting, academic reputation)            │
│ • Program NBA draft pipeline and player development track record        │
│ • PROGRAM INPUT: All program preferences (CRITICAL)                     │
│   - Importance sliders (NIL budget, geography, academics: 1-10)        │
│   - Target archetypes, position needs, system constraints               │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ SUPERVISED ML: NIL Budget Fit Model (Gradient Boosting)                │
├─────────────────────────────────────────────────────────────────────────┤
│ MODEL: XGBoost Regressor                                                │
│ Features: [player_PER, position, social_followers, program_nil_pool,   │
│           conference_tv_deal_value, program_market_size]               │
│ Target: NIL ask vs. program budget alignment score                      │
│ Training: Historical deal data + program NIL budget estimates           │
│ Output: NIL fit score → 0-100 (100 = program can fully meet ask)       │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ CLASSICAL CALCULATIONS: Other Personal Factors                         │
├─────────────────────────────────────────────────────────────────────────┤
│ GEOGRAPHIC:                                                              │
│ • Haversine distance: player hometown → program location               │
│ • Score: 100 if in program's recruiting range, 0 if outside region     │
│                                                                          │
│ ACADEMIC:                                                                │
│ • Major availability check (binary: 100 or 0)                          │
│ • Reputation score (based on rankings)                                  │
│ • Weighted: 70% major match, 30% reputation                            │
│                                                                          │
│ CULTURAL:                                                                │
│ • School size match (small/medium/large preference)                     │
│ • Setting match (urban/suburban/rural preference)                       │
│ • Average of matches                                                    │
│                                                                          │
│ PROFESSIONAL:                                                            │
│ • NBA draft pipeline strength (% drafted)                              │
│ • Conference exposure (TV games, national visibility)                   │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ MULTI-ATTRIBUTE UTILITY THEORY: Aggregate Personal Fit                 │
├─────────────────────────────────────────────────────────────────────────┤
│ PROGRAM WEIGHTS (from preference sliders):                              │
│ • w_nil, w_geo, w_academic, w_cultural (sum to 1.0)                   │
│                                                                          │
│ WEIGHTED AVERAGE:                                                        │
│ personal_fit = (nil_score × w_nil) + (geo_score × w_geo) +            │
│                (academic_score × w_academic) +                          │
│                (cultural_score × w_cultural)                            │
│                                                                          │
│ OUTPUT: Personal Fit Score (0-100)                                      │
└─────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║           COMPOSITE FIT SCORE (Combines All Four Components)              ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────┐
│ INPUTS:                                                                 │
│ • Component 1: Gap Match Score (0-100)                                 │
│ • Component 2: Scheme Fit Score (0-100)                                │
│ • Component 3: Role Fit Score (0-100)                                │
│ • Component 4: Program Fit Score (0-100)                               │
│                                                                          │
│ PROGRAM INPUT: Priority Weights (CRITICAL)                             │
│ Default: gap=0.20, scheme=0.30, role_fit=0.25, program_fit=0.25         │
│ Customizable: "System fit is everything" → 60% Scheme                  │
│                                                                          │
│ CALCULATION:                                                             │
│ overall_fit = (scheme × w_scheme) + (role_fit × w_role) +           │
│               (gap × w_gap) + (program_fit × w_program)                   │
│                                                                          │
│ OUTPUT: Overall Fit Score (0-100)                                       │
│         Stored in: player_team_fit_scores table                         │
└─────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║              TEAM IMPACT PROJECTION (Separate from Fit Score)             ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────┐
│ DATA SOURCES                                                            │
├─────────────────────────────────────────────────────────────────────────┤
│ • On/off split data: player's net rating differential on vs. off court │
│   (extracted from hoopR play-by-play + barttorvik — already scraped)  │
│ • Team season AdjEM: from barttorvik — already scraped                 │
│ • Player archetype: output of Player Clustering model                  │
│ • Expected minutes: output of Playing Time Predictor (Component 3)    │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ FEATURE ENGINEERING: Marginal Impact Features                          │
├─────────────────────────────────────────────────────────────────────────┤
│ • Player on/off differential (net rating on court minus off court)     │
│ • Team baseline: current AdjEM split by offensive/defensive rating     │
│ • Role-adjusted contribution: expected_minutes × per-possession impact │
│ • Conference SOS adjustment (normalize across conference levels)       │
│ • Minutes vacated: sum of departing players' minutes (grad + portal)  │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ SUPERVISED ML: Marginal Contribution Regression (XGBoost)              │
├─────────────────────────────────────────────────────────────────────────┤
│ MODEL: XGBoost Regressor                                                │
│ Target: ΔAdjEM (season-level change in team adjusted efficiency)       │
│ Features: [player_archetype, player_per, team_current_adjEM,           │
│            expected_minutes, role_change_magnitude,                     │
│            conference_strength, off_def_rating_split]                  │
│ Training: Historical transfers 2020–2024; temporal CV                  │
│                                                                          │
│ Key insight: Same player contributes more to a weak team than to an   │
│              already-elite program — model captures this diminishing   │
│              marginal return directly from the training data            │
│                                                                          │
│ Output: point estimate + 80% CI for projected ΔAdjEM                  │
│ Example: current_adjEM = +2.8 → projected_adjEM = +5.1                │
│          delta = +2.3 (CI: [+0.8, +3.7]) · 68th national percentile  │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ DISPLAY (NOT part of composite fit score — shown alongside it)         │
├─────────────────────────────────────────────────────────────────────────┤
│ • Shown on school detail page as a separate panel from fit score       │
│ • "With you, this team projects top-40 nationally (up from top-80)"   │
│ • Wide confidence interval → flag as "projection uncertain"            │
│ • Shows both current rank and projected rank for interpretability      │
│ • Cached in team_rating_projections table (1-hour TTL)                 │
└─────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║            RECOMMENDATION ENGINE (Uses Fit Scores + ML)                   ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────┐
│ COLLABORATIVE FILTERING: Matrix Factorization (SVD)                    │
├─────────────────────────────────────────────────────────────────────────┤
│ • Build interaction matrix: Program × Player (historical recruitments) │
│ • SVD decomposition: 50 latent factors                                  │
│ • For program: find similar historical programs                         │
│ • Recommend players similar to those programs recruited successfully    │
│ • Output: Collaborative recommendations with confidence                 │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ CONTENT-BASED FILTERING: Feature Similarity                            │
├─────────────────────────────────────────────────────────────────────────┤
│ • Player-school feature vectors (combine player stats + school stats)  │
│ • Cosine similarity to program's "target player profile"               │
│ • Apply hard filters (position, archetype, min stats threshold)        │
│ • PROGRAM INPUT: Filters and target player profile preferences          │
│ • Output: Content-based recommendations                                 │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ HYBRID ENSEMBLE: Weighted Combination                                   │
├─────────────────────────────────────────────────────────────────────────┤
│ • Combine: 30% Collaborative + 30% Content + 40% Fit Scores            │
│ • Normalize scores to 0-1 range                                         │
│ • Aggregate by school, rank by combined score                           │
│ • PROGRAM FEEDBACK: Implicit learning from clicks/dismissals           │
│ • Output: Top-N recommendations (typically N=10)                        │
└─────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║          AGENTIC WORKFLOW: Intelligent Scouting Agent                     ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────┐
│ EVENT: New Player Enters Portal                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ LLM-POWERED DECISION (Claude 4 Sonnet)                                 │
├─────────────────────────────────────────────────────────────────────────┤
│ PROMPT: "Should I alert coach about this player?"                       │
│ Context:                                                                 │
│ • New player stats and profile                                         │
│ • Team gaps and needs (from Component 1)                               │
│ • Coach saved filters and priorities (USER INPUT)                       │
│                                                                          │
│ LLM Analyzes:                                                           │
│ 1. Does player fill critical need?                                     │
│ 2. Is caliber suitable for program?                                    │
│ 3. Is there urgency (high-demand player)?                              │
│                                                                          │
│ OUTPUT: ALERT_IMMEDIATE / ALERT_DAILY_DIGEST / IGNORE                  │
│ + Reasoning explanation                                                 │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ ACTION EXECUTION                                                        │
├─────────────────────────────────────────────────────────────────────────┤
│ If ALERT_IMMEDIATE:                                                     │
│ • Send push notification to coach                                       │
│ • Pre-compute full fit analysis (ready when coach clicks)              │
│                                                                          │
│ If ALERT_DAILY_DIGEST:                                                  │
│ • Add to daily summary email                                           │
│                                                                          │
│ If IGNORE:                                                              │
│ • Log but don't alert                                                   │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ LEARNING FROM FEEDBACK                                                  │
├─────────────────────────────────────────────────────────────────────────┤
│ • Track coach response: clicked, dismissed, shortlisted, ignored        │
│ • Store feedback data: {player_profile, agent_reasoning, coach_action} │
│ • Periodically adjust LLM prompt based on patterns                      │
│ • Example: If coach always dismisses low-major alerts, be more         │
│   selective in future                                                   │
└─────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║      AGENTIC WORKFLOW: Recruitment Strategy Agent                         ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────┐
│ USER QUERY: "How should I recruit John Smith?"                         │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ MULTI-STEP REASONING (Agentic Workflow)                                │
├─────────────────────────────────────────────────────────────────────────┤
│ Agent plans information gathering:                                      │
│ 1. Search web for recent news about player                             │
│ 2. Query database for competing schools                                │
│ 3. Analyze historical commitment timelines                             │
│ 4. Check team's recruiting pitch strengths                             │
│ 5. Generate action plan                                                │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ TOOL USE: Web Search + Database Queries                                │
├─────────────────────────────────────────────────────────────────────────┤
│ • web_search("John Smith transfer portal basketball")                  │
│ • db.query("SELECT competing_schools, recruitment_strength...")         │
│ • db.query("SELECT AVG(days_until_commitment) FROM...")                │
│ • Gather: timeline, competition, player priorities, our advantages     │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ LLM SYNTHESIS: Generate Strategy                                       │
├─────────────────────────────────────────────────────────────────────────┤
│ PROMPT: Full context + "Generate week-by-week recruitment strategy"    │
│                                                                          │
│ LLM Output:                                                             │
│ • Week 1: Day-by-day action plan                                       │
│ • Week 2: Follow-up strategy                                           │
│ • Key differentiators to emphasize                                     │
│ • Contingency plans (if Duke offers, if player delays...)             │
│                                                                          │
│ OUTPUT: Structured strategy document with actionable tasks             │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ MONITORING & ADAPTATION                                                 │
├─────────────────────────────────────────────────────────────────────────┤
│ • Daily checks for new developments                                     │
│ • If situation changes → re-run analysis → update strategy             │
│ • Notify coach of updates                                              │
└─────────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════
                        SUMMARY: TECHNIQUES BY LAYER
═══════════════════════════════════════════════════════════════════════════

Layer 3 Techniques Used:
├─ Classical Statistics: Aggregation, feature engineering, basic scoring
├─ Unsupervised ML: K-Means clustering (player archetypes, team systems)
├─ Supervised ML: Gradient Boosting (NIL valuation), Regression (trajectories, team rating projection)
├─ Bayesian Models: Hierarchical regression (playing time with uncertainty)
├─ Similarity Metrics: Cosine similarity (style matching, feature matching)
├─ Recommendation Systems: SVD collaborative filtering, content-based filtering
├─ Multi-Attribute Utility: Weighted aggregation of competing objectives
├─ Heuristic Algorithms: Rule-based depth charts, gap analysis
├─ LLM Agentic Workflows: Multi-step reasoning, tool use, strategic planning
└─ Reinforcement Learning (Implicit): Learning from user feedback over time

Key Program Inputs:
├─ Program Preferences: Target archetypes, position needs, system constraints, NIL budget
├─ Staff Overrides: System description, depth chart, roster strategy
├─ Filters: Position, archetype, geographic reach, academic eligibility
├─ Feedback: Clicks, dismissals, pipeline additions (implicit learning)
└─ Queries: Natural language questions to agentic assistants
```

## Visual Hierarchy Summary

**TOP LAYER (User-Facing):** Natural language interactions, what users want to accomplish

**MIDDLE LAYER (Conceptual):** Four interpretable fit components that users understand

**BOTTOM LAYER (Technical):** Data science pipelines, models, algorithms that power the components

**DATA FLOW:** Bottom → Middle → Top (data flows up, powers components, serves users)

**USER INPUT FLOW:** Top → Middle → Bottom (user inputs flow down, parameterize models, customize results)
