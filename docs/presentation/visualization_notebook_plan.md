# Visualization Notebook Plan
## `notebooks/presentation/technical_walkthrough_visuals.ipynb`

**Purpose:** Generate all charts, tables, and graphs for the PortalPoint technical walkthrough presentation. Pulls live data from DB + MLflow artifacts. Produces publication-ready figures saved to `docs/presentation/figures/`.

**Not a modeling notebook** — no fitting, no writing to DB. Read-only queries + visualization only.

---

## Dependencies

```python
# Standard
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns
from pathlib import Path

# Dimensionality reduction
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
import umap  # pip install umap-learn

# DB connection
from portalpoint.modeling.io import get_sync_engine, load_env
from sqlalchemy import text

# MLflow
import mlflow
from portalpoint.modeling.mlflow_helpers import setup_mlflow

load_env()
engine = get_sync_engine()
setup_mlflow()

# Output dir
FIG_DIR = Path("../../docs/presentation/figures")
FIG_DIR.mkdir(parents=True, exist_ok=True)

# Target players and school
SCHOOL_ID = 8           # Gonzaga
TARGET_PLAYERS = {
    "Ruffin":   7578028029286400392,   # Daeshun Ruffin, PG, Jackson St.
    "Crawford": 6910442837336165955,   # Elijah Crawford, PG, Illinois Chicago
    "Evans":    9023425631028193516,   # Kyle Evans, C, UC Irvine
}
SEASON = 2026
PLAYER_COLORS = {"Ruffin": "#1f77b4", "Crawford": "#d62728", "Evans": "#2ca02c"}
```

---

## Section 0 — Setup: Resolve Real Player IDs

```python
# Cell 0-1: Verify real player_ids by name + season for all three targets

PLAYER_LOOKUP_SQL = """
SELECT p.id, p.full_name, p.position, pss.season, s.name as school
FROM players p
JOIN player_season_stats pss ON pss.player_id = p.id
JOIN schools s ON s.id = pss.school_id
WHERE p.id = ANY(:player_ids)
  AND pss.season = :season
ORDER BY p.full_name, pss.season
"""
players_df = pd.read_sql(
    text(PLAYER_LOOKUP_SQL),
    engine,
    params={"player_ids": list(TARGET_PLAYERS.values()), "season": SEASON},
)
display(players_df)
```

---

## Section 1 — Team Clustering (M2)

### Cell 1-1: Load Team Cluster Data

```python
TEAM_CLUSTER_SQL = """
SELECT
    tsp.school_id,
    s.name                          AS school_name,
    tsp.season,
    tsp.offense_cluster,
    tsp.defense_cluster,
    tsp.system_label,
    -- Offense features
    tss.three_pt_rate,
    tss.rim_rate,
    tss.pace,
    -- Hoop Explorer features (assisted%, transition, scramble)
    het.off_assisted_pct,
    het.off_trans_pct,
    het.off_trans_ppp,
    het.off_scramble_pct
FROM team_system_profiles tsp
JOIN schools s ON s.id = tsp.school_id
JOIN team_season_stats tss ON tss.school_id = tsp.school_id AND tss.season = tsp.season
LEFT JOIN hoop_explorer_team_stats het ON het.school_id = tsp.school_id AND het.season = tsp.season
WHERE tsp.season = :season
"""
team_df = pd.read_sql(text(TEAM_CLUSTER_SQL), engine, params={"season": SEASON})
gonzaga_row = team_df[team_df.school_id == SCHOOL_ID].iloc[0]
gonzaga_cluster = gonzaga_row["offense_cluster"]
```

### Cell 1-2: VISUAL 1a — K Selection Curves (load from MLflow artifact)

Load the silhouette and inertia curves saved during the team clustering run.

```python
# Load MLflow artifact: team clustering run logged silhouette_scores and inertia as arrays
client = mlflow.MlflowClient()
model_version = client.get_model_version_by_alias("team-clustering", "champion")
run_id = model_version.run_id
run = client.get_run(run_id)

# Pull k_range, silhouette_scores, inertia from run metrics or artifact JSON
# (If not logged separately, fall back to re-reading the artifact pkl for centroid count)
# Expect artifact: "k_selection_data.json" with keys: k_range, silhouette, inertia

artifact_path = mlflow.artifacts.download_artifacts(run_id=run_id, artifact_path="k_selection_data.json")
import json
with open(artifact_path) as f:
    k_data = json.load(f)

fig, axes = plt.subplots(1, 2, figsize=(12, 4))
axes[0].plot(k_data["k_range"], k_data["silhouette"], marker="o", color="#1f77b4")
axes[0].set_title("Silhouette Score vs. K (Team Clustering)")
axes[0].set_xlabel("K"); axes[0].set_ylabel("Silhouette Score")
axes[0].axvline(x=gonzaga_cluster, color="red", linestyle="--", alpha=0.5, label=f"Chosen K")

axes[1].plot(k_data["k_range"], k_data["inertia"], marker="o", color="#ff7f0e")
axes[1].set_title("Inertia (Elbow) vs. K")
axes[1].set_xlabel("K"); axes[1].set_ylabel("Inertia")

plt.tight_layout()
plt.savefig(FIG_DIR / "1a_team_k_selection.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Cell 1-3: VISUAL 1b — 2D PCA Scatter of Team Clusters

```python
feature_cols = ["three_pt_rate", "rim_rate", "pace", "off_assisted_pct", "off_trans_pct"]
feat_df = team_df[feature_cols].fillna(team_df[feature_cols].median())

pca = PCA(n_components=2)
coords = pca.fit_transform(StandardScaler().fit_transform(feat_df))
team_df["pc1"] = coords[:, 0]
team_df["pc2"] = coords[:, 1]

fig, ax = plt.subplots(figsize=(12, 8))

# Color by cluster
labels = team_df["system_label"].unique()
palette = dict(zip(labels, sns.color_palette("tab10", len(labels))))

for label, grp in team_df.groupby("system_label"):
    ax.scatter(grp["pc1"], grp["pc2"], label=label,
               color=palette[label], alpha=0.5, s=40)

# Highlight Gonzaga
gz = team_df[team_df.school_id == SCHOOL_ID]
ax.scatter(gz["pc1"], gz["pc2"], color="black", s=200, zorder=5, marker="*")
ax.annotate("Gonzaga", (gz["pc1"].values[0], gz["pc2"].values[0]),
            fontsize=11, fontweight="bold", xytext=(5, 5), textcoords="offset points")

# Label a few peer programs in same cluster
gonzaga_label = gonzaga_row["system_label"]
peers = team_df[team_df.system_label == gonzaga_label].nlargest(5, "three_pt_rate")
for _, r in peers.iterrows():
    if r.school_id != SCHOOL_ID:
        ax.annotate(r["school_name"], (r["pc1"], r["pc2"]), fontsize=8, alpha=0.8)

ax.set_title("Team System Clusters — PCA Projection (2026 Season)", fontsize=13)
ax.set_xlabel(f"PC1 ({pca.explained_variance_ratio_[0]:.1%} var)")
ax.set_ylabel(f"PC2 ({pca.explained_variance_ratio_[1]:.1%} var)")
ax.legend(loc="upper right", fontsize=8)

