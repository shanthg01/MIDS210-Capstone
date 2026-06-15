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

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
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
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
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
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Traditional
    games_played: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    minutes_per_game: Mapped[float] = mapped_column(Float, nullable=False)
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
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    season: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    archetype_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)   # K-Means cluster 0-9
    archetype_label: Mapped[str] = mapped_column(String(50), nullable=False)  # "3&D Wing" etc.
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
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
    system_label: Mapped[str] = mapped_column(String(50), nullable=False)  # "Fast 3PT Heavy" etc.
    offense_cluster_id: Mapped[Optional[int]] = mapped_column(SmallInteger)
    defense_cluster_id: Mapped[Optional[int]] = mapped_column(SmallInteger)
    # 5-dim style vector [3PT%, rim%, usage%, assisted%, pace]
    # Replace ARRAY(Float) with Vector(5) from pgvector.sqlalchemy when extension is enabled
    style_vector: Mapped[Optional[list]] = mapped_column(ARRAY(Float))
    model_version: Mapped[str] = mapped_column(String(20), nullable=False)
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
    def_assist: Mapped[Optional[float]] = mapped_column(Float)
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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    school: Mapped[Optional[School]] = relationship()


class HoopExplorerPlayerStats(Base):
    """
    Player-level data from Hoop Explorer CSV exports.
    Covers Power 6 + strong mid-majors only (high tier, ~672 players per season).
    Primary use: RAPM for transfer outcome model (Model 5), play-style vectors for scheme fit (Model 3).
    Cross-source join: he_player_code stable across seasons; he_ncaa_id → barttorvik roster.
    player_id FK nullable until reconciled via (name, team, season) match.
    """

    __tablename__ = "hoop_explorer_player_stats"
    __table_args__ = (
        UniqueConstraint("he_player_code", "season", name="uq_he_player_stats"),
        Index("ix_he_player_stats_player_season", "player_id", "season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[Optional[int]] = mapped_column(ForeignKey("players.id"))  # nullable until matched
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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    player: Mapped[Optional[Player]] = relationship()
    school: Mapped[Optional[School]] = relationship()


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
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
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
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
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


class NILValuation(Base):
    __tablename__ = "nil_valuations"
    __table_args__ = (
        UniqueConstraint("player_id", "school_id", "season", name="uq_nil_valuation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
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
        UniqueConstraint("player_id", "school_id", name="uq_fit_score"),
        Index("ix_fit_scores_overall_fit", "overall_fit"),  # for ranking queries
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    overall_fit: Mapped[float] = mapped_column(Float, nullable=False)
    gap_match: Mapped[float] = mapped_column(Float, nullable=False)
    scheme_fit: Mapped[float] = mapped_column(Float, nullable=False)
    role_fit: Mapped[float] = mapped_column(Float, nullable=False)
    program_fit: Mapped[float] = mapped_column(Float, nullable=False)
    # Weights used for this computation (may differ from defaults if user customized)
    weight_gap: Mapped[float] = mapped_column(Float, nullable=False, default=0.20)
    weight_scheme: Mapped[float] = mapped_column(Float, nullable=False, default=0.30)
    weight_role: Mapped[float] = mapped_column(Float, nullable=False, default=0.25)
    weight_program: Mapped[float] = mapped_column(Float, nullable=False, default=0.25)
    # Full component breakdown stored as JSONB for API response
    breakdown: Mapped[Optional[dict]] = mapped_column(JSONB)
    model_version: Mapped[str] = mapped_column(String(20), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("player_id", "school_id", name="uq_prediction"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    predicted_per_change: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_role: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    shap_explanations: Mapped[Optional[list]] = mapped_column(JSONB)
    model_version: Mapped[str] = mapped_column(String(20), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        Index("ix_recommendations_user_player", "user_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    overall_fit: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[Optional[str]] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(String(20), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TeamRatingProjection(Base):
    __tablename__ = "team_rating_projections"
    __table_args__ = (
        UniqueConstraint("player_id", "school_id", name="uq_team_rating_projection"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    current_adj_em: Mapped[float] = mapped_column(Float, nullable=False)
    projected_adj_em: Mapped[float] = mapped_column(Float, nullable=False)
    delta_adj_em: Mapped[float] = mapped_column(Float, nullable=False)
    ci_lower: Mapped[float] = mapped_column(Float, nullable=False)   # 80% CI lower bound
    ci_upper: Mapped[float] = mapped_column(Float, nullable=False)   # 80% CI upper bound
    national_percentile: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    conference_rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    expected_minutes_input: Mapped[float] = mapped_column(Float, nullable=False)  # from Model 4
    model_version: Mapped[str] = mapped_column(String(20), nullable=False)
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
    importance_scheme_fit: Mapped[int] = mapped_column(SmallInteger, default=7)
    importance_role_fit: Mapped[int] = mapped_column(SmallInteger, default=5)
    importance_gap_match: Mapped[int] = mapped_column(SmallInteger, default=5)
    importance_program_fit: Mapped[int] = mapped_column(SmallInteger, default=5)
    # Fit component weights (must sum to 1.0)
    weight_gap: Mapped[float] = mapped_column(Float, default=0.20)
    weight_scheme: Mapped[float] = mapped_column(Float, default=0.30)
    weight_role: Mapped[float] = mapped_column(Float, default=0.25)
    weight_program: Mapped[float] = mapped_column(Float, default=0.25)
    # Flexible filters stored as JSONB (desired_major, regions, conferences, enrollment range)
    filters: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="preferences")


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
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
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
