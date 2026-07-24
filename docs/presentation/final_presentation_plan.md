# PortalPoint — Final Presentation Plan (15 minutes)

**Audience:** general data-science literacy, minimal background on this project or the transfer-portal problem space.
**Goal:** demonstrate (1) effective communication, (2) technical depth, (3) strategic/critical thinking — in 15 minutes flat.
**Format:** one top-level section per agenda item, one `###` subsection per slide. Each slide subsection lists: purpose, content bullets, speaker script cues, and time budget. Running time is tracked cumulatively so timing drift is visible at a glance.

**Structural note (v2):** the Demo and Modeling sections are now linked by two running example players carried across the whole talk. The demo shows their live results at the UI level; the modeling section jumps back to slides and explains, for those same two players, *why* the numbers came out the way they did. This lets one concrete pair of examples double as both the product walkthrough and the "works well / edge case" technical discussion, instead of two disconnected sets of examples.

**Structural note (v3):** Sections 4 and 5 are now each a "build" — one full diagram shown once, then repeated across 3 more slides with a different region highlighted (orange) and the rest dimmed, one or two bullets per region. This is why those two sections now run 8 slides between them instead of 2: most of the new slides are quick (~20–30 sec) highlight beats reusing the same diagram, not new content to absorb — the total *information* in Sections 4–5 hasn't grown, just its pacing.

**Structural note (v4):** `docs/dataflow_diagram.mmd`, `docs/model_pipeline.mmd`, and `docs/solution_architecture.mmd` were all brought current (this pass) to match actual project status — all 9 models now shown built (were previously 4 marked "not yet built," and 2 — Player Projection, Destination Projection — missing entirely), the News Monitoring Agent detail refreshed to match its real PR #50 design, and `solution_architecture.mmd` rewritten from the real Production Deployment facts (was describing infrastructure — WebSocket service, XGBoost model-serving, Celery/RabbitMQ, Airflow — that was never built). **`solution_architecture.mmd` is now an accurate, presentable diagram** — Section 5's slides below were already hand-built from the same underlying facts (`CLAUDE.md`'s Production Deployment section) and now match it exactly; the wireframe's node names (`portalpoint-prod` cluster, `portalpoint-backend` service, `/ready` health check) are pulled directly from it. `docs/diagram_2_solution_architecture.md` (an older ASCII-art version) remains stale/aspirational and unfixed — out of scope for this pass, flagged as a follow-up if needed.

Placeholders to fill before the real run: anything in `[BRACKETS]`. Three are open — a domain-expert quote, a user testimonial, and the two real example players — all flagged inline; fill with real content or cut/adjust the beat if unavailable.

---

## Timing Budget (cumulative)

| # | Section | Presenter | Slides | Section time | Cumulative |
|---|---|---|---|---|---|
| 1 | Team Intro | Justin | 1 | 0:25 | 0:25 |
| 2 | Problem Space | Justin | 3 | 1:55 | 2:20 |
| 3 | Demo (scenario + 2 running examples) | Ajay + Shanth | 4 | 3:05 | 5:25 |
| 4 | Data & Pipeline (build: overview + 3 highlights) | Shanth | 4 | 1:35 | 7:00 |
| 5 | System Architecture & Infrastructure (build: overview + 3 highlights) | Shanth | 4 | 1:40 | 8:40 |
| 6 | Modeling Approach (ties back to demo examples) | Ajay + Yoko, **Justin closes with Agent Workflow (6.6)** | 6 | 3:50 | 12:30 |
| 7 | Evaluation | Yoko | 2 | 1:10 | 13:40 |
| 8 | Technical Takeaways | Shanth | 1 | 0:30 | 14:10 |
| 9 | Roadmap & Generalizability | Ajay | 1 | 0:20 | 14:30 |
| 10 | Wrap-Up | Ajay | 1 | 0:30 | 15:00 |
| — | Appendix (not presented) | — | 3+ | — | — |