plt.tight_layout()
plt.savefig(FIG_DIR / "1b_team_cluster_scatter.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Cell 1-4: VISUAL 1c — Centroid Feature Heatmap

```python
# Compute per-cluster mean for each feature
centroid_df = team_df.groupby("system_label")[feature_cols].mean()

fig, ax = plt.subplots(figsize=(10, 6))
sns.heatmap(
    centroid_df.T,
    annot=True, fmt=".2f", cmap="RdYlGn",
    linewidths=0.5, ax=ax,
    cbar_kws={"label": "Feature value (scaled means)"}
)
ax.set_title("Team Cluster Centroids — Feature Heatmap", fontsize=13)
ax.set_xlabel("System Label"); ax.set_ylabel("Feature")

# Highlight Gonzaga's column
gonzaga_col_idx = list(centroid_df.index).index(gonzaga_label)
ax.add_patch(plt.Rectangle((gonzaga_col_idx, 0), 1, len(feature_cols),
             fill=False, edgecolor="black", lw=3))

plt.tight_layout()
plt.savefig(FIG_DIR / "1c_team_centroid_heatmap.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Cell 1-5: VISUAL 1d — Peer Programs Table

```python
gonzaga_peers = (
    team_df[team_df.system_label == gonzaga_label]
    [["school_name", "three_pt_rate", "rim_rate", "off_assisted_pct", "pace", "off_trans_pct"]]
    .sort_values("off_assisted_pct", ascending=False)
    .head(10)
    .rename(columns={
        "school_name": "School", "three_pt_rate": "3PT Rate",
        "rim_rate": "Rim Rate", "off_assisted_pct": "Assisted%",
        "pace": "Pace", "off_trans_pct": "Trans%"
    })
)
display(gonzaga_peers.style.highlight_max(color="#c6efce").format(precision=3))
```

---

## Section 2 — Player Clustering (M1)

### Cell 2-1: Load Player Cluster Data

```python
PLAYER_CLUSTER_SQL = """
SELECT
    pa.player_id,
    p.name,
    p.position,
    pa.season,
    pa.archetype_label,
    pa.cluster_id,
    pa.distance_to_centroid,
    pss.ortg,
    pss.usage,
    pss.three_pt_rate,
    pss.rim_rate,
    pss.bpm, pss.obpm, pss.dbpm,
    he.pos_confidence_pg, he.pos_confidence_sg,
    he.pos_confidence_sf, he.pos_confidence_pf, he.pos_confidence_c
FROM player_archetypes pa
JOIN players p ON p.id = pa.player_id
JOIN player_season_stats pss ON pss.player_id = pa.player_id AND pss.season = pa.season
LEFT JOIN hoop_explorer_player_stats he ON he.player_id = pa.player_id AND he.season = pa.season
WHERE pa.season = :season
"""
player_arch_df = pd.read_sql(text(PLAYER_CLUSTER_SQL), engine, params={"season": SEASON})

# Pull target player rows
target_rows = player_arch_df[player_arch_df.player_id.isin(TARGET_PLAYERS.values())]
```

### Cell 2-2: VISUAL 2a — K Selection Curves (from MLflow artifact)

Same pattern as Cell 1-2 but for player clustering model (`player-clustering` registered model).

```python
# Load "k_selection_data.json" from player-clustering champion run
# Plot silhouette + elbow curves for k=6–15
# Mark chosen k=9 with vertical line
```

### Cell 2-3: VISUAL 2b — UMAP Scatter of Player Clusters

```python
feat_cols_player = ["ortg", "usage", "three_pt_rate", "rim_rate", "bpm", "obpm", "dbpm"]
pfeat = player_arch_df[feat_cols_player].fillna(player_arch_df[feat_cols_player].median())

# UMAP (better topology preservation than PCA for cluster visualization)
reducer = umap.UMAP(n_components=2, random_state=42, n_neighbors=30, min_dist=0.1)
embedding = reducer.fit_transform(StandardScaler().fit_transform(pfeat))
player_arch_df["u1"] = embedding[:, 0]
player_arch_df["u2"] = embedding[:, 1]

arch_labels = player_arch_df["archetype_label"].unique()
arch_palette = dict(zip(arch_labels, sns.color_palette("tab10", len(arch_labels))))

fig, ax = plt.subplots(figsize=(14, 9))
for label, grp in player_arch_df.groupby("archetype_label"):
    ax.scatter(grp["u1"], grp["u2"], label=label,
               color=arch_palette[label], alpha=0.3, s=15)

# Highlight targets
for name, pid in TARGET_PLAYERS.items():
    row = player_arch_df[player_arch_df.player_id == pid]
    if not row.empty:
        ax.scatter(row["u1"], row["u2"], color=PLAYER_COLORS[name],
                   s=250, zorder=10, edgecolors="black", linewidths=1.5)
        ax.annotate(name, (row["u1"].values[0], row["u2"].values[0]),
                    fontsize=11, fontweight="bold",
                    xytext=(8, 8), textcoords="offset points")

ax.set_title("Player Archetype Clusters — UMAP (2026 Season)", fontsize=13)
ax.legend(loc="upper left", fontsize=8, markerscale=2)
ax.set_xlabel("UMAP-1"); ax.set_ylabel("UMAP-2")

plt.tight_layout()
plt.savefig(FIG_DIR / "2b_player_cluster_umap.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Cell 2-4: VISUAL 2c — Archetype Centroid Heatmap

```python
centroid_player = player_arch_df.groupby("archetype_label")[feat_cols_player].mean()

fig, ax = plt.subplots(figsize=(14, 6))
sns.heatmap(
    centroid_player.T, annot=True, fmt=".1f", cmap="coolwarm",
    linewidths=0.5, ax=ax,
    cbar_kws={"label": "Mean value across archetype"}
)
ax.set_title("Player Archetype Centroids — Feature Heatmap", fontsize=13)

# Highlight columns for each target's archetype
for name, pid in TARGET_PLAYERS.items():
    row = player_arch_df[player_arch_df.player_id == pid]
    if not row.empty:
        arch = row["archetype_label"].values[0]
        col_idx = list(centroid_player.index).index(arch)
        ax.add_patch(plt.Rectangle((col_idx, 0), 1, len(feat_cols_player),
                     fill=False, edgecolor=PLAYER_COLORS[name], lw=2.5))

plt.tight_layout()
plt.savefig(FIG_DIR / "2c_player_centroid_heatmap.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Cell 2-5: VISUAL 2d — Peer Players Table (one per target)

```python
for name, pid in TARGET_PLAYERS.items():
    target_row = player_arch_df[player_arch_df.player_id == pid]
    if target_row.empty:
        continue
    arch = target_row["archetype_label"].values[0]
    peers = (
        player_arch_df[
            (player_arch_df.archetype_label == arch) &
            (player_arch_df.player_id != pid)
        ]
        .nsmallest(5, "distance_to_centroid")
        [["name", "archetype_label", "distance_to_centroid", "usage", "three_pt_rate", "bpm"]]
        .rename(columns={"name": "Player", "archetype_label": "Archetype",
                          "distance_to_centroid": "Dist. to Centroid"})
    )
    print(f"\n=== Peers: {name} ({arch}) ===")
    display(peers.style.format(precision=3))
```

---

## Section 3 — Scheme Fit (M3)

### Cell 3-1: Load Scheme Fit Data

```python
SCHEME_FIT_SQL = """
SELECT
    ptfs.player_id,
    ptfs.school_id,
    ptfs.season,
    ptfs.scheme_fit,
    -- Player style vector components (from source tables)
    pss.three_pt_rate     AS player_3pt,
    pss.rim_rate          AS player_rim,
    pss.usage             AS player_usage,
    het_p.off_assisted_pct AS player_assisted,
    pss.pace              AS player_pace,
    -- School style vector components
    tss.three_pt_rate     AS school_3pt,
    tss.rim_rate          AS school_rim,
    het_s.off_assisted_pct AS school_assisted,
    tss.pace              AS school_pace
FROM player_team_fit_scores ptfs
JOIN player_season_stats pss
    ON pss.player_id = ptfs.player_id AND pss.season = ptfs.season
LEFT JOIN hoop_explorer_player_stats het_p
    ON het_p.player_id = ptfs.player_id AND het_p.season = ptfs.season
JOIN team_season_stats tss
    ON tss.school_id = ptfs.school_id AND tss.season = ptfs.season
LEFT JOIN hoop_explorer_team_stats het_s
    ON het_s.school_id = ptfs.school_id AND het_s.season = ptfs.season
WHERE ptfs.school_id = :school_id
  AND ptfs.player_id = ANY(:player_ids)
  AND ptfs.season = :season
"""
scheme_df = pd.read_sql(
    text(SCHEME_FIT_SQL), engine,
    params={"school_id": SCHOOL_ID, "player_ids": list(TARGET_PLAYERS.values()), "season": SEASON}
)
```

### Cell 3-2: VISUAL 3a — 5-Dimension Style Vector Table

```python
# Build comparison table: Gonzaga target row + one row per player
# Columns: 3PT%, Rim%, Usage%, Assisted%, Pace, Scheme Fit score
# Delta row showing player − Gonzaga for each dimension

dims = ["3PT%", "Rim%", "Usage%", "Assisted%", "Pace"]

gonzaga_vals = {
    "3PT%": scheme_df["school_3pt"].mean(),
    "Rim%": scheme_df["school_rim"].mean(),
    "Usage%": None,                             # school-level usage not single-dim; skip or use avg
    "Assisted%": scheme_df["school_assisted"].mean(),
    "Pace": scheme_df["school_pace"].mean(),
}

table_rows = []
for name, pid in TARGET_PLAYERS.items():
    row = scheme_df[scheme_df.player_id == pid]
    if row.empty:
        continue
    r = row.iloc[0]
    table_rows.append({
        "Player": name,
        "3PT%": r["player_3pt"],
        "Rim%": r["player_rim"],
        "Usage%": r["player_usage"],
        "Assisted%": r["player_assisted"],
        "Pace": r["player_pace"],
        "Scheme Fit": r["scheme_fit"],
    })

style_table = pd.DataFrame(table_rows).set_index("Player")

# Add Gonzaga row
gonzaga_series = pd.Series({
    "3PT%": gonzaga_vals["3PT%"], "Rim%": gonzaga_vals["Rim%"],
    "Usage%": None, "Assisted%": gonzaga_vals["Assisted%"],
    "Pace": gonzaga_vals["Pace"], "Scheme Fit": "—"
}, name="Gonzaga Target")
display_table = pd.concat([pd.DataFrame([gonzaga_series]), style_table])

display(
    display_table.style
    .format(precision=3, na_rep="—")
    .highlight_max(subset=["Scheme Fit"], color="#c6efce")
    .highlight_min(subset=["Scheme Fit"], color="#ffc7ce")
)
```

### Cell 3-3: VISUAL 3b — Radar Chart

```python
from matplotlib.patches import FancyArrowPatch
import matplotlib.patheffects as pe

radar_dims = ["3PT%", "Rim%", "Assisted%", "Pace", "Scheme Fit (÷10)"]

def make_radar(ax, values, label, color, alpha=0.25):
    N = len(radar_dims)
    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    values_plot = values + [values[0]]
    angles += [angles[0]]
    ax.fill(angles, values_plot, color=color, alpha=alpha)
    ax.plot(angles, values_plot, color=color, linewidth=2, label=label)

fig, ax = plt.subplots(figsize=(8, 8), subplot_kw={"polar": True})

# Normalize each dimension 0–1 across all players + Gonzaga
# Plot Gonzaga, Ruffin, Crawford, Evans

ax.set_title("Style Vector Radar — Targets vs. Gonzaga System", pad=20, fontsize=13)
ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1))
plt.tight_layout()
plt.savefig(FIG_DIR / "3b_scheme_radar.png", dpi=150, bbox_inches="tight")
plt.show()
```

---

## Section 4 — Gap Matching

### Cell 4-1: Load Roster State and Gap Match Data

```python
# Roster state features: historical target mix + post-departure composition
ROSTER_STATE_SQL = """
SELECT
    rsf.school_id,
    rsf.season,
    rsf.snapshot_date,
    rsf.returning_minutes_pg, rsf.returning_minutes_sg,
    rsf.returning_minutes_sf, rsf.returning_minutes_pf, rsf.returning_minutes_c,
    rsf.departing_minutes_pg, rsf.departing_minutes_sg,
    rsf.departing_minutes_sf, rsf.departing_minutes_pf, rsf.departing_minutes_c,
    rsf.incoming_minutes_pg,  rsf.incoming_minutes_sg,
    rsf.incoming_minutes_sf,  rsf.incoming_minutes_pf,  rsf.incoming_minutes_c
FROM roster_state_features rsf
WHERE rsf.school_id = :school_id
ORDER BY rsf.season, rsf.snapshot_date
"""
roster_state = pd.read_sql(text(ROSTER_STATE_SQL), engine, params={"school_id": SCHOOL_ID})

