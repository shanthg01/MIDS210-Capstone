"""
SQLAlchemy ORM models — 5-layer schema matching diagram_4_database_architecture.md.

Partitioning:
  player_season_stats and team_season_stats are partitioned by season in production.
  PostgreSQL RANGE partitioning must be applied via Alembic migration — not expressible
  in the ORM. The ORM models are correct; add the DDL partition clause in the migration.

pg_vector:
  style_vector columns use ARRAY(Float) as a placeholder. When the pgvector extension
  is enabled, replace with Vector(5) from pgvector.sqlalchemy and add an ivfflat index.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from portalpoint.db.base import Base

# ---------------------------------------------------------------------------
# Layer 1 — Core
# ---------------------------------------------------------------------------


class School(Base):
    __tablename__ = "schools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    conference: Mapped[str] = mapped_column(String(100), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    region: Mapped[str] = mapped_column(String(50), nullable=False)
    enrollment: Mapped[Optional[int]] = mapped_column(Integer)
    setting: Mapped[Optional[str]] = mapped_column(String(20))  # urban/suburban/rural
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    majors_offered: Mapped[Optional[list]] = mapped_column(ARRAY(String))
    graduation_rate: Mapped[Optional[float]] = mapped_column(Float)
    nil_tier: Mapped[Optional[str]] = mapped_column(String(10))  # high/medium/low
    nil_estimated_budget_usd: Mapped[Optional[float]] = mapped_column(Float)
    espn_id: Mapped[Optional[str]] = mapped_column(String(50), unique=True)
    barttorvik_id: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    coaches: Mapped[list[Coach]] = relationship(back_populates="school")
    team_season_stats: Mapped[list[TeamSeasonStats]] = relationship(back_populates="school")
    team_system_profiles: Mapped[list[TeamSystemProfile]] = relationship(back_populates="school")


class Coach(Base):
    __tablename__ = "coaches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # head/assistant
    tenure_start: Mapped[Optional[date]] = mapped_column(Date)
    twitter_handle: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    school: Mapped[School] = relationship(back_populates="coaches")
    coaching_tendencies: Mapped[list[CoachingTendency]] = relationship(back_populates="coach")


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (
        # Trigram GIN index for fuzzy search — created via Alembic after enabling pg_trgm extension
        # Index("ix_players_full_name_trgm", "full_name", postgresql_using="gin",
        #       postgresql_ops={"full_name": "gin_trgm_ops"}),
        Index("ix_players_full_name", "full_name"),
    )

    # BigInteger, not Integer — id is hash(barttorvik_id) masked to 63 bits
    # (see db/player_ids.py), doesn't fit a 32-bit Integer.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    position: Mapped[str] = mapped_column(String(2), nullable=False)  # PG/SG/SF/PF/C
    height_inches: Mapped[Optional[int]] = mapped_column(SmallInteger)
    class_year: Mapped[str] = mapped_column(String(20), nullable=False)
    hometown: Mapped[Optional[str]] = mapped_column(String(200))
    twitter_handle: Mapped[Optional[str]] = mapped_column(String(100))
    social_followers: Mapped[Optional[int]] = mapped_column(Integer)
    espn_id: Mapped[Optional[str]] = mapped_column(String(50), unique=True)
    barttorvik_id: Mapped[Optional[str]] = mapped_column(String(50), unique=True)
    cbbpy_id: Mapped[Optional[str]] = mapped_column(String(50))
    verbalcommits_id: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    player_school_seasons: Mapped[list[PlayerSchoolSeason]] = relationship(back_populates="player")
    season_stats: Mapped[list[PlayerSeasonStats]] = relationship(back_populates="player")
    archetypes: Mapped[list[PlayerArchetype]] = relationship(back_populates="player")
    transfers: Mapped[list[Transfer]] = relationship(foreign_keys="Transfer.player_id", back_populates="player")


class PlayerSchoolSeason(Base):
    __tablename__ = "player_school_seasons"
    __table_args__ = (
        UniqueConstraint("player_id", "school_id", "season", name="uq_player_school_season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # year season ends
    jersey_number: Mapped[Optional[str]] = mapped_column(String(3))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    player: Mapped[Player] = relationship(back_populates="player_school_seasons")
    school: Mapped[School] = relationship()


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        Index("ix_games_date_season", "game_date", "season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    espn_game_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    home_school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    away_school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    home_score: Mapped[Optional[int]] = mapped_column(SmallInteger)
    away_score: Mapped[Optional[int]] = mapped_column(SmallInteger)
    neutral_site: Mapped[bool] = mapped_column(Boolean, default=False)
    conference_game: Mapped[bool] = mapped_column(Boolean, default=False)
    arena: Mapped[Optional[str]] = mapped_column(String(200))
    attendance: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    home_school: Mapped[School] = relationship(foreign_keys=[home_school_id])
    away_school: Mapped[School] = relationship(foreign_keys=[away_school_id])


# ---------------------------------------------------------------------------
# Layer 2 — Analytics
# ---------------------------------------------------------------------------


class PlayerSeasonStats(Base):
    """Partitioned by season in production (RANGE partitioning via Alembic migration)."""

    __tablename__ = "player_season_stats"
    __table_args__ = (
        UniqueConstraint("player_id", "school_id", "season", name="uq_player_season_stats"),
        Index("ix_player_season_stats_player_season", "player_id", "season"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Traditional
    games_played: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    minutes_per_game: Mapped[float] = mapped_column(Float, nullable=False)
    min_pct: Mapped[Optional[float]] = mapped_column(Float)  # % of team minutes played (barttorvik min_per, 0-100)
    points_per_game: Mapped[float] = mapped_column(Float, nullable=False)
    rebounds_per_game: Mapped[float] = mapped_column(Float, nullable=False)
    assists_per_game: Mapped[float] = mapped_column(Float, nullable=False)
    steals_per_game: Mapped[float] = mapped_column(Float, nullable=False)
    blocks_per_game: Mapped[float] = mapped_column(Float, nullable=False)
    turnovers_per_game: Mapped[float] = mapped_column(Float, nullable=False)
    # Advanced
    per: Mapped[Optional[float]] = mapped_column(Float)
    true_shooting_pct: Mapped[Optional[float]] = mapped_column(Float)
    usage_rate: Mapped[Optional[float]] = mapped_column(Float)
    assist_rate: Mapped[Optional[float]] = mapped_column(Float)
    bpm: Mapped[Optional[float]] = mapped_column(Float)
    win_shares: Mapped[Optional[float]] = mapped_column(Float)
    # Shot distribution (from barttorvik/hoopR)
    three_point_rate: Mapped[Optional[float]] = mapped_column(Float)
    rim_rate: Mapped[Optional[float]] = mapped_column(Float)
    mid_range_rate: Mapped[Optional[float]] = mapped_column(Float)
    assisted_fg_pct: Mapped[Optional[float]] = mapped_column(Float)
    # Barttorvik advanced — labeled in getadvstats.php but previously not stored
    offensive_rating: Mapped[Optional[float]] = mapped_column(Float)    # ortg
    defensive_rating: Mapped[Optional[float]] = mapped_column(Float)    # drtg
    efg_pct: Mapped[Optional[float]] = mapped_column(Float)             # effective FG%
    off_reb_pct: Mapped[Optional[float]] = mapped_column(Float)         # or_pct
    def_reb_pct: Mapped[Optional[float]] = mapped_column(Float)         # dr_pct
    tov_pct: Mapped[Optional[float]] = mapped_column(Float)             # turnover %
    free_throw_rate: Mapped[Optional[float]] = mapped_column(Float)     # ftr (FTA/FGA)
    ft_pct: Mapped[Optional[float]] = mapped_column(Float)
    fg2_pct: Mapped[Optional[float]] = mapped_column(Float)
    fg3_pct: Mapped[Optional[float]] = mapped_column(Float)
    block_pct: Mapped[Optional[float]] = mapped_column(Float)
    steal_pct: Mapped[Optional[float]] = mapped_column(Float)
    rim_pct: Mapped[Optional[float]] = mapped_column(Float)             # shooting% at rim
    mid_pct: Mapped[Optional[float]] = mapped_column(Float)             # shooting% mid-range
    dunk_made: Mapped[Optional[int]] = mapped_column(SmallInteger)
    dunk_att: Mapped[Optional[int]] = mapped_column(SmallInteger)
    barttorvik_role: Mapped[Optional[str]] = mapped_column(String(50))  # "Combo G", "Scoring PG"
    barttorvik_role_metric: Mapped[Optional[float]] = mapped_column(Float)
    rsci: Mapped[Optional[float]] = mapped_column(Float)                # recruiting rank
    birth_date: Mapped[Optional[date]] = mapped_column(Date)
    # Quality flags
    data_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    minutes_threshold_met: Mapped[bool] = mapped_column(Boolean, default=False)  # >= 10 games
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    player: Mapped[Player] = relationship(back_populates="season_stats")
    school: Mapped[School] = relationship()


class TeamSeasonStats(Base):
    """Partitioned by season in production (RANGE partitioning via Alembic migration)."""

    __tablename__ = "team_season_stats"
    __table_args__ = (
        UniqueConstraint("school_id", "season", name="uq_team_season_stats"),
        Index("ix_team_season_stats_school_season", "school_id", "season"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Performance (from barttorvik/hoopR)
    pace: Mapped[Optional[float]] = mapped_column(Float)          # possessions per 40 min
    offensive_rating: Mapped[Optional[float]] = mapped_column(Float)
    defensive_rating: Mapped[Optional[float]] = mapped_column(Float)
    net_rating: Mapped[Optional[float]] = mapped_column(Float)
    adj_em: Mapped[Optional[float]] = mapped_column(Float)        # barttorvik AdjEM
    adj_o: Mapped[Optional[float]] = mapped_column(Float)
    adj_d: Mapped[Optional[float]] = mapped_column(Float)
    adj_tempo: Mapped[Optional[float]] = mapped_column(Float)
    barthag: Mapped[Optional[float]] = mapped_column(Float)
    # Offensive four factors
    efg_pct: Mapped[Optional[float]] = mapped_column(Float)
    tov_rate: Mapped[Optional[float]] = mapped_column(Float)
    orb_rate: Mapped[Optional[float]] = mapped_column(Float)
    ft_rate: Mapped[Optional[float]] = mapped_column(Float)
    # Defensive four factors (from barttorvik four_factors endpoint — previously never written due to bug)
    efg_pct_def: Mapped[Optional[float]] = mapped_column(Float)
    tov_rate_def: Mapped[Optional[float]] = mapped_column(Float)
    drb_rate: Mapped[Optional[float]] = mapped_column(Float)
    ft_rate_def: Mapped[Optional[float]] = mapped_column(Float)
    # Shooting splits
    three_pct_off: Mapped[Optional[float]] = mapped_column(Float)
    three_pct_def: Mapped[Optional[float]] = mapped_column(Float)
    two_pct_off: Mapped[Optional[float]] = mapped_column(Float)
    assist_rate: Mapped[Optional[float]] = mapped_column(Float)
    assist_rate_opp: Mapped[Optional[float]] = mapped_column(Float)
    # Style
    three_point_rate: Mapped[Optional[float]] = mapped_column(Float)
    # Team metadata
    national_rank: Mapped[Optional[int]] = mapped_column(SmallInteger)
    wab: Mapped[Optional[float]] = mapped_column(Float)                 # wins above bubble
    sos: Mapped[Optional[float]] = mapped_column(Float)                 # strength of schedule
    ncsos: Mapped[Optional[float]] = mapped_column(Float)               # non-conference SOS
    # Record
    games_played: Mapped[Optional[int]] = mapped_column(SmallInteger)
    wins: Mapped[Optional[int]] = mapped_column(SmallInteger)
    losses: Mapped[Optional[int]] = mapped_column(SmallInteger)
    conf_wins: Mapped[Optional[int]] = mapped_column(SmallInteger)
    conf_losses: Mapped[Optional[int]] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    school: Mapped[School] = relationship(back_populates="team_season_stats")


class PlayerArchetype(Base):
    __tablename__ = "player_archetypes"
    __table_args__ = (
        UniqueConstraint("player_id", "season", name="uq_player_archetype_season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    archetype_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)   # K-Means cluster 0-9
    archetype_label: Mapped[str] = mapped_column(String(50), nullable=False)  # "3&D Wing" etc.
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    archetype_memberships: Mapped[Optional[list]] = mapped_column(JSONB)
    model_version: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    player: Mapped[Player] = relationship(back_populates="archetypes")


class TeamSystemProfile(Base):
    __tablename__ = "team_system_profiles"
    __table_args__ = (
        UniqueConstraint("school_id", "season", name="uq_team_system_season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    cluster_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    system_label: Mapped[str] = mapped_column(String(100), nullable=False)  # combined offense/defense label
    offense_cluster_id: Mapped[Optional[int]] = mapped_column(SmallInteger)
    defense_cluster_id: Mapped[Optional[int]] = mapped_column(SmallInteger)
    offense_memberships: Mapped[Optional[list]] = mapped_column(JSONB)
    defense_memberships: Mapped[Optional[list]] = mapped_column(JSONB)
    system_memberships: Mapped[Optional[list]] = mapped_column(JSONB)
    # 4-dim base style vector [3PT%, rim%, mid%, pace]
    # Replace ARRAY(Float) with Vector(4) from pgvector.sqlalchemy when extension is enabled
    style_vector: Mapped[Optional[list]] = mapped_column(ARRAY(Float))
    model_version: Mapped[str] = mapped_column(String(20), nullable=False)
    # Set by the news-monitoring agent's coach_departure tool (PR #50 / migration b1d3f5a7c9e2)
    # when a coaching change is detected — signals that cached scheme fit scores may be stale.
    stale_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)
    stale_reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    school: Mapped[School] = relationship(back_populates="team_system_profiles")


class HoopExplorerTeamStats(Base):
    """
    Team-level data from Hoop Explorer CSV exports.
    Covers Power 6 + strong mid-majors only (~365 teams per season).
    Primary use: team play-style vectors for clustering (Model 2) and scheme fit (Model 3).
    Join key to team_season_stats: school_id + season.
    """

    __tablename__ = "hoop_explorer_team_stats"
    __table_args__ = (
        UniqueConstraint("he_team_name", "season", name="uq_he_team_stats"),
        Index("ix_he_team_stats_school_season", "school_id", "season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))  # nullable until matched
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    he_team_id: Mapped[Optional[str]] = mapped_column(String(20))  # _id from CSV
    he_team_name: Mapped[str] = mapped_column(String(200), nullable=False)  # raw HE team_name
    conf: Mapped[Optional[str]] = mapped_column(String(100))
    # Season record
    wins: Mapped[Optional[int]] = mapped_column(SmallInteger)
    losses: Mapped[Optional[int]] = mapped_column(SmallInteger)
    wab: Mapped[Optional[float]] = mapped_column(Float)
    power: Mapped[Optional[float]] = mapped_column(Float)
    # Efficiency
    off_adj_ppp: Mapped[Optional[float]] = mapped_column(Float)
    def_adj_ppp: Mapped[Optional[float]] = mapped_column(Float)
    adj_net: Mapped[Optional[float]] = mapped_column(Float)
    tempo: Mapped[Optional[float]] = mapped_column(Float)
    # Offensive four factors
    off_efg: Mapped[Optional[float]] = mapped_column(Float)
    off_to: Mapped[Optional[float]] = mapped_column(Float)
    off_ftr: Mapped[Optional[float]] = mapped_column(Float)
    off_orb: Mapped[Optional[float]] = mapped_column(Float)
    # Defensive four factors
    def_efg: Mapped[Optional[float]] = mapped_column(Float)
    def_to: Mapped[Optional[float]] = mapped_column(Float)
    def_ftr: Mapped[Optional[float]] = mapped_column(Float)
    def_orb: Mapped[Optional[float]] = mapped_column(Float)
    # Shot profile
    off_threepr: Mapped[Optional[float]] = mapped_column(Float)   # 3PT attempt rate
    off_twoprimr: Mapped[Optional[float]] = mapped_column(Float)  # rim attempt rate
    off_twopmidr: Mapped[Optional[float]] = mapped_column(Float)  # mid attempt rate
    def_threepr: Mapped[Optional[float]] = mapped_column(Float)
    def_twoprimr: Mapped[Optional[float]] = mapped_column(Float)
    def_twopmidr: Mapped[Optional[float]] = mapped_column(Float)
    # Assist rates
    off_assist: Mapped[Optional[float]] = mapped_column(Float)
    off_ast_rim: Mapped[Optional[float]] = mapped_column(Float)
    off_ast_mid: Mapped[Optional[float]] = mapped_column(Float)
    off_ast_threep: Mapped[Optional[float]] = mapped_column(Float)
    def_assist: Mapped[Optional[float]] = mapped_column(Float)
    def_ast_rim: Mapped[Optional[float]] = mapped_column(Float)
    def_ast_mid: Mapped[Optional[float]] = mapped_column(Float)
    def_ast_threep: Mapped[Optional[float]] = mapped_column(Float)
    # Offensive play-style frequencies (12 types; _pct = plays per 100 team possessions)
    off_style_rim_attack_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_attack_kick_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_dribble_jumper_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_mid_range_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_perimeter_cut_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_big_cut_roll_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_post_up_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_post_kick_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_pick_pop_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_high_low_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_reb_scramble_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_transition_pct: Mapped[Optional[float]] = mapped_column(Float)
    # Defensive play-style frequencies (same 12 types)
    def_style_rim_attack_pct: Mapped[Optional[float]] = mapped_column(Float)
    def_style_attack_kick_pct: Mapped[Optional[float]] = mapped_column(Float)
    def_style_dribble_jumper_pct: Mapped[Optional[float]] = mapped_column(Float)
    def_style_mid_range_pct: Mapped[Optional[float]] = mapped_column(Float)
    def_style_perimeter_cut_pct: Mapped[Optional[float]] = mapped_column(Float)
    def_style_big_cut_roll_pct: Mapped[Optional[float]] = mapped_column(Float)
    def_style_post_up_pct: Mapped[Optional[float]] = mapped_column(Float)
    def_style_post_kick_pct: Mapped[Optional[float]] = mapped_column(Float)
    def_style_pick_pop_pct: Mapped[Optional[float]] = mapped_column(Float)
    def_style_high_low_pct: Mapped[Optional[float]] = mapped_column(Float)
    def_style_reb_scramble_pct: Mapped[Optional[float]] = mapped_column(Float)
    def_style_transition_pct: Mapped[Optional[float]] = mapped_column(Float)
    # Standalone transition / scramble rates (separate from play-style classification pct)
    off_trans_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_trans_ppp: Mapped[Optional[float]] = mapped_column(Float)
    def_trans_pct: Mapped[Optional[float]] = mapped_column(Float)
    def_trans_ppp: Mapped[Optional[float]] = mapped_column(Float)
    off_scramble_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_scramble_ppp: Mapped[Optional[float]] = mapped_column(Float)
    def_scramble_pct: Mapped[Optional[float]] = mapped_column(Float)
    def_scramble_ppp: Mapped[Optional[float]] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    school: Mapped[Optional[School]] = relationship()


class HoopExplorerPlayerStats(Base):
    """
    Player-level data from Hoop Explorer CSV exports.
    Covers all D1 tiers (~2,958 players per season; 5 seasons loaded = ~14,500 rows).
    Primary use: RAPM for transfer outcome model (Model 5), play-style vectors for M1 clustering and M3 scheme fit.
    Cross-source join: he_player_code stable across seasons; he_ncaa_id → barttorvik roster.
    player_id FK nullable until reconciled via (name, team, season) match.
    """

    __tablename__ = "hoop_explorer_player_stats"
    __table_args__ = (
        UniqueConstraint("he_player_code", "season", name="uq_he_player_stats"),
        Index("ix_he_player_stats_player_season", "player_id", "season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("players.id"))  # nullable until matched
    school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))  # nullable until matched
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # HE identifiers
    he_player_code: Mapped[str] = mapped_column(String(50), nullable=False)  # "CmBoozer" — stable cross-season
    he_ncaa_id: Mapped[Optional[str]] = mapped_column(String(20))  # roster.ncaa_id for cross-source join
    he_team_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Player metadata
    player_name: Mapped[Optional[str]] = mapped_column(String(200))  # HE format: "Surname, First Name"
    pos_class: Mapped[Optional[str]] = mapped_column(String(10))  # posClass: "PG", "s-PG", "CG", "WG", "WF", "S-PF", "PF/C", "C"
    year_class: Mapped[Optional[str]] = mapped_column(String(10))  # "Fr", "So", "Jr", "Sr"
    height: Mapped[Optional[str]] = mapped_column(String(10))  # "6-09"
    conf: Mapped[Optional[str]] = mapped_column(String(100))
    transfer_src: Mapped[Optional[str]] = mapped_column(String(200))
    transfer_dest: Mapped[Optional[str]] = mapped_column(String(200))
    # Playing time proxy
    off_team_poss_pct: Mapped[Optional[float]] = mapped_column(Float)  # fraction of team possessions on floor
    # Impact ratings — core RAPM metrics
    adj_rtg_margin: Mapped[Optional[float]] = mapped_column(Float)   # on-court net efficiency
    adj_rapm_margin: Mapped[Optional[float]] = mapped_column(Float)  # RAPM: isolates individual impact
    off_adj_rapm: Mapped[Optional[float]] = mapped_column(Float)
    def_adj_rapm: Mapped[Optional[float]] = mapped_column(Float)
    adj_rapm_margin_pred: Mapped[Optional[float]] = mapped_column(Float)  # projection to NCAAT-bound high-major
    # Production-weighted RAPM (mixes per-possession impact with playing-time share —
    # secondary label, see player_projection_state_space_plan.md §5/§8) and the off/def
    # split of the predicted-high-major projection. Source CSV column names are
    # asymmetric (off_adj_rapm_prod vs def_adj_prod_rapm) — not a typo, that's HE's own naming.
    off_adj_rapm_prod: Mapped[Optional[float]] = mapped_column(Float)
    def_adj_prod_rapm: Mapped[Optional[float]] = mapped_column(Float)
    adj_rapm_prod_margin: Mapped[Optional[float]] = mapped_column(Float)
    off_adj_rapm_pred: Mapped[Optional[float]] = mapped_column(Float)
    def_adj_rapm_pred: Mapped[Optional[float]] = mapped_column(Float)
    # Usage and shot creation
    off_usage: Mapped[Optional[float]] = mapped_column(Float)
    off_assist: Mapped[Optional[float]] = mapped_column(Float)
    off_efg: Mapped[Optional[float]] = mapped_column(Float)
    off_to: Mapped[Optional[float]] = mapped_column(Float)
    off_ftr: Mapped[Optional[float]] = mapped_column(Float)
    # Shot profile
    off_threepr: Mapped[Optional[float]] = mapped_column(Float)
    off_twoprimr: Mapped[Optional[float]] = mapped_column(Float)
    off_twopmidr: Mapped[Optional[float]] = mapped_column(Float)
    # Shooting efficiency
    off_threep: Mapped[Optional[float]] = mapped_column(Float)   # 3P%
    off_twoprim: Mapped[Optional[float]] = mapped_column(Float)  # rim%
    off_twopmid: Mapped[Optional[float]] = mapped_column(Float)  # mid%
    off_ft: Mapped[Optional[float]] = mapped_column(Float)       # FT%
    # Rebounding
    off_orb: Mapped[Optional[float]] = mapped_column(Float)
    def_orb: Mapped[Optional[float]] = mapped_column(Float)
    # Defense
    def_stl: Mapped[Optional[float]] = mapped_column(Float)
    def_blk: Mapped[Optional[float]] = mapped_column(Float)
    # Play-style frequencies (15 types; _pct = plays per 100 player possessions)
    off_style_rim_attack_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_attack_kick_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_perimeter_sniper_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_dribble_jumper_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_mid_range_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_hits_cutter_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_perimeter_cut_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_pnr_passer_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_big_cut_roll_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_post_up_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_post_kick_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_pick_pop_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_high_low_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_reb_scramble_pct: Mapped[Optional[float]] = mapped_column(Float)
    off_style_transition_pct: Mapped[Optional[float]] = mapped_column(Float)
    # Position probability distributions (posConfidences[_PG_] etc. from HE CSV)
    pos_confidence_pg: Mapped[Optional[float]] = mapped_column(Float)
    pos_confidence_sg: Mapped[Optional[float]] = mapped_column(Float)
    pos_confidence_sf: Mapped[Optional[float]] = mapped_column(Float)
    pos_confidence_pf: Mapped[Optional[float]] = mapped_column(Float)
    pos_confidence_c: Mapped[Optional[float]] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    player: Mapped[Optional[Player]] = relationship()
    school: Mapped[Optional[School]] = relationship()


class HoopRTeamSeasonStats(Base):
    """
    Team-level features aggregated from hoopR ESPN play-by-play data.
    Raw PBP (2.9M rows/season) is not stored here — aggregated features only.
    Raw parquet lives at s3://portalpoint-data/raw/hoopr/YYYY-MM-DD/

    Primary use: spatial shot zones + tempo for team clustering (Model 2)
    and scheme fit scoring (Model 3).
    Join key to team_season_stats: school_id + season.
    """

    __tablename__ = "hoopr_team_season_stats"
    __table_args__ = (
        UniqueConstraint("school_id", "season", name="uq_hoopr_team_stats"),
        Index("ix_hoopr_team_stats_school_season", "school_id", "season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    espn_team_id: Mapped[Optional[str]] = mapped_column(String(20))
    espn_team_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Tempo
    pbp_possession_sec: Mapped[Optional[float]] = mapped_column(Float)           # avg offensive possession duration (s)
    # Shot type profile
    pbp_rim_pct: Mapped[Optional[float]] = mapped_column(Float)                  # rim attempts / total shots
    pbp_three_pct: Mapped[Optional[float]] = mapped_column(Float)                # 3PT attempts / total shots
    pbp_mid_pct: Mapped[Optional[float]] = mapped_column(Float)                  # mid-range attempts / total shots
    # Spatial shot zones (5-zone half-court; sums to ~1.0)
    pbp_zone1_restricted_pct: Mapped[Optional[float]] = mapped_column(Float)     # restricted area (< 4ft from rim)
    pbp_zone2_mid_pct: Mapped[Optional[float]] = mapped_column(Float)            # mid-range 2PT
    pbp_zone3_corner3_pct: Mapped[Optional[float]] = mapped_column(Float)        # corner 3PT (y < 7.5)
    pbp_zone4_straight3_pct: Mapped[Optional[float]] = mapped_column(Float)      # above-break center 3PT
    pbp_zone5_wing3_pct: Mapped[Optional[float]] = mapped_column(Float)          # above-break wing 3PT
    # Possession outcome rates
    pbp_turnover_rate: Mapped[Optional[float]] = mapped_column(Float)            # TOs per tracked possession
    pbp_transition_rate: Mapped[Optional[float]] = mapped_column(Float)          # shots within 7s / total shots
    # Coverage metadata
    games_tracked: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default=text("0"))
    possessions_tracked: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    school: Mapped[Optional[School]] = relationship()


class HoopRPlayerSeasonStats(Base):
    """
    Player-level features aggregated from hoopR ESPN play-by-play data.
    Mirrors hoopr_team_season_stats' pbp_* feature set, keyed on athlete_id_1
    instead of team_id, plus player-only additions (clutch TS%, assist rate).
    player_id FK nullable until matched via crosswalk (name + team + season
    fuzzy match — see scripts/crosswalk_hoopr_players.py, ~90% hit rate);
    unmatched rows keep espn_athlete_id + raw_display_name for manual backfill,
    same pattern hoop_explorer_player_stats uses for he_player_code.
    """

    __tablename__ = "hoopr_player_season_stats"
    __table_args__ = (
        UniqueConstraint("espn_athlete_id", "season", name="uq_hoopr_player_stats"),
        Index("ix_hoopr_player_stats_player_season", "player_id", "season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("players.id"))  # nullable until matched
    school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))  # nullable until matched
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    espn_athlete_id: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_display_name: Mapped[str] = mapped_column(String(200), nullable=False)  # text-parsed PBP name
    espn_team_name: Mapped[str] = mapped_column(String(200), nullable=False)  # raw team, for manual backfill if unmatched
    match_confidence: Mapped[Optional[float]] = mapped_column(Float)  # fuzzy-match score; NULL if unmatched
    # Shot type profile (mirrors hoopr_team_season_stats)
    pbp_rim_pct: Mapped[Optional[float]] = mapped_column(Float)
    pbp_three_pct: Mapped[Optional[float]] = mapped_column(Float)
    pbp_mid_pct: Mapped[Optional[float]] = mapped_column(Float)
    # Spatial shot zones (5-zone half-court; sum to ~1.0)
    pbp_zone1_restricted_pct: Mapped[Optional[float]] = mapped_column(Float)
    pbp_zone2_mid_pct: Mapped[Optional[float]] = mapped_column(Float)
    pbp_zone3_corner3_pct: Mapped[Optional[float]] = mapped_column(Float)
    pbp_zone4_straight3_pct: Mapped[Optional[float]] = mapped_column(Float)
    pbp_zone5_wing3_pct: Mapped[Optional[float]] = mapped_column(Float)
    # Possession outcome rates
    pbp_turnover_rate: Mapped[Optional[float]] = mapped_column(Float)
    pbp_transition_rate: Mapped[Optional[float]] = mapped_column(Float)
    # Player-only additions
    pbp_clutch_ts_pct: Mapped[Optional[float]] = mapped_column(Float)  # TS% last 2min, <=5pt margin
    pbp_assist_rate: Mapped[Optional[float]] = mapped_column(Float)    # athlete_id_2 assists / possessions
    # Coverage metadata
    shot_attempts_tracked: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    games_tracked: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default=text("0"))
    possessions_tracked: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    player: Mapped[Optional[Player]] = relationship()
    school: Mapped[Optional[School]] = relationship()


class HoopRGame(Base):
    """
    One row per ESPN game, from sportsdataverse-data's mbb_schedule_{season}.parquet
    (same hoopR/ESPN lineage as hoopr_team_season_stats — different release tag,
    game-level grain instead of season-aggregate).
    home/away_school_id nullable until matched via ESPN_TEAM_ALIASES/fuzzy match;
    raw espn_team_id kept for manual backfill if unmatched.
    """

    __tablename__ = "hoopr_games"
    __table_args__ = (
        UniqueConstraint("espn_game_id", name="uq_hoopr_games_espn_game_id"),
        Index("ix_hoopr_games_season", "season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    espn_game_id: Mapped[str] = mapped_column(String(20), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    game_date: Mapped[Optional[date]] = mapped_column(Date)
    home_school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))
    away_school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))
    home_espn_team_id: Mapped[Optional[str]] = mapped_column(String(20))
    away_espn_team_id: Mapped[Optional[str]] = mapped_column(String(20))
    home_score: Mapped[Optional[int]] = mapped_column(Integer)
    away_score: Mapped[Optional[int]] = mapped_column(Integer)
    neutral_site: Mapped[Optional[bool]] = mapped_column(Boolean)
    venue: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    home_school: Mapped[Optional[School]] = relationship(foreign_keys=[home_school_id])
    away_school: Mapped[Optional[School]] = relationship(foreign_keys=[away_school_id])


class HoopRTeamGameLog(Base):
    """
    One row per team per game, from team_box_{season}.parquet.
    FKs to hoopr_games.espn_game_id (game-level grain). school_id nullable
    until matched, same crosswalk as hoopr_team_season_stats.
    """

    __tablename__ = "hoopr_team_game_logs"
    __table_args__ = (
        UniqueConstraint("espn_game_id", "espn_team_id", name="uq_hoopr_team_game_log"),
        Index("ix_hoopr_team_game_logs_school_season", "school_id", "season"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    espn_game_id: Mapped[str] = mapped_column(ForeignKey("hoopr_games.espn_game_id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    game_date: Mapped[Optional[date]] = mapped_column(Date)
    school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))
    espn_team_id: Mapped[str] = mapped_column(String(20), nullable=False)
    opponent_school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))
    home_away: Mapped[Optional[str]] = mapped_column(String(10))
    points: Mapped[Optional[int]] = mapped_column(Integer)
    opponent_points: Mapped[Optional[int]] = mapped_column(Integer)
    field_goals_made: Mapped[Optional[int]] = mapped_column(Integer)
    field_goals_attempted: Mapped[Optional[int]] = mapped_column(Integer)
    three_point_field_goals_made: Mapped[Optional[int]] = mapped_column(Integer)
    three_point_field_goals_attempted: Mapped[Optional[int]] = mapped_column(Integer)
    free_throws_made: Mapped[Optional[int]] = mapped_column(Integer)
    free_throws_attempted: Mapped[Optional[int]] = mapped_column(Integer)
    offensive_rebounds: Mapped[Optional[int]] = mapped_column(Integer)
    defensive_rebounds: Mapped[Optional[int]] = mapped_column(Integer)
    total_rebounds: Mapped[Optional[int]] = mapped_column(Integer)
    assists: Mapped[Optional[int]] = mapped_column(Integer)
    steals: Mapped[Optional[int]] = mapped_column(Integer)
    blocks: Mapped[Optional[int]] = mapped_column(Integer)
    turnovers: Mapped[Optional[int]] = mapped_column(Integer)
    fouls: Mapped[Optional[int]] = mapped_column(Integer)
    points_in_paint: Mapped[Optional[int]] = mapped_column(Integer)
    fast_break_points: Mapped[Optional[int]] = mapped_column(Integer)
    turnover_points: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    game: Mapped[HoopRGame] = relationship()
    school: Mapped[Optional[School]] = relationship(foreign_keys=[school_id])
    opponent_school: Mapped[Optional[School]] = relationship(foreign_keys=[opponent_school_id])


class HoopRPlayerGameLog(Base):
    """
    One row per player per game, from player_box_{season}.parquet.
    player_id resolved via players.espn_id first (already ~90% backfilled by
    hoopr_player_season_stats ingest), fuzzy name+roster match second —
    same fallback order as hoopr_player_season_stats. match_status records
    which path resolved the row (or that none did).
    """

    __tablename__ = "hoopr_player_game_logs"
    __table_args__ = (
        UniqueConstraint("espn_game_id", "espn_athlete_id", name="uq_hoopr_player_game_log"),
        Index("ix_hoopr_player_game_logs_player_season", "player_id", "season"),
        Index("ix_hoopr_player_game_logs_school_season", "school_id", "season"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    espn_game_id: Mapped[str] = mapped_column(ForeignKey("hoopr_games.espn_game_id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    game_date: Mapped[Optional[date]] = mapped_column(Date)
    player_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("players.id"))  # nullable until matched
    espn_athlete_id: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))
    opponent_school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))
    home_away: Mapped[Optional[str]] = mapped_column(String(10))
    starter: Mapped[Optional[bool]] = mapped_column(Boolean)
    minutes: Mapped[Optional[float]] = mapped_column(Float)
    field_goals_made: Mapped[Optional[int]] = mapped_column(Integer)
    field_goals_attempted: Mapped[Optional[int]] = mapped_column(Integer)
    three_point_field_goals_made: Mapped[Optional[int]] = mapped_column(Integer)
    three_point_field_goals_attempted: Mapped[Optional[int]] = mapped_column(Integer)
    free_throws_made: Mapped[Optional[int]] = mapped_column(Integer)
    free_throws_attempted: Mapped[Optional[int]] = mapped_column(Integer)
    offensive_rebounds: Mapped[Optional[int]] = mapped_column(Integer)
    defensive_rebounds: Mapped[Optional[int]] = mapped_column(Integer)
    rebounds: Mapped[Optional[int]] = mapped_column(Integer)
    assists: Mapped[Optional[int]] = mapped_column(Integer)
    steals: Mapped[Optional[int]] = mapped_column(Integer)
    blocks: Mapped[Optional[int]] = mapped_column(Integer)
    turnovers: Mapped[Optional[int]] = mapped_column(Integer)
    fouls: Mapped[Optional[int]] = mapped_column(Integer)
    points: Mapped[Optional[int]] = mapped_column(Integer)
    match_confidence: Mapped[Optional[float]] = mapped_column(Float)
    match_status: Mapped[str] = mapped_column(String(20), nullable=False)  # matched/unmatched/ambiguous/no_school
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    game: Mapped[HoopRGame] = relationship()
    player: Mapped[Optional[Player]] = relationship()
    school: Mapped[Optional[School]] = relationship(foreign_keys=[school_id])
    opponent_school: Mapped[Optional[School]] = relationship(foreign_keys=[opponent_school_id])


class CoachingTendency(Base):
    __tablename__ = "coaching_tendencies"
    __table_args__ = (
        UniqueConstraint("coach_id", "school_id", "season", name="uq_coaching_tendency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    coach_id: Mapped[int] = mapped_column(ForeignKey("coaches.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    pace_preference: Mapped[Optional[float]] = mapped_column(Float)
    three_point_emphasis: Mapped[Optional[float]] = mapped_column(Float)
    post_emphasis: Mapped[Optional[float]] = mapped_column(Float)
    ball_movement_rating: Mapped[Optional[float]] = mapped_column(Float)
    defensive_scheme: Mapped[Optional[str]] = mapped_column(String(50))
    games_sample: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    coach: Mapped[Coach] = relationship(back_populates="coaching_tendencies")


class RosterDepthChart(Base):
    __tablename__ = "roster_depth_charts"
    __table_args__ = (
        Index("ix_roster_depth_school_season", "school_id", "season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), nullable=False)
    position: Mapped[str] = mapped_column(String(2), nullable=False)
    depth_rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1=starter
    projected_minutes: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RosterGapAnalysis(Base):
    __tablename__ = "roster_gap_analysis"
    __table_args__ = (
        Index("ix_roster_gap_school_season", "school_id", "season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    position_needed: Mapped[str] = mapped_column(String(2), nullable=False)
    archetype_needed: Mapped[Optional[str]] = mapped_column(String(50))
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1=high 2=medium 3=low
    minutes_available: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------------------
# Layer 3 — Transfer
# ---------------------------------------------------------------------------


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        Index("ix_transfers_player_season", "player_id", "season"),
        UniqueConstraint("player_id", "season", name="uq_transfers_player_season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), nullable=False)
    from_school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))
    to_school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # season transferred INTO
    portal_entry_date: Mapped[Optional[date]] = mapped_column(Date)
    commitment_date: Mapped[Optional[date]] = mapped_column(Date)
    transfer_type: Mapped[Optional[str]] = mapped_column(String(20))  # graduate/regular
    # Pre-transfer stats snapshot
    pre_per: Mapped[Optional[float]] = mapped_column(Float)
    pre_minutes_per_game: Mapped[Optional[float]] = mapped_column(Float)
    pre_usage_rate: Mapped[Optional[float]] = mapped_column(Float)
    # Post-transfer stats (filled after season completes — training labels for Model 5)
    post_per: Mapped[Optional[float]] = mapped_column(Float)
    post_minutes_per_game: Mapped[Optional[float]] = mapped_column(Float)
    post_usage_rate: Mapped[Optional[float]] = mapped_column(Float)
    per_change: Mapped[Optional[float]] = mapped_column(Float)
    minutes_change: Mapped[Optional[float]] = mapped_column(Float)
    outcome_score: Mapped[Optional[float]] = mapped_column(Float)  # 0-5 success rating
    verbalcommits_id: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    player: Mapped[Player] = relationship(foreign_keys=[player_id], back_populates="transfers")
    from_school: Mapped[Optional[School]] = relationship(foreign_keys=[from_school_id])
    to_school: Mapped[Optional[School]] = relationship(foreign_keys=[to_school_id])


class TransferPortalEvent(Base):
    """
    Raw staging for every scraped 247Sports transfer-portal row, matched or
    not — same "keep raw rows, don't silently drop unmatched" pattern as
    hoopr_player_game_logs. Matched rows get promoted into `transfers`
    (which keeps its existing NOT NULL player_id contract untouched).

    portal_entry_date/commitment_date fill in incrementally across repeated
    scrapes: a single scrape only exposes a player's *current* status and its
    timestamp, so a player scraped while status=Entered records
    portal_entry_date, and a later scrape after they commit fills
    commitment_date without erasing the already-stored portal_entry_date.
    """

    __tablename__ = "transfer_portal_events"
    __table_args__ = (
        UniqueConstraint("source", "source_player_key", "season", name="uq_transfer_portal_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # "247sports"
    source_player_key: Mapped[str] = mapped_column(String(50), nullable=False)
    player_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("players.id"))  # nullable until matched
    raw_player_name: Mapped[str] = mapped_column(String(200), nullable=False)
    match_confidence: Mapped[Optional[float]] = mapped_column(Float)
    match_status: Mapped[str] = mapped_column(String(20), nullable=False)  # matched/unmatched/ambiguous/no_school
    from_school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))
    to_school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))
    from_institution_raw: Mapped[Optional[str]] = mapped_column(String(200))
    to_institution_raw: Mapped[Optional[str]] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # Entered/Committed/...
    portal_entry_date: Mapped[Optional[date]] = mapped_column(Date)
    commitment_date: Mapped[Optional[date]] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    player: Mapped[Optional[Player]] = relationship()
    from_school: Mapped[Optional[School]] = relationship(foreign_keys=[from_school_id])
    to_school: Mapped[Optional[School]] = relationship(foreign_keys=[to_school_id])


class RosterSnapshot(Base):
    """One row per school per scrape date, from barttorvik rostercast.php."""

    __tablename__ = "roster_snapshots"
    __table_args__ = (
        UniqueConstraint("school_id", "snapshot_date", name="uq_roster_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)  # "barttorvik_rostercast"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    school: Mapped[School] = relationship()


class RosterSnapshotPlayer(Base):
    """
    One row per player on a roster_snapshots row. returning_status is
    computed by us (not given by barttorvik — the base rostercast.php table
    carries no departure/incoming markers): returning if this player_id was
    on this same school last season per player_season_stats, transfer_in if
    they were on a different school, new if unresolved/no prior stats row.
    "departing" is intentionally not a value here — a departed player isn't
    on the current snapshot at all, so detecting it requires diffing two
    snapshots (or last season's roster), which is issue #17 items 5/6, not
    this table.
    """

    __tablename__ = "roster_snapshot_players"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "raw_player_name", name="uq_roster_snapshot_player"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("roster_snapshots.id"), nullable=False)
    player_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("players.id"))  # nullable until matched
    raw_player_name: Mapped[str] = mapped_column(String(200), nullable=False)
    class_year: Mapped[Optional[str]] = mapped_column(String(10))
    height: Mapped[Optional[str]] = mapped_column(String(10))
    min_pct: Mapped[Optional[float]] = mapped_column(Float)
    ortg: Mapped[Optional[float]] = mapped_column(Float)
    usage_rate: Mapped[Optional[float]] = mapped_column(Float)
    returning_status: Mapped[str] = mapped_column(String(20), nullable=False)  # returning/transfer_in/new/unknown
    transfer_source_school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))
    match_confidence: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    snapshot: Mapped[RosterSnapshot] = relationship()
    player: Mapped[Optional[Player]] = relationship()
    transfer_source_school: Mapped[Optional[School]] = relationship(foreign_keys=[transfer_source_school_id])


class RosterStateFeatures(Base):
    """
    Derived roster-composition facts for one roster_snapshots row — deliberately
    facts (counts/sums), not gap scores: turning a count into a "gap" needs the
    league-benchmark logic Gap Matching already owns (src/portalpoint/modeling/
    gap_matching.py), so that interpretation stays there instead of forking.

    "Departing" is computed by diffing player_season_stats[school, season]
    (last completed season's roster) against this snapshot's actual players —
    no day-over-day snapshot history needed, since player_season_stats already
    gives a real prior-roster reference.
    """

    __tablename__ = "roster_state_features"
    __table_args__ = (
        UniqueConstraint("snapshot_id", name="uq_roster_state_features_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("roster_snapshots.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    returning_minutes_by_position: Mapped[Optional[dict]] = mapped_column(JSONB)
    departing_minutes_by_position: Mapped[Optional[dict]] = mapped_column(JSONB)
    incoming_transfer_minutes_by_position: Mapped[Optional[dict]] = mapped_column(JSONB)
    open_minutes_by_position: Mapped[Optional[dict]] = mapped_column(JSONB)
    open_usage_by_position: Mapped[Optional[dict]] = mapped_column(JSONB)
    returning_production: Mapped[Optional[float]] = mapped_column(Float)
    returning_player_impact: Mapped[Optional[float]] = mapped_column(Float)
    class_balance: Mapped[Optional[dict]] = mapped_column(JSONB)
    returning_archetype_counts: Mapped[Optional[dict]] = mapped_column(JSONB)
    departing_archetype_counts: Mapped[Optional[dict]] = mapped_column(JSONB)
    incoming_archetype_counts: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    snapshot: Mapped[RosterSnapshot] = relationship()
    school: Mapped[School] = relationship()


class RosterBaselineMember(Base):
    """
    Persisted output of portalpoint.modeling.roster_baseline.build_roster_baseline_frame()
    — one row per (player_id, school_id, season) the shared roster baseline
    considers "on this school's roster outlook." Written by both
    scripts/run_gap_matching.py and notebooks/models/gap_matching.ipynb (same
    helper, roster_baseline.write_roster_baseline_members()) so there is one
    real computation, not a second copy of the membership rules re-derived
    at API-read time.
    """

    __tablename__ = "roster_baseline_members"
    __table_args__ = (
        UniqueConstraint("player_id", "school_id", "season", name="uq_roster_baseline_member"),
        Index("ix_roster_baseline_school_season", "school_id", "season"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    baseline_status: Mapped[str] = mapped_column(String(30), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    player: Mapped[Player] = relationship()
    school: Mapped[School] = relationship()


class NILValuation(Base):
    __tablename__ = "nil_valuations"
    __table_args__ = (
        UniqueConstraint("player_id", "school_id", "season", name="uq_nil_valuation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    estimated_value_usd: Mapped[Optional[float]] = mapped_column(Float)
    market_tier: Mapped[Optional[str]] = mapped_column(String(10))  # high/medium/low
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # on3/estimated
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------------------
# Layer 4 — ML Outputs
# ---------------------------------------------------------------------------


class PlayerTeamFitScore(Base):
    __tablename__ = "player_team_fit_scores"
    __table_args__ = (
        UniqueConstraint("player_id", "school_id", "season", name="uq_fit_score"),
        Index("ix_fit_scores_overall_fit", "overall_fit"),  # for ranking queries
        Index("ix_fit_scores_school_season_candidate", "school_id", "season", "is_portal_candidate"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    overall_fit: Mapped[float] = mapped_column(Float, nullable=False)
    gap_match: Mapped[float] = mapped_column(Float, nullable=False)
    scheme_fit: Mapped[float] = mapped_column(Float, nullable=False)
    role_fit: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.0"))
    program_fit: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.0"))
    # Weights used for this computation (may differ from defaults if user customized)
    weight_gap: Mapped[float] = mapped_column(Float, nullable=False, default=0.20)
    weight_scheme: Mapped[float] = mapped_column(Float, nullable=False, default=0.30)
    weight_role: Mapped[float] = mapped_column(Float, nullable=False, default=0.25, server_default=text("0.25"))
    weight_program: Mapped[float] = mapped_column(Float, nullable=False, default=0.25, server_default=text("0.25"))
    # Full component breakdown stored as JSONB for API response
    breakdown: Mapped[Optional[dict]] = mapped_column(JSONB)
    # True if player_id had a matched transfer_portal_events row (Entered/Committed)
    # for this season — see portalpoint.modeling.availability. Scoring stays
    # all-pairs; this flag scopes the recommendation-facing query surface.
    is_portal_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("player_id", "school_id", name="uq_prediction"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    predicted_per_change: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_role: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    shap_explanations: Mapped[Optional[list]] = mapped_column(JSONB)
    model_version: Mapped[str] = mapped_column(String(20), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PlayerProjection(Base):
    """Player talent/value projection — see docs/models/player_projection_state_space_plan.md.

    Phase 0 writes neutral rows only (school_id NULL, projection_mode='neutral').
    Destination-mode rows (school_id set) are Phase 2+ work, gated on Role Fit
    existing. school_id is nullable, so a plain UniqueConstraint can't dedupe
    correctly — Postgres treats every NULL as distinct under a normal unique
    constraint, which would let every neutral rerun insert a fresh row instead
    of updating in place. Two partial unique indexes handle the two modes
    separately instead (see plan doc §18).
    """

    __tablename__ = "player_projections"
    __table_args__ = (
        Index(
            "uq_player_projections_neutral", "player_id", "season", "model_version",
            unique=True, postgresql_where=text("school_id IS NULL"),
        ),
        Index(
            "uq_player_projections_destination", "player_id", "school_id", "season", "model_version",
            unique=True, postgresql_where=text("school_id IS NOT NULL"),
        ),
        Index("ix_player_projections_player_season", "player_id", "season"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), nullable=False)
    school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))  # null for neutral mode
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    projection_mode: Mapped[str] = mapped_column(String(20), nullable=False)  # 'neutral' | 'destination'
    value_per_100: Mapped[float] = mapped_column(Float, nullable=False)
    value_ci_lower: Mapped[Optional[float]] = mapped_column(Float)
    value_ci_upper: Mapped[Optional[float]] = mapped_column(Float)
    projected_minutes: Mapped[Optional[float]] = mapped_column(Float)  # destination mode only
    projected_usage: Mapped[Optional[float]] = mapped_column(Float)    # destination mode only
    projected_box_score: Mapped[Optional[dict]] = mapped_column(JSONB)
    projected_rates: Mapped[Optional[dict]] = mapped_column(JSONB)
    skill_states: Mapped[Optional[dict]] = mapped_column(JSONB)
    skill_percentiles: Mapped[Optional[dict]] = mapped_column(JSONB)
    uncertainty: Mapped[Optional[dict]] = mapped_column(JSONB)
    explanation: Mapped[Optional[dict]] = mapped_column(JSONB)
    model_version: Mapped[str] = mapped_column(String(30), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    player: Mapped[Player] = relationship()
    school: Mapped[Optional[School]] = relationship()


class PlayingTimeProjection(Base):
    """Roster-aware opportunity projection used as the source of Role Fit."""

    __tablename__ = "playing_time_projections"
    __table_args__ = (
        UniqueConstraint(
            "player_id",
            "school_id",
            "season",
            "model_version",
            name="uq_playing_time_projection",
        ),
        Index("ix_playing_time_player_season", "player_id", "season"),
        Index("ix_playing_time_school_season", "school_id", "season"),
        Index("ix_playing_time_season_role_fit", "season", "role_fit"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    roster_snapshot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("roster_snapshots.id"))
    expected_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    expected_minutes_share: Mapped[float] = mapped_column(Float, nullable=False)
    minutes_ci_lower: Mapped[float] = mapped_column(Float, nullable=False)
    minutes_ci_upper: Mapped[float] = mapped_column(Float, nullable=False)
    expected_usage: Mapped[float] = mapped_column(Float, nullable=False)
    usage_role: Mapped[str] = mapped_column(String(40), nullable=False)
    usage_role_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    starter_probability: Mapped[Optional[float]] = mapped_column(Float)
    rotation_probability: Mapped[Optional[float]] = mapped_column(Float)
    displaced_minutes: Mapped[Optional[dict]] = mapped_column(JSONB)
    opportunity_drivers: Mapped[Optional[dict]] = mapped_column(JSONB)
    data_quality_flags: Mapped[Optional[dict]] = mapped_column(JSONB)
    scenario_overrides: Mapped[Optional[dict]] = mapped_column(JSONB)
    explanation: Mapped[Optional[dict]] = mapped_column(JSONB)
    role_fit: Mapped[float] = mapped_column(Float, nullable=False)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    player: Mapped[Player] = relationship()
    school: Mapped[School] = relationship()
    roster_snapshot: Mapped[Optional[RosterSnapshot]] = relationship()


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        Index("ix_recommendations_user_player", "user_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), nullable=False)
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    overall_fit: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[Optional[str]] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(String(20), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TeamRatingProjection(Base):
    __tablename__ = "team_rating_projections"
    __table_args__ = (
        UniqueConstraint("player_id", "school_id", "season", name="uq_team_rating_projection"),
        Index("ix_team_rating_school_season", "school_id", "season"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    current_adj_em: Mapped[float] = mapped_column(Float, nullable=False)
    projected_adj_em: Mapped[float] = mapped_column(Float, nullable=False)
    delta_adj_em: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_adj_o: Mapped[Optional[float]] = mapped_column(Float)
    baseline_adj_d: Mapped[Optional[float]] = mapped_column(Float)
    projected_adj_o: Mapped[Optional[float]] = mapped_column(Float)
    projected_adj_d: Mapped[Optional[float]] = mapped_column(Float)
    ci_lower: Mapped[float] = mapped_column(Float, nullable=False)
    ci_upper: Mapped[float] = mapped_column(Float, nullable=False)
    national_percentile: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    conference_rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    expected_minutes_input: Mapped[float] = mapped_column(Float, nullable=False)
    candidate_usage_role: Mapped[Optional[str]] = mapped_column(String(40))
    explanation: Mapped[Optional[dict]] = mapped_column(JSONB)
    minutes_distribution: Mapped[Optional[dict]] = mapped_column(JSONB)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# Layer 5 — User
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    preferences: Mapped[Optional[UserPreference]] = relationship(back_populates="user", uselist=False)
    shortlist: Mapped[list[UserShortlist]] = relationship(back_populates="user")
    feedback: Mapped[list[UserFeedback]] = relationship(back_populates="user")


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    # Importance weights (1-10 scale) for Program Fit sub-components
    importance_scheme_fit: Mapped[int] = mapped_column(SmallInteger, default=7, server_default=text("7"))
    importance_role_fit: Mapped[int] = mapped_column(SmallInteger, default=5, server_default=text("5"))
    importance_gap_match: Mapped[int] = mapped_column(SmallInteger, default=5, server_default=text("5"))
    importance_program_fit: Mapped[int] = mapped_column(SmallInteger, default=5, server_default=text("5"))
    # Fit component weights (must sum to 1.0)
    weight_gap: Mapped[float] = mapped_column(Float, default=0.20)
    weight_scheme: Mapped[float] = mapped_column(Float, default=0.30)
    weight_role: Mapped[float] = mapped_column(Float, default=0.25, server_default=text("0.25"))
    weight_program: Mapped[float] = mapped_column(Float, default=0.25, server_default=text("0.25"))
    # Flexible filters stored as JSONB (desired_major, regions, conferences, enrollment range)
    filters: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="preferences")


class UserPreferenceProfile(Base):
    """Named, saved snapshots of weights+filters a user can switch between
    (e.g. "Wing search" vs "Backup PG search") — additive on top of
    UserPreference, which stays the single "active" row fit_scores.py reads.
    Activating a profile copies its fields into that row; this table is never
    read by the fit-score computation path itself."""

    __tablename__ = "user_preference_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_user_preference_profiles_user_name"),
        Index("ix_user_preference_profiles_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    importance_scheme_fit: Mapped[int] = mapped_column(SmallInteger, default=7, server_default=text("7"))
    importance_role_fit: Mapped[int] = mapped_column(SmallInteger, default=5, server_default=text("5"))
    importance_gap_match: Mapped[int] = mapped_column(SmallInteger, default=5, server_default=text("5"))
    importance_program_fit: Mapped[int] = mapped_column(SmallInteger, default=5, server_default=text("5"))
    weight_gap: Mapped[float] = mapped_column(Float, default=0.20, server_default=text("0.20"))
    weight_scheme: Mapped[float] = mapped_column(Float, default=0.30, server_default=text("0.30"))
    weight_role: Mapped[float] = mapped_column(Float, default=0.25, server_default=text("0.25"))
    weight_program: Mapped[float] = mapped_column(Float, default=0.25, server_default=text("0.25"))
    filters: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship()


class UserFeedback(Base):
    __tablename__ = "user_feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id"))
    feedback_type: Mapped[str] = mapped_column(String(20), nullable=False)  # helpful/not_helpful/dismissed
    rating: Mapped[Optional[int]] = mapped_column(SmallInteger)  # 1-5
    comment: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="feedback")


class UserShortlist(Base):
    __tablename__ = "user_shortlists"
    __table_args__ = (
        UniqueConstraint("user_id", "player_id", name="uq_user_shortlist"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), nullable=False)
    overall_fit: Mapped[Optional[float]] = mapped_column(Float)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="shortlist")
    player: Mapped[Player] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(50), nullable=False)  # login/claim_player/update_preferences/...
    resource_type: Mapped[Optional[str]] = mapped_column(String(50))
    resource_id: Mapped[Optional[int]] = mapped_column(Integer)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))   # supports IPv6
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