27 body slides, exactly 15:00 — **zero buffer**. Adding Slide 6.6 (Justin's Agent Workflow close-out, +0:30) was offset by trims to Slides 6.3, 7.1, 7.2, 8.1, 9.1 (5–10s each) — net zero change to the total. First cut candidate if rehearsal runs long: Slide 7.2 (Ablation & Calibration) compressed into 7.1. Second candidate: cut Slide 4.1/5.1 overview shots to ~0:10 each (the highlight slides that follow re-establish context anyway). **Handoff note:** every section boundary above is a presenter handoff — rehearse the physical transitions (who clicks, who's mic'd) once, not just the content.

---

## 1. Team Introduction
**Presenter: Justin**

### Slide 1.1 — Who We Are
**Purpose:** fast, no lingering — establish credibility, move on.
**Content:**
- PortalPoint team, 4 members, roles:
  - **Shanth** — Architect & Data Engineer
  - **Ajay** — ML Engineer & Product
  - **Yoko** — ML Engineer
  - **Justin** — Product & Agent Engineer
- One line: "We built an end-to-end ML system, from raw box scores to a live recommendation engine, in production on AWS."
**Script cue:** name the four roles, don't dwell on individual bios. Signal immediately that this is a full-stack team (data eng + ML + product), which foreshadows why the project spans ingestion → modeling → infra → live app, not just a notebook.
**Time:** 0:25

---

## 2. Problem Space
**Presenter: Justin**

### Slide 2.1 — The Hook (stat-led cold open)
**Purpose:** land the scale of the problem in under 3 seconds of reading, before any product framing.
**Visual / Layout:**
- Cold-open exception to the standard header pattern — **no left accent bar, no eyebrow label** on this one slide; let the stat carry the room.
- 2-column layout (~40% / 55%, ~5% gutter, per palette's 2-column pattern):
  - **Left column — the stat block:** oversized numeral in Inter Black italic, orange `#FF6B35`, sized well above the standard hero token (custom one-off size, ~72–96px — this is the one slide licensed to break the type scale). Small caption directly beneath in `text-caption`/muted secondary color.
  - **Right column — the headline:** hero-scale title (Inter Black italic, 25px, white) + one subtitle line (Inter Light, `#B0C4DE`, body-lg).
- Background stays standard `#0D1B2A`. Optional: a faint blue (`#4A90E2`) upward sparkline behind/beside the numeral suggesting portal-volume growth since 2018 — decorative only, no axis labels, minimal chrome per dataviz conventions.
- Tiny citation footnote, bottom-right, `text-micro`, muted: source for the figure.
**Slide Text (exact copy):**
- Numeral: **"2,000+"**
- Caption under numeral: *"Division I transfers, most recent season*"*
- Headline (right column): **"No Data-Driven Way to Evaluate Any of Them."**
- Subtitle: *"Coaching staffs still rely on film study and word-of-mouth to make season-defining roster decisions."*
- Footnote: *"\*Confirm against current-season portal-tracker figure before presenting — verify source (On3/247Sports/NCAA) and cite it here."*
**Script cue:** let the numeral sit on screen a beat before speaking. Open with "two thousand." Not "the problem is." Land the number, then the gap.
**Note:** the original longer title ("2000+ D1 Players Enter the Portal Annually, With No Data-Driven Guide") is split across the numeral + caption (carries the "2,000+ D1 / annually" fact) and the headline (carries just the gap — "no data-driven way"). Splitting it this way is what makes both halves readable at a glance instead of one dense sentence.
**Time:** 0:40

### Slide 2.2 — Why This Is Getting Harder, Not Easier
**Purpose:** establish that this isn't a static problem — it's an accelerating operational one — and implicitly name the target user (front offices, mid-major programs) through the content itself rather than a separate labeled slide.
**Visual / Layout:**
- Standard header pattern returns here: 6px orange left-edge accent bar + eyebrow section label.
- 3-column equal-thirds layout (~29% each, ~6.5% gutters, per palette's 3-column pattern) — one card per impact point.
- Each card: standard translucent card (`rgba(255,255,255,0.10)`, 4px radius, 16px/20px padding) with a **Numbered Step** treatment inside (blue `#4A90E2` bold number "01/02/03", white bold step-label, `#B0C4DE` light body description below) — reuses the palette's existing Numbered Step + Content Card patterns together rather than inventing a new one.
- Optional supporting element beneath the 3 cards: a single small `card-accent-blue` quote chip (blue border/tint, per palette's "informational" card variant) for the domain-expert line, styled distinctly smaller than the 3 main cards so it reads as support, not a 4th equal point.
**Slide Text (exact copy):**
- Eyebrow label: **"WHY THIS MATTERS NOW"**
- Section title (text-section-header scale, not full hero): **"Portal Management Has Become a Front-Office Discipline"**
- Card 01 — label: **"Year-Round Volatility"** / desc: *"Roster turnover is no longer an offseason event — it's a continuous operational challenge that demands dedicated front-office infrastructure."*
- Card 02 — label: **"The Resource Gap"** / desc: *"Mid-major programs face the same volume and stakes as Power 5 programs, without the recruiting budgets or staff to match."*
- Card 03 — label: **"Front Offices Are Here"** / desc: *"D1 programs are rapidly adopting professional GM structures — a clear signal that data pipelines aren't optional anymore, they're infrastructure."*
- Quote chip (optional): `[DOMAIN EXPERT INSIGHT — fill in: quote or paraphrase from a coach/analyst on what manual portal scouting misses today]`, small attribution line beneath.
**Script cue:** land Card 02 with the most emphasis — it's the one that names who this is really for (under-resourced programs) without needing a separate "target user" slide. If the quote chip is empty by presentation day, drop it silently — the 3 cards stand on their own.
**Time:** 0:50

### Slide 2.3 — Mission
**Purpose:** state the mission once, cleanly, as a single declarative sentence — it gets echoed verbatim at the very end (Slide 10.1).
**Visual / Layout:**
- Deliberately the sparsest slide in the deck: full-bleed navy background, no cards, no columns, no bullets.
- One large centered line, Inter Black italic, white, sized between the stat-hero custom size (2.1) and the standard hero token — this is the "quiet after the loud stat" beat.
- One smaller supporting line beneath in `#B0C4DE`, Inter Light, tying the mission back to the equity tension just raised in Slide 2.2.
- No footer, no citation, no accent bar — whitespace does the work.
**Slide Text (exact copy):**
- Mission (large, centered): **"Every program deserves a data-driven front office — not just the ones who can afford one."**
- Supporting line (smaller, beneath): *"Quantitative player evaluation, systematized and made accessible — because manual scouting doesn't scale, and budget shouldn't be the differentiator."*
**Script cue:** deliver the mission line slowly, verbatim, then pause before moving on — this exact wording repeats at the close (Slide 10.1), and the callback only lands if it's word-for-word both times.
**Time:** 0:25

---

## 3. Demo — Two Running Examples
**Presenter: Ajay + Shanth**

**Framing for this whole section:** present in first person, walking a coaching staff through their own tool. Pick **two real candidate players from the live app** before rehearsal — one that showcases a clean, high-confidence fit, one that showcases a genuinely uncertain/edge case. Carry both by name through the rest of the talk (Section 6 revisits them to explain *why* the model produced these exact results). Narrative-script only in this doc — screen-share the live app (`https://d331zwrxbrp79d.cloudfront.net`) for the actual talk.

`[PICK 2 PLAYERS — fill in real names/positions from the live DB before final run-through; suggested casting below]`
- **Player A ("the clean case"):** a candidate with strong Scheme Fit + Gap Match + a clear positive Team Rating delta — ideally a low-major→high-major transfer, since that cohort is where the model's validation is strongest (Section 7 will cite this same cohort's real numbers).
- **Player B ("the honest edge case"):** a candidate where the tool visibly shows uncertainty — e.g. a wide confidence interval on Role Fit, or a profile leaning on the defensive-playmaking skill block, which Section 6 will disclose as the one skill block that doesn't validate cleanly cross-season.

### Slide 3.1 — The Scenario
**Purpose:** ground the demo in one concrete, named user and one concrete, named need — so the ranked list on the next slide reads as "built for this exact ask" instead of a generic feature tour. This is what makes Player A/B feel earned, not arbitrarily picked.
**Visual / Layout:**
- Standard header pattern (accent bar + eyebrow label) at top, but the body is a single wide **intake-ticket card** — not bullets, not columns — styled like a filled-out scouting request: a left rail of small monospace field labels, values in white beside each.
- This grounds the slide in the subject's own vernacular (a coach submitting a real ask to their staff) rather than a generic bulleted "context" slide.
- `[FICTIONAL PERSONA — for demo narrative purposes only, not a real staff member]`
**Slide Text (exact copy — ticket fields):**
- `REQUESTED BY` — Alex Chen, Assistant Coach / Recruiting Coordinator
- `PROGRAM` — California Golden Bears (UC Berkeley) Men's Basketball
- `SYSTEM` — Up-tempo, ball-movement offense · switch-everything defense
- `ROSTER NEED` — Lost two starting wings to graduation — need 3-and-D wing scoring and secondary shot creation
- `FILTER` — Available portal players only · fits our tempo/style · immediate-impact, not a multi-year project
- Closing line beneath the ticket, smaller, italic: *"Let's see what PortalPoint gives Alex."*
**Script cue:** read the ticket like a real request, not a slide of bullets — "Alex needs wing shooting, plays fast, and doesn't have three years to develop a project." Then click into the live app.
**Note:** Cal (Berkeley) is a deliberate choice, not a random pick — a high-major program that's still resource-constrained relative to blue-bloods ties directly back to Section 2's resource-gap framing (Slide 2.2, Card 02) without having to say so explicitly.
**Time:** 0:30

### Slide 3.2 — Dashboard: "Who should we be looking at?"
**Purpose:** answer Alex's first question — a ranked, live list built against the exact filter just stated, not a spreadsheet.
**Content / demo beats:**
- Open the Dashboard — live, ranked recommendations feed for the logged-in program (M7 Recommendation Engine, `rec-v1.2`), already reflecting Cal's `available_only` + roster-need context.
- Point out Player A and Player B on the list. Say to camera-as-Alex: *"Every player here is already ranked for our program specifically — not a generic top-100. Let's look at two of them closely."*
**Script cue:** name the pain point out loud: "before this, that ranking lived in someone's head."
**Time:** 0:45

### Slide 3.3 — Player A Deep-Dive: "Why does this player fit us?"
**Purpose:** answer the *why* question with the explainable four-component breakdown, using the clean case.
**Content / demo beats:**
- Click into Player A → fit-score breakdown: Scheme Fit, Gap Matching, Role Fit, (Program Fit placeholder, flagged as such).
- Show `is_portal_candidate` / `is_current_school` badges.
- Show the Player Projection card and the Team Rating Projection delta — "signing Player A is worth about +X points of projected efficiency margin."
**Script cue:** say explicitly "this is not a black box — every number breaks into parts a coach can argue with." Plant the flag: "hold onto Player A — we'll come back to exactly why this score is high."
**Time:** 0:55

### Slide 3.4 — Player B Deep-Dive: "What does the tool do when it's not sure?"
**Purpose:** show the tool being honest about uncertainty, not just confident — sets up Section 6's edge-case discussion.
**Content / demo beats:**
- Click into Player B → same breakdown, but highlight the wider confidence interval or the weaker-signal component.
- Say to camera-as-Alex: *"The tool doesn't hide this — it tells you when a projection is a safer bet versus a bigger swing."*
- `[USER TESTIMONIAL — fill in: quote from a coach/staff member who saw the demo; if unavailable, state plainly what informal feedback confirmed instead of inventing a quote]`
**Script cue:** plant the second flag: "hold onto Player B too — we'll explain exactly where this uncertainty comes from."
**Time:** 0:55

---

## 4. Data & Pipeline
**Presenter: Shanth**

**Framing for this whole section:** a "build" — one diagram, shown whole first, then the same diagram repeated with one region highlighted (accent-orange border + tint) and the rest dimmed, one region per slide. Real content, not decoration: this is the actual ingest pipeline (`docs/dataflow_diagram.mmd`, simplified for a 15-minute talk — that file now shows all 9 models built and includes the News Monitoring Agent + `program_events`, current as of this pass), not a redrawn mermaid export.
**Visual convention (applies to all 4 slides below):** flow arrows between pipeline stages are blue (`#4A90E2`) — "this is data moving," a neutral/informational signal. The one region under discussion gets the orange accent treatment (border + tint, per the palette's `card-accent-orange`); everything else drops to ~35% opacity. This orange-for-attention / blue-for-flow split is the same convention Section 5's infrastructure build reuses, so the audience only has to learn it once.

### Slide 4.1 — Full Pipeline (Overview)
**Purpose:** orient the room on the whole shape before zooming into any one part.
**Visual / Layout:** left-to-right chain, all nodes at normal (non-dimmed) state: **Sources** (5 small stacked chips) → **Ingest Scripts** → **PostgreSQL** → **Feature Engineering** → a faded, dashed-arrow pill labeled **"9 ML Models →"** pointing off the right edge (forward reference to Section 6, intentionally muted since it's not this section's job to explain).
**Slide Text:** stage labels only, no bullets yet — this slide is the establishing shot.
**Script cue:** "Five sources, one pipeline, feeding nine models. Let's walk it."
**Time:** 0:20

### Slide 4.2 — Highlight: Data Sources
**Purpose:** establish real variety and scale before any modeling claims are made.
**Visual / Layout:** same diagram, the 5 source chips glow orange, everything downstream dims to ~35% opacity.
**Slide Text (callout beneath diagram):**
- **"Five sources, four different grains of the same reality."** BartTorvik (box score + Four Factors), Hoop Explorer (RAPM, spatial shot data, play-type frequency), hoopR/ESPN play-by-play (2.9M events/season, 2020–2026 backfilled), 247Sports (transfer portal events), BartTorvik roster snapshots.
**Script cue:** "We didn't train on one CSV."
**Time:** 0:25

### Slide 4.3 — Highlight: Ingest → PostgreSQL
**Purpose:** show real scale, and the one EDA finding that most shaped downstream modeling.
**Visual / Layout:** the Ingest Scripts and PostgreSQL nodes (plus the arrow between them) glow orange; Sources and everything past PostgreSQL dim.
**Slide Text (callout beneath diagram):**
- **Scale:** ~13,300 players, ~1.17M player-game log rows, ~9.7M all-pairs fit-score rows, ~27 tables.
- **A real bug EDA caught, not a test:** `players.position` sat hardcoded to `'G'` for all 13,303 players for months — found by looking at the data, not by a unit test. Re-running ingest recovered a real position distribution and un-froze a dependent fallback path in Gap Matching.
**Script cue:** "EDA didn't just describe the data here — it caught a live bug."
**Time:** 0:30

### Slide 4.4 — Highlight: Feature Engineering → Models
**Purpose:** close the loop into Section 6 without stealing its content.
**Visual / Layout:** Feature Engineering node and the dashed hand-off arrow into the "9 ML Models →" pill glow orange; everything upstream dims.
**Slide Text (callout beneath diagram):**
- **"This parquet output is what Section 6's nine models actually train on."** Feature notebooks join BartTorvik + Hoop Explorer + hoopR into player- and team-level feature sets — the last stop before modeling.
**Script cue:** land this as a clean handoff line, then move straight into Section 5 (infrastructure) before circling back to modeling in Section 6.
**Time:** 0:20

---

## 5. System Architecture & Infrastructure
**Presenter: Shanth**

**Framing for this whole section:** the same "build" technique as Section 4, same visual convention (blue = flow, orange = the region under discussion, dimmed = not yet). This diagram reflects the **actual deployed system** as of 2026-07-21 (per `CLAUDE.md`'s Production Deployment section, and matching `docs/solution_architecture.mmd`, which was rewritten from those same facts this pass) — deliberately *not* the older aspirational ASCII diagram still sitting in `docs/diagram_2_solution_architecture.md` (WebSocket service, XGBoost model-serving, multi-instance ECS, Celery/RabbitMQ, Airflow), none of which reflects what's actually running today. Getting this right matters for a technical-rigor presentation — showing the aspirational diagram would overstate the infrastructure. One detail worth carrying into the script: `solution_architecture.mmd` also keeps the aspirational pieces (Airflow, Celery, WebSocket, Prometheus, drift monitoring) visible in an explicitly labeled "Planned / Not Implemented" cluster with dashed connectors, rather than deleting that history — a good one-line callback if there's time on Slide 5.4 ("we designed for this, chose not to build it yet, and said so").
**Layout convention:** two lanes. **Top lane = live request path** (solid arrows) — what happens when a coach loads the app. **Bottom lane = deploy/ops plane** (dashed arrows) — what happens when the team ships code or trains a model; this plane doesn't serve live requests.

### Slide 5.1 — Full Infrastructure (Overview)
**Purpose:** orient the room on both lanes before zooming in.
**Visual / Layout:**
- Top lane (solid, left→right): **Browser (React SPA)** → **CloudFront + S3** → **ALB** → **ECS Fargate (FastAPI)** → two boxes side by side: **RDS Postgres** and **ElastiCache Redis**.
- Bottom lane (dashed, beneath): **GitHub Actions (OIDC)** → **ECR / ECS Deploy** (dashed arrow up into the ECS box above) · a separate **MLflow + S3 (artifact store)** box, dashed-connected to the ECS box (used by training scripts, not the live request path).
**Slide Text:** stage labels only — establishing shot, no bullets yet.
**Script cue:** "Two lanes: what serves a coach right now, and what ships the code and trains the models that make that possible."
**Time:** 0:20

### Slide 5.2 — Highlight: Frontend Delivery
**Purpose:** show the frontend is a real deployed SPA, not a dev server.
**Visual / Layout:** Browser + CloudFront/S3 nodes glow orange; rest of both lanes dim.
**Slide Text (callout beneath diagram):**
- **"React SPA, served from S3 behind CloudFront — `/api/*` routed to the backend at the CDN layer."** The app only ever calls relative paths; it doesn't know or care that the API lives on a different origin.
**Script cue:** keep this one brief — it's the least eventful lane, say it and move on.
**Time:** 0:20

### Slide 5.3 — Highlight: Backend + Data Layer
**Purpose:** show the backend is production-hardened, not a toy — real health checks, real caching.
**Visual / Layout:** ALB + ECS Fargate + RDS Postgres + ElastiCache Redis glow orange; frontend and bottom lane dim.
**Slide Text (callout beneath diagram):**
- **"ECS Fargate behind an ALB, health-checked on a real DB-backed `/ready` probe"** — not a naive `/health` that just says the process is alive.
- **Redis is live**, not a stub — powers fit-score caching and the news-monitoring agent's run-tracking.
**Script cue:** "This is the part that has to actually work under load — here's why we trust it does."
**Time:** 0:30

### Slide 5.4 — Highlight: CI/CD + MLOps
**Purpose:** show real deployment discipline and a real incident it fixed — the most substantive beat in this section.
**Visual / Layout:** the entire bottom lane (GitHub Actions, ECR/Deploy, MLflow + S3) glows orange; both top-lane clusters dim.
**Slide Text (callout beneath diagram):**
- **"GitHub Actions (OIDC, no long-lived keys) deploys on merge to `main`."** Database migrations are a gated, blocking pre-deploy step — added after a real incident where a merge's migrations were never applied because the pipeline didn't check.
- **Every model is MLflow-tracked**, with a formal +5% improvement bar before anything auto-promotes to production.
**Script cue:** "We found and fixed a real deployment gap here — that's covered again in Section 8 as one of the top technical challenges."
**Time:** 0:30

---

## 6. Modeling Approach — Back to the Slides
**Presenter: Ajay + Yoko (Slides 6.1–6.5); Justin closes with Slide 6.6 (Agent Workflow)**

**Section framing:** explicitly reconnect to Section 3. Open with: *"Let's go back to Player A and Player B and actually explain those numbers."*

### Slide 6.1 — End-to-End Model Architecture
**Purpose:** show the system as a pipeline of models feeding each other, not 9 unrelated experiments.
**Content:** walk the dependency chain in one breath:
1. **Clustering** (player archetypes, team system profiles) →
2. **Scheme Fit** (cosine similarity, style vector) + **Gap Matching** (cosine similarity vs. roster holes) →
3. **Playing Time Predictor** (minutes/usage, feeds Role Fit) →
4. **Player Projection** (context-neutral talent layer: shrinkage + Ridge, then Kalman state-space across seasons) →
5. **Destination-Adjusted Projection** (translates neutral talent into *this-roster* context) →
6. **Team Rating Projection** (roster counterfactual — what adding this player does to team efficiency) →
7. **Transfer Success Predictor** (did similar past moves actually work out) →
8. **Recommendation Engine** (2-stage rank, combines everything into the ranked list from Section 3).
**Script cue:** "every model downstream consumes the model before it — Player A's Team Rating delta in Section 3 came from step 6, which itself depended on steps 4 and 5 being right."
**Time:** 0:50

### Slide 6.2 — Models Explored & Why We Landed Where We Did
**Purpose:** directly answer "what did you try, what did you pick, why" — the strategic-thinking ask.
**Content (concrete decisions, not a generic model zoo):**
- **Transfer Success:** empirical-Bayes shrinkage over XGBoost — explicit product decision favoring interpretability and stability on a modest labeled set (2,143 labeled transfers) over a black-box accuracy gain.
- **Team Rating:** Ridge over higher-capacity models — 2,158 school-seasons is small-n; Ridge's regularization and stable coefficients mattered more than marginal fit.
- **Player Projection:** Bayesian state-space (Kalman) over static regression — skill evolves and single seasons are noisy; state-space filtering is the standard tool for exactly that, versus most public CBB rating systems, which are static single-season snapshots.
- **Builds on, doesn't replace, existing public work:** BartTorvik (AdjEM/Four Factors) and Hoop Explorer (RAPM) supply the underlying talent signal; our contribution is the layer neither provides — destination-specific counterfactual projection and need-based matching.
**Script cue:** land the sentence "we didn't reinvent player ratings — we built the fit and matching layer on top of the best public ones."
**Time:** 0:35

### Slide 6.3 — Features & What Matters Most
**Purpose:** ground the "explainable, not black-box" claim in specifics.
**Content:**
- Scheme Fit: 5-dim style vector — 3PT rate, rim rate, usage, assisted-rate, pace.
- Team Rating: 14 roster-composition features (returning minutes, positional depth, class balance, etc.).
- Most important finding: competition-tier transfer direction is a strong differentiator — low-major→high-major shows real signal (Spearman 0.67, n=156) vs. weak/small-sample in reverse (n=36), reported honestly rather than smoothed over.
**Script cue:** tie directly to Player A: "if Player A is a low-major→high-major mover, this is exactly the cohort where we have the most confidence."
**Time:** 0:25

### Slide 6.4 — Back to Player A and Player B: Where the Model Works, Where It Doesn't
**Purpose:** the payoff slide for the whole running-example structure — directly answers "main use case vs. edge cases" using the two players the audience already met.
**Content:**
- **Player A, explained:** clean case because it likely sits in the validated cohort — cross-season persistence validates cleanly for creation and rebounding skill blocks, and low-major→high-major transfers carry real, usable signal. That's why the Team Rating delta and fit breakdown looked confident in Section 3.
- **Player B, explained:** honest edge case because it likely touches a weaker part of the model — defensive-playmaking skill doesn't validate cross-season as cleanly (plausibly a genuinely low-persistence skill, not a pipeline bug), or the transfer direction is the weak/small-sample one (high-major→mid-major).
- One additional disclosed limitation: an experimental context-adjustment layer was tested and made results *worse* on real data (fold 3 off_rmse 1.987 vs. 1.633 without it) — flagged, not shipped.
**Script cue:** say plainly "we're not showing you two wins — we're showing you a win and a case where the tool tells you to be careful, on purpose."
**Time:** 0:55

### Slide 6.5 — Interpretability: Tying This Back to the User's Decision
**Purpose:** close the modeling section by reconnecting technical detail to user value — the "how is this useful/interpretable" ask.
**Content:**
- Every score the coach sees in Section 3 decomposes into named, arguable components — never a single opaque number.
- Uncertainty is surfaced, not hidden — Player B's wider interval is a feature of the system, not a gap in it.
- Net effect: the model doesn't replace the coach's judgment — it gives a ranked, explainable, honestly-uncertain starting point instead of starting from zero.
**Script cue:** "the goal was never to hand a coach a number and ask for trust — it was to hand them a number, the reasons behind it, and how much to trust it."
**Time:** 0:35

### Slide 6.6 — Agent Workflow (presented by Justin, closing Section 6)
**Purpose:** close the modeling section with the one real system in the deck that isn't a fit-score or projection model — the News Monitoring Agent — and show it's wired into everything just discussed, not a side project.
**Visual / Layout:** simple 3-step flow, left to right: **Tavily search** → **dual classifier** (regex confidence-tiered / Gemini structured-output, toggle-able) → **two tools**: `transfer_player` (writes `transfer_portal_events`/`transfers`, then calls the same `sync_portal_candidate_flags()` Section 6.1's pipeline already depends on) and `coach_departure` (sets `stale_flag`/`stale_reason` on `team_system_profiles`). A small callout beneath: this stale flag is what Section 3's `scheme_fit_stale` badge (mentioned in the Player A/B fit breakdown) actually comes from.
**Slide Text:**
- **"The pipeline doesn't wait for the next ingest run to know a coach left."** A LangGraph ReAct agent watches for portal entries and coaching changes in near-real-time via Tavily search, classifies them (regex or Gemini, confidence-scored), resolves player/school identity with the same fuzzy-matching logic the 247Sports ingest already uses (`entity_resolution.py` — shared module, not a fork), and writes straight into the tables the rest of the pipeline reads.
- **Real, shipped, not aspirational:** production scaffolding complete (Gates 1–5 + 7), 67 pure-unit tests, zero DB dependency in CI. VerbalCommits/beat-writer sources and the alert digest are explicitly next, not yet built.
**Script cue:** Justin ties this back to Player B's staleness flag from the demo and to Section 5's infra (this agent's run-tracking is one of the two real things Redis does in production) — a genuine full-circle close before Evaluation opens.
**Time:** 0:30

---

## 7. Evaluation
**Presenter: Yoko**

### Slide 7.1 — Baseline, Validation, Comparison
**Purpose:** show rigor — a defined baseline, a real validation scheme, honest comparison.
**Content:**
- **Validation scheme:** rolling-origin (temporal) cross-validation throughout — never a random split — every model predicts forward in time.
- **Defined baseline:** Phase 0 (static shrinkage+Ridge) is what every downstream refinement is measured against; Phase 2a (cross-season state-space) beats it on offense in all 3 folds (~5-6% RMSE reduction each fold) and ties on defense — a real, modest win, reported as such.
- **Auto-promotion bar:** every model must clear a formal +5% improvement vs. current production baseline (MLflow-tracked) before promotion — one caught bug (a false `Δ=+inf%` from a missing-metric zero-division) was manually caught and reverted before it could false-promote a worse model.
**Script cue:** the caught false-promotion bug is a concrete "we have real guardrails, and they worked" beat.
**Time:** 0:45

### Slide 7.2 — Ablation & Calibration
**Purpose:** show the ablation-study ask directly, and connect back to the disclosed limitation on Slide 6.4.
**Content:**
- **Ablation 1:** offense/defense feature-set split — defense R² dropped ~30% relative when offense features were removed; kept the split anyway for interpretability, cost accepted explicitly.
- **Ablation 2:** the context-adjustment layer from Slide 6.4 — tested, made real results worse, not adopted.
- **Calibration:** Transfer Success Predictor evaluated via Brier score (0.2492, out-of-sample) — measures whether stated probabilities are trustworthy, not just whether rankings are ordered correctly.
**Script cue:** "that edge case we showed you with Player B wasn't a guess — it's backed by an ablation that measured it directly."
**Time:** 0:25

---

## 8. Key Technical Takeaways
**Presenter: Shanth**

### Slide 8.1 — Top 3 Challenges Overcome
**Purpose:** the technical-depth payoff slide — concrete, not generic ("we learned a lot about teamwork").
**Content:**
1. **Scale:** all-pairs scoring across ~9.7M player×school×season rows required real engineering — vectorized per-pair computation, fixed a 15× redundant-CTE-reevaluation bug in the playing-time query, parallelized cross-season model fitting across a process pool (with a real OOM bug found and fixed along the way).
2. **Real-world entity resolution:** matching player identities across three independently, messily-formatted sources (247Sports, BartTorvik, Hoop Explorer) — a 3-pass fuzzy-match pipeline (name normalization, position pre-filtering, strict-then-relaxed thresholds) took match rate from ~0% to 87–91%.
3. **Statistically sound uncertainty modeling on sparse data:** a subtle noise-model bug (Bernoulli-shaped observation noise applied to Poisson-shaped count stats) silently capped model precision; fixing it lifted Phase 0/Phase 1 agreement from 0.15–0.39 correlation to 0.50–0.81. A related non-identifiability issue (jointly fitting persistence and drift parameters) was solved by switching to a simpler, more robust lag-1 autocorrelation estimator instead of full MLE.
**Script cue:** each of these is "we found a real bug/limit, diagnosed the actual cause, fixed the right thing" — say that pattern once, let the three examples carry it.
**Time:** 0:30

---

## 9. Roadmap & Generalizability
**Presenter: Ajay**

### Slide 9.1 — What's Next, and Where Else This Applies
**Purpose:** show forward thinking without overpromising.
**Content:**
- **Top roadmap items (priority order):**
  1. Feature-drift / model-decay monitoring (Prometheus/Grafana) — designed for from day one, not yet built.
  2. Replace rule-based style/skill-fit deltas with an empirically fit model — the single highest-leverage remaining accuracy gain identified.
  3. Player-specific (not global-width) confidence intervals — directly addresses the Player B-style uncertainty communication shown in the demo.
- **Generalizability:** the architecture — shrinkage + state-space skill projection, explainable multi-component fit scoring, roster-counterfactual rating, recommendation layer on top — is sport-agnostic. Swap the feature set and this pipeline applies to other sports' transfer/free-agency markets, or more broadly to any "match a mover to a role with a gap" problem.
**Script cue:** keep to one breath per bullet — brisk, not a second roadmap talk.
**Time:** 0:20

---

## 10. Wrap-Up
**Presenter: Ajay**

### Slide 10.1 — Mission, Restated
**Purpose:** land the callback to Slide 2.3, one clean sentence, then stop talking.
**Visual / Layout:** identical treatment to Slide 2.3 — full-bleed navy, no cards, no accent bar, centered type. The repetition of the *layout*, not just the words, is what sells the callback.
**Content (verbatim, single line, large type, nothing else on the slide):**
> "Every program deserves a data-driven front office — not just the ones who can afford one. PortalPoint is that front office, built, tested, and live."
**Script cue:** deliver slowly, pause, stop. No "any questions" filler on the slide itself — save that for verbally after. Wording must match Slide 2.3's mission line exactly for the first clause — only the closing clause changes, from aspiration to "we built it."
**Time:** 0:30

---

## Appendix (not presented — reference only)

### A.1 — Acknowledgements
- Domain experts / coaches consulted (fill in names once confirmed).
- Data sources credited: BartTorvik, Hoop Explorer, hoopR/ESPN, 247Sports.

### A.2 — Additional Resources
- `docs/models/` — per-model design docs (Player Projection state-space plan, Team Rating Projection plan, Recommendation Engine plan, etc.)
- `docs/status/MODEL_STATUS.md` / `ARCHITECTURE_STATUS.md` — authoritative live status
- `docs/dataflow_diagram.mmd` — real source diagram for Section 4's build sequence (simplified for the talk). Brought current this pass: all 9 models now shown built, News Monitoring Agent + `program_events` added — safe to reuse or extend directly.
- `docs/model_pipeline.mmd` — the detailed model-level flow (all 9 models, tables, MLflow status) that Section 6's architecture slide (6.1) simplifies further. Also brought current this pass — good backup if Q&A goes deep on any one model's inputs/outputs.
- `docs/solution_architecture.mmd` — **the real source diagram for Section 5**, rewritten this pass from `CLAUDE.md`'s Production Deployment facts. Good backup for Q&A depth beyond Slide 5.1's summary — includes the explicit "Planned / Not Implemented" cluster (Airflow, Celery, WebSocket, Prometheus, drift monitoring) with dashed connectors showing where each would have plugged in.
- `docs/diagram_2_solution_architecture.md` — **older ASCII-art planning diagram, still aspirational and NOT fixed this pass.** Describes infrastructure that was never built (WebSocket service, XGBoost model-serving, multi-instance ECS, Celery/RabbitMQ, Airflow). Do not use for Section 5 or Q&A — kept for historical reference only.
- `docs/road_to_production.md` — deployment history
- Live app: `https://d331zwrxbrp79d.cloudfront.net`

### A.3 — Backup Slides (if Q&A needs depth)
- Full 9-model table with versions and MLflow status.
- Full architecture diagram (`docs/solution_architecture.mmd`) — every infra component (ECS, RDS, ElastiCache, CloudFront, S3, GitHub Actions) with data flow arrows, plus the "Planned / Not Implemented" cluster, not just the summary bullets from Slide 5.1.
- Database schema diagram (5 logical layers, ~27 tables).
- Full CV fold-by-fold metrics table for Player Projection Phase 2a and Team Rating Projection.

---

## Open Items Before Final Run-Through

1. **Pick the 2 real running-example players** (Section 3) from the live app — one clean/high-confidence case, ideally a wing who plausibly fills Cal's stated need (3-and-D scoring/creation) *and* is a low-major→high-major transfer, since that's both the scenario's ask and the cohort where the model validates strongest; one honest edge case (wide CI or defensive-playmaking-heavy profile). Confirm both actually render well in the UI before rehearsal.
2. Confirm Cal (UC Berkeley) actually resolves cleanly in the live app as the logged-in program for the demo login — the scenario (Slide 3.1) is built around it, so a login/data mismatch here breaks the opening beat.
3. Fill `[DOMAIN EXPERT INSIGHT]` (Slide 2.2) and `[USER TESTIMONIAL]` (Slide 3.4), or explicitly cut those beats.
4. Confirm live-demo path (Scenario → Dashboard → Player A → Player B) works end-to-end on the production URL immediately before presenting — no dry run, no surprises.
5. Rehearse with a timer once — budget is now exact at 15:00, zero buffer; if running long, first cut candidate is Slide 7.2 (Ablation & Calibration) compressed into Slide 7.1.