# Gap match scores for target players
GAP_MATCH_SQL = """
SELECT player_id, gap_match, scheme_fit, overall_fit, role_fit
FROM player_team_fit_scores
WHERE school_id = :school_id
  AND player_id = ANY(:player_ids)
  AND season = :season
"""
gap_df = pd.read_sql(
    text(GAP_MATCH_SQL), engine,
    params={"school_id": SCHOOL_ID, "player_ids": list(TARGET_PLAYERS.values()), "season": SEASON}
)
```

### Cell 4-2: VISUAL 4a — Historical Minute Distribution by Position (stacked bar)

```python
# Last 3 seasons: returning minutes by position as stacked bar
# Shows what Gonzaga's "normal" composition looks like before departure

positions = ["PG", "SG", "SF", "PF", "C"]
pos_colors = {"PG": "#4e79a7", "SG": "#f28e2b", "SF": "#e15759",
              "PF": "#76b7b2", "C": "#59a14f"}

seasons_to_show = sorted(roster_state["season"].unique())[-3:]

fig, axes = plt.subplots(1, len(seasons_to_show), figsize=(14, 5), sharey=True)

for i, season in enumerate(seasons_to_show):
    row = roster_state[roster_state.season == season].iloc[-1]  # latest snapshot per season
    returning = {pos: row[f"returning_minutes_{pos.lower()}"] for pos in positions}
    departing = {pos: row[f"departing_minutes_{pos.lower()}"] for pos in positions}

    ax = axes[i]
    bottom = 0
    for pos in positions:
        ax.bar("Returning", returning[pos], bottom=bottom, color=pos_colors[pos],
               label=pos if i == 0 else "")
        bottom += returning.get(pos, 0) or 0

    ax.set_title(f"Season {season}", fontsize=11)
    ax.set_ylabel("Minutes (total roster)" if i == 0 else "")

axes[0].legend(title="Position", loc="upper right")
fig.suptitle("Gonzaga — Roster Minute Distribution by Position", fontsize=13, y=1.02)
plt.tight_layout()
plt.savefig(FIG_DIR / "4a_roster_history.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Cell 4-3: VISUAL 4b — Gap Vector (departing − incoming)

```python
# Net gap per position: departing_minutes − incoming_minutes
latest = roster_state[roster_state.season == SEASON].iloc[-1]

gap_by_pos = {
    pos: (latest[f"departing_minutes_{pos.lower()}"] or 0) -
         (latest[f"incoming_minutes_{pos.lower()}"] or 0)
    for pos in positions
}

fig, ax = plt.subplots(figsize=(8, 4))
bars = ax.bar(positions, [gap_by_pos[p] for p in positions],
              color=["#d62728" if gap_by_pos[p] > 0 else "#2ca02c" for p in positions])
ax.axhline(0, color="black", linewidth=0.8)
ax.set_title("Gonzaga Roster Gap — Net Minutes by Position (2026)", fontsize=13)
ax.set_ylabel("Departing − Incoming Minutes")
ax.set_xlabel("Position")

for bar, pos in zip(bars, positions):
    val = gap_by_pos[pos]
    ax.annotate(f"{val:.0f}", (bar.get_x() + bar.get_width() / 2, val),
                ha="center", va="bottom" if val > 0 else "top",
                fontsize=10, fontweight="bold")

plt.tight_layout()
plt.savefig(FIG_DIR / "4b_roster_gap_vector.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Cell 4-4: VISUAL 4c — Player Skill Profile vs. Gap Shape

```python
# For each target: overlay their skill profile on the gap shape
# Skills: usage, 3PT creation, rim creation, assist rate, rebound rate, defensive metrics
# Use normalized bars side-by-side: gap need (normalized 0-1) vs. player value (normalized 0-1)

SKILL_COLS = ["three_pt_rate", "rim_rate", "usage", "off_assisted_pct", "dbpm", "obpm"]
SKILL_LABELS = ["3PT Rate", "Rim Rate", "Usage", "Assisted%", "Def BPM", "Off BPM"]

# Pull player stats for each target
PLAYER_SKILLS_SQL = """
SELECT pss.player_id, pss.three_pt_rate, pss.rim_rate, pss.usage, pss.dbpm, pss.obpm,
       he.off_assisted_pct
FROM player_season_stats pss
LEFT JOIN hoop_explorer_player_stats he
    ON he.player_id = pss.player_id AND he.season = pss.season
WHERE pss.player_id = ANY(:player_ids) AND pss.season = :season
"""
player_skills = pd.read_sql(
    text(PLAYER_SKILLS_SQL), engine,
    params={"player_ids": list(TARGET_PLAYERS.values()), "season": SEASON}
)

# Gap "need" vector: normalize gap_by_pos to a skill-need profile
# (simple proxy: lead-guard/frontcourt gaps → weight assist/usage/rebounding skills higher)
gap_need = np.array([0.6, 0.3, 0.5, 0.7, 0.6, 0.4])  # manual for demo; replace with real roster gap vector

fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=True)

for i, (name, pid) in enumerate(TARGET_PLAYERS.items()):
    row = player_skills[player_skills.player_id == pid]
    if row.empty:
        continue
    r = row.iloc[0]
    player_vals_raw = [r[c] if c in r.index else 0 for c in SKILL_COLS[:5]] + [r.get("obpm", 0)]
    # Normalize to 0-1 across the player population (use quantile)
    player_norm = np.array(player_vals_raw) / (np.array(player_vals_raw).max() + 1e-6)

    x = np.arange(len(SKILL_LABELS))
    w = 0.35
    ax = axes[i]
    ax.bar(x - w/2, gap_need, w, label="Roster Need", color="#4e79a7", alpha=0.7)
    ax.bar(x + w/2, player_norm, w, label="Player Profile", color=PLAYER_COLORS[name], alpha=0.8)

    ax.set_title(f"{name} (Gap Match: {gap_df[gap_df.player_id==pid]['gap_match'].values[0]:.1f})",
                 fontsize=11)
    ax.set_xticks(x); ax.set_xticklabels(SKILL_LABELS, rotation=30, ha="right")
    if i == 0:
        ax.legend()
    ax.set_ylim(0, 1.1)

fig.suptitle("Roster Need vs. Player Skill Profile", fontsize=13, y=1.02)
plt.tight_layout()
plt.savefig(FIG_DIR / "4c_gap_profile_comparison.png", dpi=150, bbox_inches="tight")
plt.show()
```

---

## Section 5 — Player Projection (Ph 0 → Ph 2a)

### Cell 5-1: Load Projection Data (Phase 0 and Phase 2a)

```python
PROJECTION_SQL = """
SELECT
    pp.player_id,
    pp.season,
    pp.model_version,
    pp.projected_off_rapm,
    pp.projected_def_rapm,
    pp.skill_states,          -- JSONB: per-skill shrunk/smoothed estimates
    pp.uncertainty,           -- JSONB: per-skill posterior variance
    pp.projected_rates,       -- JSONB: per-skill per-40 rates
    pp.projected_box_score    -- JSONB: PPG, APG, RPG etc
FROM player_projections pp
WHERE pp.player_id = ANY(:player_ids)
  AND pp.season = :season
  AND pp.school_id IS NULL   -- neutral mode only
ORDER BY pp.player_id, pp.model_version
"""
proj_df = pd.read_sql(
    text(PROJECTION_SQL), engine,
    params={"player_ids": list(TARGET_PLAYERS.values()), "season": SEASON}
)
import json
for col in ["skill_states", "uncertainty", "projected_rates", "projected_box_score"]:
    proj_df[col] = proj_df[col].apply(lambda x: json.loads(x) if isinstance(x, str) else x)

ph0 = proj_df[proj_df.model_version == "player-projection-shrinkage-v1"]
ph2 = proj_df[proj_df.model_version == "player-projection-phase2a-v1"]

# Also pull actual last-season stats for comparison
ACTUAL_SQL = """
SELECT player_id, ortg, usage AS usage_rate, three_pt_rate AS three_pt_pct,
       rim_rate, bpm, obpm, dbpm, games, min_pct
FROM player_season_stats
WHERE player_id = ANY(:player_ids) AND season = :season
"""
actual_df = pd.read_sql(
    text(ACTUAL_SQL), engine,
    params={"player_ids": list(TARGET_PLAYERS.values()), "season": SEASON - 1}
)
```

### Cell 5-2: VISUAL 5a — Per-Skill Comparison: Actual vs Ph0 vs Ph2a

```python
SKILLS = ["three_pt_rate", "rim_rate", "usage_rate", "assist_rate",
          "rebound_rate", "steal_rate", "block_rate", "shooting_touch"]
SKILL_LABELS_5 = ["3PT Rate", "Rim Rate", "Usage", "Assist Rate",
                   "Reb Rate", "Steal Rate", "Block Rate", "eFG%"]

fig, axes = plt.subplots(len(TARGET_PLAYERS), 1, figsize=(14, 4 * len(TARGET_PLAYERS)))

for i, (name, pid) in enumerate(TARGET_PLAYERS.items()):
    ax = axes[i]

    actual_row = actual_df[actual_df.player_id == pid]
    ph0_row = ph0[ph0.player_id == pid]
    ph2_row = ph2[ph2.player_id == pid]

    x = np.arange(len(SKILLS))
    w = 0.25

    # Pull values from JSON fields or stat columns
    def get_skill_vals(row, source):
        if source == "actual":
            return [actual_row.get(s, pd.Series([0])).values[0] for s in SKILLS]
        else:
            rates = row["projected_rates"].values[0] if not row.empty else {}
            return [rates.get(s, 0) for s in SKILLS]

    actual_vals = get_skill_vals(actual_row, "actual")
    ph0_vals = get_skill_vals(ph0_row, "ph0")
    ph2_vals = get_skill_vals(ph2_row, "ph2")

    ax.bar(x - w, actual_vals, w, label="Actual (prev season)", color="#aec7e8", alpha=0.9)
    ax.bar(x,     ph0_vals,   w, label="Phase 0 (shrinkage)", color="#1f77b4", alpha=0.9)
    ax.bar(x + w, ph2_vals,   w, label="Phase 2a (Kalman)",   color="#ff7f0e", alpha=0.9)

    ax.set_title(f"{name} — Actual vs. Projected Skills", fontsize=12)
    ax.set_xticks(x); ax.set_xticklabels(SKILL_LABELS_5, rotation=30, ha="right")
    if i == 0:
        ax.legend()

plt.tight_layout()
plt.savefig(FIG_DIR / "5a_skill_comparison.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Cell 5-3: VISUAL 5b — Shrinkage Weight Chart

```python
# Show games_played × min_pct (shrinkage weight) for each player
# Alongside: how much did their estimates move from raw to shrunk?
# (raw − shrunk) magnitude as a second bar

SHRINKAGE_SQL = """
SELECT pss.player_id, pss.games, pss.min_pct, pss.games * pss.min_pct AS shrink_weight
FROM player_season_stats pss
WHERE pss.player_id = ANY(:player_ids) AND pss.season = :season
"""
shrink_df = pd.read_sql(
    text(SHRINKAGE_SQL), engine,
    params={"player_ids": list(TARGET_PLAYERS.values()), "season": SEASON - 1}
)

fig, ax = plt.subplots(figsize=(8, 4))
names = list(TARGET_PLAYERS.keys())
weights = [shrink_df[shrink_df.player_id == pid]["shrink_weight"].values[0]
           if pid in shrink_df.player_id.values else 0
           for name, pid in TARGET_PLAYERS.items()]

bars = ax.bar(names, weights, color=[PLAYER_COLORS[n] for n in names])
ax.set_title("Shrinkage Weight (games × min_pct) — Higher = Less Shrinkage", fontsize=12)
ax.set_ylabel("games × min_pct")

for bar, w in zip(bars, weights):
    ax.annotate(f"{w:.2f}", (bar.get_x() + bar.get_width() / 2, w),
                ha="center", va="bottom", fontsize=11, fontweight="bold")

plt.tight_layout()
plt.savefig(FIG_DIR / "5b_shrinkage_weights.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Cell 5-4: VISUAL 5c — Phase 2a Cross-Season Trajectory

```python
# For each target player: line chart of a key skill (e.g., 3PT rate) across seasons 2021-2026
# Show: actual observed rate vs. Phase 0 estimate vs. Phase 2a smoothed estimate + uncertainty band

CROSS_SEASON_SQL = """
SELECT pp.player_id, pp.season, pp.model_version,
       pp.projected_off_rapm, pp.projected_def_rapm,
       pp.skill_states, pp.uncertainty
FROM player_projections pp
WHERE pp.player_id = ANY(:player_ids)
  AND pp.school_id IS NULL
ORDER BY pp.player_id, pp.model_version, pp.season
"""
cross_df = pd.read_sql(
    text(CROSS_SEASON_SQL), engine,
    params={"player_ids": list(TARGET_PLAYERS.values())}
)

ACTUAL_ALL_SEASONS_SQL = """
SELECT player_id, season, three_pt_rate, usage, bpm, games, min_pct
FROM player_season_stats
WHERE player_id = ANY(:player_ids)
ORDER BY player_id, season
"""
actual_all = pd.read_sql(
    text(ACTUAL_ALL_SEASONS_SQL), engine,
    params={"player_ids": list(TARGET_PLAYERS.values())}
)

fig, axes = plt.subplots(1, len(TARGET_PLAYERS), figsize=(16, 5))

for i, (name, pid) in enumerate(TARGET_PLAYERS.items()):
    ax = axes[i]

    actual_p = actual_all[actual_all.player_id == pid].sort_values("season")
    ph0_p = cross_df[(cross_df.player_id == pid) &
                     (cross_df.model_version == "player-projection-shrinkage-v1")].sort_values("season")
    ph2_p = cross_df[(cross_df.player_id == pid) &
                     (cross_df.model_version == "player-projection-phase2a-v1")].sort_values("season")

    ax.plot(actual_p["season"], actual_p["three_pt_rate"], "o--",
            color="#aec7e8", label="Actual 3PT Rate", linewidth=1.5)
    ax.plot(ph0_p["season"], ph0_p["projected_off_rapm"], "s-",
            color="#1f77b4", label="Ph0 Off RAPM", linewidth=2)
    ax.plot(ph2_p["season"], ph2_p["projected_off_rapm"], "^-",
            color="#ff7f0e", label="Ph2a Off RAPM", linewidth=2)

    # Uncertainty band from Phase 2a (use projected_def/off RAPM ± std from uncertainty JSON)
    # If uncertainty JSON has a "combined_std" field, shade it

    ax.set_title(f"{name}", fontsize=12)
    ax.set_xlabel("Season")
    if i == 0:
        ax.set_ylabel("Value")
        ax.legend(fontsize=8)

fig.suptitle("Cross-Season Trajectory: Actual vs. Phase 0 vs. Phase 2a", fontsize=13, y=1.02)
plt.tight_layout()
plt.savefig(FIG_DIR / "5c_cross_season_trajectory.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Cell 5-5: VISUAL 5d — Phase 0 vs. Phase 2a Scatter (all players)

```python
merged = ph0.merge(ph2, on="player_id", suffixes=("_ph0", "_ph2"))

fig, ax = plt.subplots(figsize=(8, 7))
ax.scatter(merged["projected_off_rapm_ph0"], merged["projected_off_rapm_ph2"],
           alpha=0.2, s=10, color="#aec7e8", label="All players")
ax.plot([merged["projected_off_rapm_ph0"].min(), merged["projected_off_rapm_ph0"].max()],
        [merged["projected_off_rapm_ph0"].min(), merged["projected_off_rapm_ph0"].max()],
        "k--", alpha=0.5, label="Ph0 = Ph2a")

for name, pid in TARGET_PLAYERS.items():
    r = merged[merged.player_id == pid]
    if not r.empty:
        ax.scatter(r["projected_off_rapm_ph0"], r["projected_off_rapm_ph2"],
                   color=PLAYER_COLORS[name], s=200, zorder=10,
                   edgecolors="black", linewidths=1.5, label=name)
        ax.annotate(name, (r["projected_off_rapm_ph0"].values[0], r["projected_off_rapm_ph2"].values[0]),
                    xytext=(6, 4), textcoords="offset points", fontsize=10, fontweight="bold")

ax.set_xlabel("Phase 0 Projected Off RAPM")
ax.set_ylabel("Phase 2a Projected Off RAPM")
ax.set_title("Phase 0 vs. Phase 2a — Offense (all players, 2026)", fontsize=13)
ax.legend()
plt.tight_layout()
plt.savefig(FIG_DIR / "5d_ph0_vs_ph2a_scatter.png", dpi=150, bbox_inches="tight")
plt.show()
```

---

## Section 6 — Playing Time (M4)

### Cell 6-1: Load Playing Time Projections

```python
PT_SQL = """
SELECT
    ptp.player_id,
    ptp.school_id,
    ptp.season,
    ptp.projected_minutes,
    ptp.projected_usage,
    ptp.minutes_ci_lower,
    ptp.minutes_ci_upper,
    ptp.role_fit_score
FROM playing_time_projections ptp
WHERE ptp.school_id = :school_id
  AND ptp.player_id = ANY(:player_ids)
  AND ptp.season = :season
"""
pt_df = pd.read_sql(
    text(PT_SQL), engine,
    params={"school_id": SCHOOL_ID, "player_ids": list(TARGET_PLAYERS.values()), "season": SEASON}
)
```

### Cell 6-2: VISUAL 6a — Minutes Projection with 90% CI

```python
fig, ax = plt.subplots(figsize=(9, 5))

y_pos = np.arange(len(TARGET_PLAYERS))
names = list(TARGET_PLAYERS.keys())
pids = list(TARGET_PLAYERS.values())

proj_mins = []
ci_lower = []
ci_upper = []

for pid in pids:
    row = pt_df[pt_df.player_id == pid]
    if not row.empty:
        proj_mins.append(row["projected_minutes"].values[0])
        ci_lower.append(row["minutes_ci_lower"].values[0])
        ci_upper.append(row["minutes_ci_upper"].values[0])
    else:
        proj_mins.append(0); ci_lower.append(0); ci_upper.append(0)

xerr_low  = [m - l for m, l in zip(proj_mins, ci_lower)]
xerr_high = [u - m for m, u in zip(proj_mins, ci_upper)]

ax.barh(y_pos, proj_mins,
        xerr=[xerr_low, xerr_high],
        color=[PLAYER_COLORS[n] for n in names],
        capsize=8, error_kw={"elinewidth": 2, "capthick": 2},
        alpha=0.85, height=0.5)

for i, (m, l, u) in enumerate(zip(proj_mins, ci_lower, ci_upper)):
    ax.annotate(f"{m:.1f} min/g\n(CI: {l:.1f}–{u:.1f})",
                (m + 1, y_pos[i]), va="center", fontsize=10)

ax.set_yticks(y_pos); ax.set_yticklabels(names, fontsize=12)
ax.set_xlabel("Projected Minutes per Game (90% CI)", fontsize=11)
ax.set_title("Playing Time Projection @ Gonzaga — with Confidence Intervals", fontsize=13)
ax.axvline(x=40, color="gray", linestyle="--", alpha=0.4, label="Full game")

plt.tight_layout()
plt.savefig(FIG_DIR / "6a_playing_time_ci.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Cell 6-3: VISUAL 6b — Minutes vs. Usage Rate (paired)

```python
fig, axes = plt.subplots(1, 2, figsize=(12, 4))

for ax, metric, label in [
    (axes[0], "projected_minutes", "Minutes/Game"),
    (axes[1], "projected_usage", "Usage Rate (%)")
]:
    vals = [pt_df[pt_df.player_id == pid][metric].values[0]
            if pid in pt_df.player_id.values else 0
            for pid in pids]
    bars = ax.bar(names, vals, color=[PLAYER_COLORS[n] for n in names], alpha=0.85)
    ax.set_title(f"Projected {label} @ Gonzaga", fontsize=11)
    ax.set_ylabel(label)
    for bar, v in zip(bars, vals):
        ax.annotate(f"{v:.1f}", (bar.get_x() + bar.get_width()/2, v),
                    ha="center", va="bottom", fontsize=11, fontweight="bold")

plt.tight_layout()
plt.savefig(FIG_DIR / "6b_minutes_usage_paired.png", dpi=150, bbox_inches="tight")
plt.show()
```

---

## Section 7 — Destination Projection

### Cell 7-1: Load Destination Projection Data

```python
DEST_SQL = """
SELECT
    pp.player_id,
    pp.season,
    pp.model_version,
    pp.projected_off_rapm,
    pp.projected_def_rapm,
    pp.projected_minutes,
    pp.projected_usage,
    pp.projected_box_score,
    pp.uncertainty_components    -- JSONB: delta breakdown (d1_role, d2_style, d3_roster, d4_tier)
FROM player_projections pp
WHERE pp.school_id = :school_id
  AND pp.player_id = ANY(:player_ids)
  AND pp.season = :season
  AND pp.model_version = 'player-destination-proj-v1'
"""
dest_df = pd.read_sql(
    text(DEST_SQL), engine,
    params={"school_id": SCHOOL_ID, "player_ids": list(TARGET_PLAYERS.values()), "season": SEASON}
)
for col in ["projected_box_score", "uncertainty_components"]:
    dest_df[col] = dest_df[col].apply(lambda x: json.loads(x) if isinstance(x, str) else (x or {}))
```

### Cell 7-2: VISUAL 7a — Waterfall Chart: Neutral → Destination (per player)

```python
# For each player: waterfall chart showing neutral RAPM as baseline,
# each delta as a step, destination RAPM as final bar

def waterfall_chart(ax, baseline, deltas, delta_labels, final, color, title):
    steps = [baseline] + [d for d in deltas]
    cumulative = np.cumsum(steps)
    starts = [0] + list(cumulative[:-1])

    bar_colors = [color if d >= 0 else "#d62728" for d in steps]
    bar_colors[0] = "#4e79a7"  # baseline always blue

    for i, (start, step, label) in enumerate(zip(starts, steps, ["Neutral"] + delta_labels)):
        ax.bar(i, step, bottom=start, color=bar_colors[i], alpha=0.85,
               edgecolor="black", linewidth=0.7)
        ax.annotate(
            f"{'+' if step >= 0 else ''}{step:.2f}",
            (i, start + step), ha="center",
            va="bottom" if step >= 0 else "top",
            fontsize=9, fontweight="bold"
        )

    ax.bar(len(steps), final, color="#59a14f", alpha=0.85,
           edgecolor="black", linewidth=1.2)
    ax.annotate(f"Dest: {final:.2f}", (len(steps), final),
                ha="center", va="bottom", fontsize=10, fontweight="bold")

    ax.set_xticks(range(len(steps) + 1))
    ax.set_xticklabels(["Neutral"] + delta_labels + ["Destination"], rotation=20, ha="right")
    ax.axhline(0, color="black", linewidth=0.7)
    ax.set_title(title, fontsize=11)
    ax.set_ylabel("Off RAPM")

fig, axes = plt.subplots(1, len(TARGET_PLAYERS), figsize=(16, 6), sharey=True)

DELTA_LABELS = ["Δ1 Role/Usage", "Δ2 Style Fit", "Δ3 Roster Ctx", "Δ4 Tier"]

# Fallback demo deltas only — prefer real explanation JSON values from destination rows
DELTAS = {
    "Ruffin":   [-0.75, -0.06, 0.15, -0.60],
    "Crawford": [-0.17,  0.04, 0.07, -0.20],
    "Evans":    [-0.74,  0.07, 0.14, -0.20],
}

for i, (name, pid) in enumerate(TARGET_PLAYERS.items()):
    neutral_row = ph0[ph0.player_id == pid]
    dest_row    = dest_df[dest_df.player_id == pid]
    if neutral_row.empty or dest_row.empty:
        continue

    neutral_rapm = neutral_row["projected_off_rapm"].values[0]
    dest_rapm    = dest_row["projected_off_rapm"].values[0]

    # Pull real deltas from uncertainty_components if available
    unc = dest_row["uncertainty_components"].values[0]
    deltas = [
        unc.get("d1_role_usage_delta", DELTAS[name][0]),
        unc.get("d2_style_fit_delta",  DELTAS[name][1]),
        unc.get("d3_roster_ctx_delta", DELTAS[name][2]),
        unc.get("d4_tier_delta",       DELTAS[name][3]),
    ]

    waterfall_chart(axes[i], neutral_rapm, deltas, DELTA_LABELS, dest_rapm,
                    PLAYER_COLORS[name], f"{name}")

fig.suptitle("Neutral → Destination Off RAPM: Step-by-Step Delta", fontsize=13, y=1.02)
plt.tight_layout()
plt.savefig(FIG_DIR / "7a_destination_waterfall.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Cell 7-3: VISUAL 7b — Final Comparison Table

```python
summary_rows = []
for name, pid in TARGET_PLAYERS.items():
    arch_row    = player_arch_df[player_arch_df.player_id == pid]
    gap_row     = gap_df[gap_df.player_id == pid]
    pt_row      = pt_df[pt_df.player_id == pid]
    dest_row    = dest_df[dest_df.player_id == pid]
    neutral_row = ph0[ph0.player_id == pid]

    box = dest_row["projected_box_score"].values[0] if not dest_row.empty else {}
    summary_rows.append({
        "Player": name,
        "Archetype":       arch_row["archetype_label"].values[0] if not arch_row.empty else "—",
        "Scheme Fit":      gap_df[gap_df.player_id == pid]["scheme_fit"].values[0] if not gap_row.empty else "—",
        "Gap Match":       gap_row["gap_match"].values[0] if not gap_row.empty else "—",
        "Role Fit":        pt_row["role_fit_score"].values[0] if not pt_row.empty else "—",
        "Composite Fit":   gap_row["overall_fit"].values[0] if not gap_row.empty else "—",
        "Neutral Off RAPM": neutral_row["projected_off_rapm"].values[0] if not neutral_row.empty else "—",
        "Dest Off RAPM":   dest_row["projected_off_rapm"].values[0] if not dest_row.empty else "—",
        "System Δ":        (dest_row["projected_off_rapm"].values[0] - neutral_row["projected_off_rapm"].values[0])
                           if not dest_row.empty and not neutral_row.empty else "—",
        "Proj Min (CI)":   f"{pt_row['projected_minutes'].values[0]:.1f} ({pt_row['minutes_ci_lower'].values[0]:.0f}–{pt_row['minutes_ci_upper'].values[0]:.0f})"
                           if not pt_row.empty else "—",
        "Proj PPG":        box.get("points_per_game", "—"),
        "Proj APG":        box.get("assists_per_game", "—"),
        "Proj RPG":        box.get("rebounds_per_game", "—"),
    })

summary_df = pd.DataFrame(summary_rows).set_index("Player")

display(
    summary_df.style
    .format(precision=1, na_rep="—")
    .highlight_max(subset=["Composite Fit", "Dest Off RAPM", "Proj PPG"], color="#c6efce")
    .highlight_min(subset=["Composite Fit", "Dest Off RAPM"], color="#ffc7ce")
    .background_gradient(subset=["System Δ"], cmap="RdYlGn", vmin=-2, vmax=2)
)
```

---

## Section 8 — News Monitoring Agent (Step 0)

Demonstrates how new portal entrants dynamically enter the recommendation engine's candidate pool without a manual re-run. Sources all output from `news_monitor_agent_v2.ipynb` — no new DB queries needed; display agent run output and classifier comparison.

### Cell 8-1: VISUAL 0a — Two-Path Architecture Diagram

```python
# Architecture diagram — rendered as matplotlib figure (no external deps)
# Two branches converging on player_team_fit_scores.is_portal_candidate = True

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch

fig, ax = plt.subplots(figsize=(14, 6))
ax.set_xlim(0, 14); ax.set_ylim(0, 6); ax.axis("off")

# Branch 1: 247Sports deterministic ETL (left)
boxes_left = [
    (1.0, 4.5, "247Sports\n(structured JSON)"),
    (1.0, 3.0, "ingest_transfers\n_247sports.py"),
    (1.0, 1.5, "sync_portal\n_candidate_flags()"),
]
# Branch 2: News Agent (right)
boxes_right = [
    (7.5, 4.5, "ESPN / On3 /\n247Sports news"),
    (7.5, 3.0, "search_news\n(Tavily)"),
    (9.5, 3.0, "classify_events\n_batch_llm\n(Gemini)"),
    (11.5, 3.0, "transfer_player\ntool"),
]
# Converge
boxes_center = [(6.5, 0.6, "player_team_fit_scores\nis_portal_candidate = True\n→ recommendation engine")]

label_style = dict(ha="center", va="center", fontsize=9, fontweight="bold")
box_style   = dict(boxstyle="round,pad=0.4", facecolor="#e8f4f8", edgecolor="#4e79a7", linewidth=1.5)
box_agent   = dict(boxstyle="round,pad=0.4", facecolor="#fff7e6", edgecolor="#ff7f0e", linewidth=1.5)
box_db      = dict(boxstyle="round,pad=0.5", facecolor="#c6efce", edgecolor="#2ca02c", linewidth=2)

for x, y, label in boxes_left:
    ax.text(x + 0.7, y, label, **label_style, bbox=box_style)
for x, y, label in boxes_right:
    ax.text(x + 0.7, y, label, **label_style, bbox=box_agent)
for x, y, label in boxes_center:
    ax.text(x + 0.7, y, label, **label_style, bbox=box_db)

# Arrows (simplified — add real arrowprops for final version)
ax.annotate("", xy=(1.7, 3.5), xytext=(1.7, 4.2),
            arrowprops=dict(arrowstyle="->", color="#4e79a7", lw=1.5))
ax.annotate("", xy=(1.7, 2.0), xytext=(1.7, 2.8),
            arrowprops=dict(arrowstyle="->", color="#4e79a7", lw=1.5))
ax.annotate("", xy=(6.8, 1.2), xytext=(2.4, 1.5),
            arrowprops=dict(arrowstyle="->", color="#4e79a7", lw=1.5))

ax.annotate("", xy=(8.8, 3.0), xytext=(8.2, 4.2),
            arrowprops=dict(arrowstyle="->", color="#ff7f0e", lw=1.5))
ax.annotate("", xy=(10.2, 3.0), xytext=(9.2, 3.0),
            arrowprops=dict(arrowstyle="->", color="#ff7f0e", lw=1.5))
ax.annotate("", xy=(12.2, 3.0), xytext=(11.2, 3.0),
            arrowprops=dict(arrowstyle="->", color="#ff7f0e", lw=1.5))
ax.annotate("", xy=(7.2, 1.2), xytext=(12.2, 2.7),
            arrowprops=dict(arrowstyle="->", color="#ff7f0e", lw=1.5))

# Labels
ax.text(1.7, 5.5, "Path 1: Deterministic ETL", ha="center", fontsize=10,
        fontweight="bold", color="#4e79a7")
ax.text(10.2, 5.5, "Path 2: LangGraph News Agent", ha="center", fontsize=10,
        fontweight="bold", color="#ff7f0e")

ax.set_title("PortalPoint — Two Paths to is_portal_candidate = True", fontsize=13, pad=12)
plt.tight_layout()
plt.savefig(FIG_DIR / "0a_news_agent_architecture.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Cell 8-2: VISUAL 0b — Classifier Comparison Table

Display the regex vs. LLM comparison from `news_monitor_agent_v2.ipynb` Cell 5c as a styled DataFrame for the presentation.

```python
# Reproduce the 5-article classifier comparison as a clean display table
# (copy the COMPARISON_ARTICLES list and both classifier results from the agent notebook)

comparison_data = [
    {
        "Article": "Duke PG enters NCAA transfer portal",
        "Regex": "player_enters_portal", "Regex Conf": 0.85,
        "LLM": "player_enters_portal", "LLM Conf": 1.00,
        "Agreement": "✅",
    },
    {
        "Article": "Star forward departs program amid roster changes",
        "Regex": "player_enters_portal", "Regex Conf": 0.85,
        "LLM": "player_enters_portal", "LLM Conf": 1.00,
        "Agreement": "✅",
    },
    {
        "Article": "Arizona HC stepping down after 10 seasons",
        "Regex": "coach_leaves", "Regex Conf": 0.85,
        "LLM": "coach_leaves", "LLM Conf": 1.00,
        "Agreement": "✅",
    },
    {
        "Article": "Top recruit commits to Florida for 2026",
        "Regex": "unknown", "Regex Conf": 0.00,
        "LLM": "unknown", "LLM Conf": 1.00,
        "Agreement": "✅",
    },
    {
        "Article": "UNC guard weighing options after disappointing season",
        "Regex": "player_enters_portal", "Regex Conf": 0.85,
        "LLM": "unknown", "LLM Conf": 0.80,
        "Agreement": "⚠️ LLM correct\n(not confirmed)",
    },
]

compare_df = pd.DataFrame(comparison_data).set_index("Article")

def highlight_disagreement(row):
    color = "background-color: #ffc7ce" if row["Agreement"].startswith("⚠️") else ""
    return [color] * len(row)

display(
    compare_df.style
    .apply(highlight_disagreement, axis=1)
    .set_caption("Regex vs. LLM Classifier — 5-Article Test Set")
    .format({"Regex Conf": "{:.2f}", "LLM Conf": "{:.2f}"})
)
```

### Cell 8-3: VISUAL 0c — Simulated Agent Run Output

Display a representative `transfer_player` tool result to show what a successful DB update looks like.

```python
# Show a representative transfer_player result (use real output from agent notebook run,
# or construct from the JSON schema for a demo player)

import json

sample_result = {
    "success": True,
    "player_id": 1042,
    "matched_name": "Jalen Moore",
    "queried_name": "Jalen Moore",
    "match_confidence": 0.95,
    "from_school": "Loyola Marymount",
    "from_school_id": 88,
    "portal_entry_date": "2026-07-03",
    "fit_score_rows_updated": 365,
    "message": (
        "Jalen Moore (confidence=0.95) marked is_portal_candidate=True — "
        "now visible in the recommendation engine for 365 school(s)."
    ),
}

print("transfer_player tool result (sample):\n")
print(json.dumps(sample_result, indent=2))

# Annotated display
print("\n" + "─" * 60)
print(f"  Player matched:    {sample_result['matched_name']} (id={sample_result['player_id']})")
print(f"  Match confidence:  {sample_result['match_confidence']:.0%}")
print(f"  Portal date:       {sample_result['portal_entry_date']}")
print(f"  Rows updated:      {sample_result['fit_score_rows_updated']} school pairings in player_team_fit_scores")
print(f"  Effect:            Player now surfaces in /api/players/search?available_only=true")
```

---

## Execution Notes

1. **Run order:** Sections execute independently except Section 7 depends on Section 5 (needs `ph0` DataFrame). Run all cells top-to-bottom for a clean state.

2. **Real player IDs:** Run Cell 0-1 first, update `TARGET_PLAYERS` dict with real `player_id` values from the DB before proceeding.

3. **MLflow artifact fallback:** If `k_selection_data.json` was not logged during the clustering runs (older runs), the K-selection visuals (1-2, 2-2) will need a re-run with artifact logging added, or can be reconstructed by loading the saved sklearn model and computing silhouette scores against the stored cluster labels.

4. **UMAP install:** `pip install umap-learn`. Alternatively swap with `sklearn.manifold.TSNE` if umap-learn is not available.

5. **Hardcoded delta fallback (Section 7):** Waterfall uses `uncertainty_components` JSON if the field is populated; falls back to the `DELTAS` dict if not. Verify `uncertainty_components` is written by `destination_projection.py` before relying on live values.

6. **Figure output:** All figures saved to `docs/presentation/figures/`. Named by section and visual index (e.g., `1b_team_cluster_scatter.png`) for easy insertion into slides.

7. **Style:** Set a consistent matplotlib style at the top:
   ```python
   plt.style.use("seaborn-v0_8-whitegrid")
   plt.rcParams.update({"font.size": 11, "axes.titlesize": 13, "figure.dpi": 150})
   ```
