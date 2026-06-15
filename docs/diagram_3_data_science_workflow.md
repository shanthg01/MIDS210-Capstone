# DIAGRAM 3: Data Science & Engineering Workflow
## End-to-End Data Flow: Sources → Processing → Models → Predictions

```
╔═══════════════════════════════════════════════════════════════════════════╗
║                         DATA SOURCES (EXTERNAL)                           ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐
│  barttorvik     │  │   hoopR API     │  │  Hoop-Explorer  │  │ VerbalCommits│
│  (Primary)      │  │   (Secondary)   │  │  (Secondary)    │  │  (Web Scrape)│
├─────────────────┤  ├─────────────────┤  ├─────────────────┤  ├──────────────┤
│ • AdjEM/AdjO/   │  │ • PBP events    │  │ • Supplemental  │  │ • Transfer   │
│   AdjD ratings  │  │ • 2.9M rows/ssn │  │   player data   │  │   commitments│
│ • Four Factors  │  │ • 5 spatial     │  │ • Team data     │  │ • Portal     │
│ • Player stats  │  │ • shot zones    │  │ • Advanced      │  │   entries    │
│ • Tempo/pace    │  │ • Parquet → S3  │  │   metrics       │  │ • Timelines  │
└─────────────────┘  └─────────────────┘  └─────────────────┘  └──────────────┘
         │                    │                    │                    │
         └────────────────────┴────────────────────┴────────────────────┘
                                      │
                                      ↓

╔═══════════════════════════════════════════════════════════════════════════╗
║                    STAGE 1: DATA INGESTION & VALIDATION                   ║
║                    (Airflow DAG: daily_data_ingestion)                    ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────────────────────────────┐
│  Task 1.1: API Scraping (Celery Workers)                                 │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  scrape_barttorvik_worker.py                                 │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │  import requests                                              │        │
│  │  import pandas as pd                                         │        │
│  │                                                               │        │
│  │  # Pull team ratings (AdjEM, AdjO, AdjD)                    │        │
│  │  team_ratings = requests.get(                                │        │
│  │      'https://barttorvik.com/trank.json'                    │        │
│  │  ).json()                                                    │        │
│  │                                                               │        │
│  │  # Pull player stats                                         │        │
│  │  player_stats = requests.get(                                │        │
│  │      'https://barttorvik.com/playerstat.php',                │        │
│  │      params={'year': season, 'type': 'totals'}              │        │
│  │  ).json()                                                    │        │
│  │                                                               │        │
│  │      # Save raw data to S3                                   │        │
│  │      s3.put_object(                                          │        │
│  │          Bucket='portalpoint-raw-data',                       │        │
│  │          Key=f'barttorvik/{date}/team_ratings.json',        │        │
│  │          Body=json.dumps(team_ratings)                       │        │
│  │      )                                                        │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  scrape_verbalcommits_worker.py                              │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │  from bs4 import BeautifulSoup                               │        │
│  │  import requests                                              │        │
│  │                                                               │        │
│  │  # Scrape portal entries                                     │        │
│  │  response = requests.get(                                    │        │
│  │      'https://verbalcommits.com/transfers/basketball'        │        │
│  │  )                                                            │        │
│  │  soup = BeautifulSoup(response.text, 'html.parser')          │        │
│  │                                                               │        │
│  │  # Parse table                                               │        │
│  │  transfers = parse_transfer_table(soup)                      │        │
│  │                                                               │        │
│  │  # Detect new entrants (not in DB)                           │        │
│  │  new_entrants = [t for t in transfers                        │        │
│  │                  if not exists_in_db(t['player_name'])]      │        │
│  │                                                               │        │
│  │  # Save to S3 + trigger alerts                               │        │
│  │  s3.put_object(...)                                          │        │
│  │  if new_entrants:                                            │        │
│  │      trigger_agent_analysis.delay(new_entrants)              │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌───────────────────────────────────────────────────────────────────────────┐
│  Task 1.2: Data Validation (Pydantic Schemas)                            │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  data_validator.py                                           │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │  from pydantic import BaseModel, validator                   │        │
│  │                                                               │        │
│  │  class GameSchema(BaseModel):                                │        │
│  │      game_id: str                                            │        │
│  │      date: date                                              │        │
│  │      home_team_id: int                                       │        │
│  │      away_team_id: int                                       │        │
│  │      home_score: int                                         │        │
│  │      away_score: int                                         │        │
│  │                                                               │        │
│  │      @validator('home_score', 'away_score')                  │        │
│  │      def score_must_be_positive(cls, v):                     │        │
│  │          if v < 0:                                           │        │
│  │              raise ValueError('Score cannot be negative')    │        │
│  │          return v                                            │        │
│  │                                                               │        │
│  │  class PlayerStatSchema(BaseModel):                          │        │
│  │      player_id: int                                          │        │
│  │      game_id: str                                            │        │
│  │      minutes_played: float                                   │        │
│  │      points: int                                             │        │
│  │      rebounds: int                                           │        │
│  │      assists: int                                            │        │
│  │                                                               │        │
│  │      @validator('minutes_played')                            │        │
│  │      def minutes_valid_range(cls, v):                        │        │
│  │          if not (0 <= v <= 50):                              │        │
│  │              raise ValueError('Invalid minutes')             │        │
│  │          return v                                            │        │
│  │                                                               │        │
│  │  # Validate all records                                      │        │
│  │  validated_records = []                                      │        │
│  │  errors = []                                                 │        │
│  │                                                               │        │
│  │  for record in raw_data:                                     │        │
│  │      try:                                                    │        │
│  │          validated = PlayerStatSchema(**record)              │        │
│  │          validated_records.append(validated)                 │        │
│  │      except ValidationError as e:                            │        │
│  │          errors.append({'record': record, 'error': str(e)})  │        │
│  │                                                               │        │
│  │  # Log errors for investigation                              │        │
│  │  if errors:                                                  │        │
│  │      log_validation_errors(errors)                           │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌───────────────────────────────────────────────────────────────────────────┐
│  Task 1.3: Data Cleaning & Normalization                                 │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  data_cleaner.py                                             │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │  import pandas as pd                                         │        │
│  │  import numpy as np                                          │        │
│  │                                                               │        │
│  │  # Load validated data                                       │        │
│  │  df = pd.DataFrame(validated_records)                        │        │
│  │                                                               │        │
│  │  # Handle missing values                                     │        │
│  │  df['three_point_pct'].fillna(0.0, inplace=True)            │        │
│  │  df['blocks'].fillna(df['blocks'].median(), inplace=True)   │        │
│  │                                                               │        │
│  │  # Outlier detection and capping                             │        │
│  │  def cap_outliers(series, n_std=3):                          │        │
│  │      mean = series.mean()                                    │        │
│  │      std = series.std()                                      │        │
│  │      lower = mean - n_std * std                              │        │
│  │      upper = mean + n_std * std                              │        │
│  │      return series.clip(lower, upper)                        │        │
│  │                                                               │        │
│  │  df['points'] = cap_outliers(df['points'])                   │        │
│  │                                                               │        │
│  │  # Normalize player names (remove suffixes, standardize)     │        │
│  │  df['player_name'] = df['player_name'].str.strip()           │        │
│  │  df['player_name'] = df['player_name'].str.replace(          │        │
│  │      r' (Jr\.|Sr\.|III|IV)$', '', regex=True                │        │
│  │  )                                                            │        │
│  │                                                               │        │
│  │  # Standardize team names (abbreviations)                    │        │
│  │  team_mapping = {                                            │        │
│  │      'UNC': 'North Carolina',                                │        │
│  │      'UK': 'Kentucky',                                       │        │
│  │      # ... full mapping                                      │        │
│  │  }                                                            │        │
│  │  df['team'] = df['team'].map(team_mapping)                   │        │
│  │                                                               │        │
│  │  # Data quality flags                                        │        │
│  │  df['quality_flag'] = 'GOOD'                                 │        │
│  │  df.loc[df['minutes_played'] < 5, 'quality_flag'] = 'LOW'   │        │
│  │                                                               │        │
│  │  # Save to staging area                                      │        │
│  │  df.to_parquet(                                              │        │
│  │      's3://portalpoint-processed-data/staging/...'            │        │
│  │  )                                                            │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌───────────────────────────────────────────────────────────────────────────┐
│  Task 1.4: Load to PostgreSQL (Upsert)                                   │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  data_loader.py                                              │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │  from sqlalchemy import create_engine                        │        │
│  │  from sqlalchemy.dialects.postgresql import insert          │        │
│  │                                                               │        │
│  │  engine = create_engine(POSTGRES_CONNECTION_STRING)          │        │
│  │                                                               │        │
│  │  # Upsert pattern (insert or update on conflict)             │        │
│  │  stmt = insert(player_season_stats).values(df.to_dict(...)) │        │
│  │  stmt = stmt.on_conflict_do_update(                          │        │
│  │      index_elements=['player_id', 'season'],                 │        │
│  │      set_=dict(                                              │        │
│  │          points_per_game=stmt.excluded.points_per_game,      │        │
│  │          # ... update all columns                            │        │
│  │          updated_at=datetime.utcnow()                        │        │
│  │      )                                                        │        │
│  │  )                                                            │        │
│  │                                                               │        │
│  │  with engine.begin() as conn:                                │        │
│  │      conn.execute(stmt)                                      │        │
│  │                                                               │        │
│  │  # Update metadata table                                     │        │
│  │  update_last_ingestion_timestamp('player_stats', datetime.now())     │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║                    STAGE 2: FEATURE ENGINEERING                           ║
║                  (Airflow Task: transform_features)                       ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────────────────────────────┐
│  PIPELINE 2.1: Player Statistical Features                                │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  Input: Raw player stats from player_season_stats table                  │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  player_feature_engineering.py                               │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │  import pandas as pd                                         │        │
│  │  import numpy as np                                          │        │
│  │                                                               │        │
│  │  # Load data                                                 │        │
│  │  query = """                                                 │        │
│  │      SELECT * FROM player_season_stats                       │        │
│  │      WHERE season >= 2020                                    │        │
│  │  """                                                          │        │
│  │  df = pd.read_sql(query, engine)                             │        │
│  │                                                               │        │
│  │  # FEATURE 1: True Shooting Percentage                       │        │
│  │  df['ts_pct'] = (                                            │        │
│  │      df['points'] /                                          │        │
│  │      (2 * (df['fga'] + 0.44 * df['fta']))                   │        │
│  │  )                                                            │        │
│  │                                                               │        │
│  │  # FEATURE 2: Usage Rate                                     │        │
│  │  df['usage_rate'] = 100 * (                                  │        │
│  │      (df['fga'] + 0.44 * df['fta'] + df['tov']) *           │        │
│  │      (df['team_minutes'] / 5)                                │        │
│  │  ) / (                                                        │        │
│  │      df['minutes'] *                                         │        │
│  │      (df['team_fga'] + 0.44 * df['team_fta'] + df['team_tov'])      │
│  │  )                                                            │        │
│  │                                                               │        │
│  │  # FEATURE 3: Per-100 Possession Stats                       │        │
│  │  df['possessions'] = (                                       │        │
│  │      df['fga'] + 0.44 * df['fta'] -                         │        │
│  │      df['orb'] + df['tov']                                   │        │
│  │  )                                                            │        │
│  │  df['points_per_100'] = (df['points'] / df['possessions']) * 100    │
│  │  df['assists_per_100'] = (df['assists'] / df['possessions']) * 100  │
│  │                                                               │        │
│  │  # FEATURE 4: Shot Distribution                              │        │
│  │  # Requires play-by-play data                                │        │
│  │  pbp = load_play_by_play_data()                              │        │
│  │  shots = pbp[pbp['event_type'] == 'shot']                    │        │
│  │                                                               │        │
│  │  shot_dist = shots.groupby('player_id').apply(               │        │
│  │      lambda x: {                                             │        │
│  │          'rim_freq': (x['shot_zone'] == 'rim').mean(),      │        │
│  │          'mid_freq': (x['shot_zone'] == 'mid').mean(),      │        │
│  │          'three_freq': (x['shot_zone'] == 'three').mean()   │        │
│  │      }                                                        │        │
│  │  )                                                            │        │
│  │  df = df.join(pd.DataFrame(shot_dist.tolist()), on='player_id')     │
│  │                                                               │        │
│  │  # FEATURE 5: Creation vs. Spot-Up                           │        │
│  │  df['assisted_fg_pct'] = (                                   │        │
│  │      df['assisted_fgm'] / df['fgm']                          │        │
│  │  )                                                            │        │
│  │  df['creation_rate'] = 1 - df['assisted_fg_pct']            │        │
│  │                                                               │        │
│  │  # FEATURE 6: Player Efficiency Rating (PER)                 │        │
│  │  df['per'] = calculate_per(df)  # Complex formula           │        │
│  │                                                               │        │
│  │  # FEATURE 7: Box Plus/Minus (BPM)                           │        │
│  │  df['bpm'] = calculate_bpm(df)  # Regression-based metric   │        │
│  │                                                               │        │
│  │  # FEATURE 8: Positional Adjustments                         │        │
│  │  position_averages = df.groupby('position').mean()           │        │
│  │  df['pts_vs_position'] = (                                   │        │
│  │      df['points_per_game'] -                                 │        │
│  │      df['position'].map(position_averages['points_per_game'])        │
│  │  )                                                            │        │
│  │                                                               │        │
│  │  # FEATURE 9: Consistency (Variance)                         │        │
│  │  game_log = load_game_logs()                                 │        │
│  │  df['points_variance'] = game_log.groupby('player_id')       │        │
│  │      ['points'].std()                                        │        │
│  │                                                               │        │
│  │  # FEATURE 10: Trend (Improvement)                           │        │
│  │  # Compare last 10 games vs. first 10 games                  │        │
│  │  df['improvement_rate'] = calculate_trend(game_log)          │        │
│  │                                                               │        │
│  │  # Save engineered features                                  │        │
│  │  df.to_sql(                                                  │        │
│  │      'player_features',                                      │        │
│  │      engine,                                                 │        │
│  │      if_exists='replace',                                    │        │
│  │      index=False                                             │        │
│  │  )                                                            │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
│  Output: player_features table (30+ features per player-season)          │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌───────────────────────────────────────────────────────────────────────────┐
│  PIPELINE 2.2: Team System Features                                      │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  Input: Team-level stats from team_season_stats table                    │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  team_feature_engineering.py                                 │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │                                                               │        │
│  │  # Load team data                                            │        │
│  │  teams = pd.read_sql("SELECT * FROM team_season_stats", engine)      │
│  │                                                               │        │
│  │  # FEATURE 1: Pace (possessions per 40 minutes)              │        │
│  │  teams['pace'] = calculate_pace(teams)                       │        │
│  │                                                               │        │
│  │  # FEATURE 2: Offensive/Defensive Ratings                    │        │
│  │  teams['offensive_rating'] = (                               │        │
│  │      teams['points_scored'] / teams['possessions']           │        │
│  │  ) * 100                                                      │        │
│  │  teams['defensive_rating'] = (                               │        │
│  │      teams['points_allowed'] / teams['opp_possessions']      │        │
│  │  ) * 100                                                      │        │
│  │                                                               │        │
│  │  # FEATURE 3: Four Factors                                   │        │
│  │  teams['efg_pct'] = (                                        │        │
│  │      teams['fgm'] + 0.5 * teams['three_pm']                  │        │
│  │  ) / teams['fga']                                            │        │
│  │  teams['tov_pct'] = teams['tov'] / teams['possessions']     │        │
│  │  teams['orb_pct'] = teams['orb'] / (                         │        │
│  │      teams['orb'] + teams['opp_drb']                         │        │
│  │  )                                                            │        │
│  │  teams['ft_rate'] = teams['fta'] / teams['fga']             │        │
│  │                                                               │        │
│  │  # FEATURE 4: Shot Distribution (Team Style)                 │        │
│  │  pbp_team = load_team_play_by_play()                         │        │
│  │  teams['three_pt_rate'] = pbp_team.groupby('team_id').apply( │        │
│  │      lambda x: (x['shot_type'] == '3PT').sum() / len(x)     │        │
│  │  )                                                            │        │
│  │                                                               │        │
│  │  # FEATURE 5: Ball Movement (Assist Rate)                    │        │
│  │  teams['assist_rate'] = (                                    │        │
│  │      teams['assists'] / teams['fgm']                         │        │
│  │  ) * 100                                                      │        │
│  │                                                               │        │
│  │  # FEATURE 6: System Consistency (Coach)                     │        │
│  │  # Standard deviation of pace/style over coach's tenure      │        │
│  │  coach_history = teams.groupby('coach_id')                   │        │
│  │  teams['pace_consistency'] = coach_history['pace'].std()     │        │
│  │                                                               │        │
│  │  # Save                                                       │        │
│  │  teams.to_sql('team_features', engine, if_exists='replace')  │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌───────────────────────────────────────────────────────────────────────────┐
│  PIPELINE 2.3: Transfer Outcome Features                                  │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  Input: Transfer history + player stats before/after transfer            │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  transfer_feature_engineering.py                             │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │                                                               │        │
│  │  # Join transfer data with pre/post stats                    │        │
│  │  query = """                                                 │        │
│  │      SELECT                                                  │        │
│  │          t.transfer_id,                                      │        │
│  │          t.player_id,                                        │        │
│  │          t.from_school_id,                                   │        │
│  │          t.to_school_id,                                     │        │
│  │          pre.per as pre_per,                                 │        │
│  │          pre.minutes_per_game as pre_mpg,                    │        │
│  │          post.per as post_per,                               │        │
│  │          post.minutes_per_game as post_mpg                   │        │
│  │      FROM transfers t                                        │        │
│  │      JOIN player_features pre                                │        │
│  │          ON t.player_id = pre.player_id                      │        │
│  │          AND pre.season = t.season - 1                       │        │
│  │      JOIN player_features post                               │        │
│  │          ON t.player_id = post.player_id                     │        │
│  │          AND post.season = t.season                          │        │
│  │  """                                                          │        │
│  │  transfers = pd.read_sql(query, engine)                      │        │
│  │                                                               │        │
│  │  # FEATURE: Change in Performance                            │        │
│  │  transfers['per_change'] = (                                 │        │
│  │      transfers['post_per'] - transfers['pre_per']            │        │
│  │  )                                                            │        │
│  │  transfers['mpg_change'] = (                                 │        │
│  │      transfers['post_mpg'] - transfers['pre_mpg']            │        │
│  │  )                                                            │        │
│  │                                                               │        │
│  │  # FEATURE: Success Classification                           │        │
│  │  transfers['success'] = (                                    │        │
│  │      (transfers['per_change'] > 0) &                         │        │
│  │      (transfers['mpg_change'] > 5)                           │        │
│  │  ).astype(int)                                               │        │
│  │                                                               │        │
│  │  # Save                                                       │        │
│  │  transfers.to_sql(                                           │        │
│  │      'transfer_outcomes',                                    │        │
│  │      engine,                                                 │        │
│  │      if_exists='replace'                                     │        │
│  │  )                                                            │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────────┐
│  PIPELINE 2.4: Team Rating Projection Features                           │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  Input: Play-by-play data (already scraped) + team_season_stats          │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  team_rating_feature_engineering.py                          │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │  import pandas as pd                                         │        │
│  │  import numpy as np                                          │        │
│  │                                                               │        │
│  │  # FEATURE 1: Player on/off split differential               │        │
│  │  # Net rating (pts per 100 poss) when player is on vs. off  │        │
│  │  pbp = load_play_by_play_data()                              │        │
│  │  lineups = build_lineup_sequences(pbp)  # 5-man units       │        │
│  │                                                               │        │
│  │  on_off = lineups.groupby('player_id').apply(                │        │
│  │      lambda x: {                                             │        │
│  │          'net_rating_on': x[x['player_on']]['net_rating'].mean(),     │
│  │          'net_rating_off': x[~x['player_on']]['net_rating'].mean(),   │
│  │          'on_off_diff': (                                    │        │
│  │              x[x['player_on']]['net_rating'].mean() -        │        │
│  │              x[~x['player_on']]['net_rating'].mean()         │        │
│  │          )                                                    │        │
│  │      }                                                        │        │
│  │  )                                                            │        │
│  │  on_off_df = pd.DataFrame(on_off.tolist(),                   │        │
│  │                            index=on_off.index)               │        │
│  │                                                               │        │
│  │  # FEATURE 2: Team AdjEM baseline (offensive_rating -        │        │
│  │  # defensive_rating, already in team_season_stats)           │        │
│  │  teams = pd.read_sql("""                                     │        │
│  │      SELECT team_id, season, offensive_rating,               │        │
│  │             defensive_rating,                                │        │
│  │             (offensive_rating - defensive_rating) as adjEM   │        │
│  │      FROM team_season_stats                                  │        │
│  │  """, engine)                                                │        │
│  │                                                               │        │
│  │  # FEATURE 3: Minutes vacated at each position               │        │
│  │  # Sum of departing players' MPG (graduated + transferred out│        │
│  │  departing = pd.read_sql("""                                 │        │
│  │      SELECT p.team_id, p.position,                          │        │
│  │             SUM(s.minutes_per_game) as minutes_vacated       │        │
│  │      FROM players p                                          │        │
│  │      JOIN player_season_stats s ON p.player_id = s.player_id │        │
│  │      WHERE p.status IN ('graduated', 'transferred_out')      │        │
│  │      GROUP BY p.team_id, p.position                          │        │
│  │  """, engine)                                                │        │
│  │                                                               │        │
│  │  # FEATURE 4: Conference strength index                      │        │
│  │  conf_strength = teams.groupby('conference')['adjEM'].mean() │        │
│  │                                                               │        │
│  │  # Join all features                                         │        │
│  │  team_rating_features = (                                    │        │
│  │      on_off_df                                               │        │
│  │      .join(teams.set_index('team_id'), on='team_id')         │        │
│  │      .join(departing.set_index(['team_id', 'position']),     │        │
│  │            on=['team_id', 'position'])                        │        │
│  │  )                                                            │        │
│  │  team_rating_features['conference_strength'] = (             │        │
│  │      team_rating_features['conference'].map(conf_strength)   │        │
│  │  )                                                            │        │
│  │                                                               │        │
│  │  team_rating_features.to_sql(                                │        │
│  │      'team_rating_features', engine, if_exists='replace'     │        │
│  │  )                                                            │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
│  Output: team_rating_features table (on/off splits, AdjEM,              │
│          minutes_vacated, conference_strength per player-team-season)    │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║                  STAGE 3: MODEL TRAINING & EVALUATION                     ║
║             (Airflow DAG: weekly_model_training - Sundays)                ║
║  Note: project model numbering = M1 clustering, M2 team, M3 scheme,      ║
║  M5 transfer success, M6 team rating, M7 recommendations (M4=playing time)║
╚═══════════════════════════════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────────────────────────────┐
│  MODEL 1: Player Archetype Clustering (K-Means)                          │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  train_player_clustering.py                                  │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │  from sklearn.preprocessing import StandardScaler            │        │
│  │  from sklearn.decomposition import PCA                       │        │
│  │  from sklearn.cluster import KMeans                          │        │
│  │  import mlflow                                               │        │
│  │                                                               │        │
│  │  # Start MLflow run                                          │        │
│  │  with mlflow.start_run(run_name="player_clustering"):        │        │
│  │                                                               │        │
│  │      # Load features                                         │        │
│  │      features = [                                            │        │
│  │          'ts_pct', 'three_freq', 'usage_rate',              │        │
│  │          'assisted_fg_pct', 'height', 'rebound_rate',       │        │
│  │          'assist_rate', 'steal_rate', 'block_rate'          │        │
│  │      ]                                                        │        │
│  │      X = df[features].values                                 │        │
│  │                                                               │        │
│  │      # Standardize                                           │        │
│  │      scaler = StandardScaler()                               │        │
│  │      X_scaled = scaler.fit_transform(X)                      │        │
│  │                                                               │        │
│  │      # PCA                                                    │        │
│  │      pca = PCA(n_components=10, random_state=42)             │        │
│  │      X_pca = pca.fit_transform(X_scaled)                     │        │
│  │                                                               │        │
│  │      # Log PCA explained variance                            │        │
│  │      mlflow.log_metric(                                      │        │
│  │          "pca_explained_variance",                           │        │
│  │          pca.explained_variance_ratio_.sum()                 │        │
│  │      )                                                        │        │
│  │                                                               │        │
│  │      # Determine optimal K                                   │        │
│  │      from sklearn.metrics import silhouette_score            │        │
│  │      silhouettes = []                                        │        │
│  │      K_range = range(6, 15)                                  │        │
│  │                                                               │        │
│  │      for k in K_range:                                       │        │
│  │          kmeans = KMeans(n_clusters=k, random_state=42)      │        │
│  │          labels = kmeans.fit_predict(X_pca)                  │        │
│  │          sil = silhouette_score(X_pca, labels)               │        │
│  │          silhouettes.append(sil)                             │        │
│  │                                                               │        │
│  │      optimal_k = K_range[np.argmax(silhouettes)]             │        │
│  │      mlflow.log_param("optimal_k", optimal_k)                │        │
│  │                                                               │        │
│  │      # Train final model                                     │        │
│  │      kmeans_final = KMeans(                                  │        │
│  │          n_clusters=optimal_k,                               │        │
│  │          random_state=42,                                    │        │
│  │          n_init=20                                           │        │
│  │      )                                                        │        │
│  │      clusters = kmeans_final.fit_predict(X_pca)              │        │
│  │                                                               │        │
│  │      # Assign to dataframe                                   │        │
│  │      df['archetype_id'] = clusters                           │        │
│  │                                                               │        │
│  │      # Manually label clusters (based on centroids)          │        │
│  │      archetype_labels = {                                    │        │
│  │          0: "Elite Ball-Handler/Scorer",                     │        │
│  │          1: "3&D Wing",                                      │        │
│  │          2: "Stretch 4/5",                                   │        │
│  │          # ... etc                                           │        │
│  │      }                                                        │        │
│  │      df['archetype_label'] = df['archetype_id'].map(         │        │
│  │          archetype_labels                                    │        │
│  │      )                                                        │        │
│  │                                                               │        │
│  │      # Save model artifacts                                  │        │
│  │      import joblib                                           │        │
│  │      joblib.dump(scaler, 'scaler.pkl')                       │        │
│  │      joblib.dump(pca, 'pca.pkl')                             │        │
│  │      joblib.dump(kmeans_final, 'kmeans.pkl')                 │        │
│  │                                                               │        │
│  │      mlflow.log_artifact('scaler.pkl')                       │        │
│  │      mlflow.log_artifact('pca.pkl')                          │        │
│  │      mlflow.log_artifact('kmeans.pkl')                       │        │
│  │                                                               │        │
│  │      # Register model                                        │        │
│  │      mlflow.sklearn.log_model(                               │        │
│  │          kmeans_final,                                       │        │
│  │          "model",                                            │        │
│  │          registered_model_name="player_clusterer"            │        │
│  │      )                                                        │        │
│  │                                                               │        │
│  │      # Save cluster assignments to DB                        │        │
│  │      df[['player_id', 'season', 'archetype_id',             │        │
│  │          'archetype_label']].to_sql(                         │        │
│  │          'player_archetypes',                                │        │
│  │          engine,                                             │        │
│  │          if_exists='replace'                                 │        │
│  │      )                                                        │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌───────────────────────────────────────────────────────────────────────────┐
│  MODEL 2: Transfer Success Prediction (XGBoost)                          │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  train_transfer_success_model.py                             │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │  import xgboost as xgb                                       │        │
│  │  from sklearn.model_selection import TimeSeriesSplit         │        │
│  │  from sklearn.metrics import mean_squared_error, r2_score   │        │
│  │  import mlflow                                               │        │
│  │                                                               │        │
│  │  with mlflow.start_run(run_name="transfer_success_xgboost"): │        │
│  │                                                               │        │
│  │      # Load training data                                    │        │
│  │      query = """                                             │        │
│  │          SELECT                                              │        │
│  │              t.transfer_id,                                  │        │
│  │              pf_pre.*,                                       │        │
│  │              tf_from.*,                                      │        │
│  │              tf_to.*,                                        │        │
│  │              t.per_change as target_per_change,              │        │
│  │              t.mpg_change as target_mpg_change               │        │
│  │          FROM transfer_outcomes t                            │        │
│  │          JOIN player_features pf_pre                         │        │
│  │              ON t.player_id = pf_pre.player_id               │        │
│  │          JOIN team_features tf_from                          │        │
│  │              ON t.from_school_id = tf_from.team_id           │        │
│  │          JOIN team_features tf_to                            │        │
│  │              ON t.to_school_id = tf_to.team_id               │        │
│  │          WHERE t.season BETWEEN 2020 AND 2024                │        │
│  │      """                                                      │        │
│  │      df = pd.read_sql(query, engine)                         │        │
│  │                                                               │        │
│  │      # Feature selection                                     │        │
│  │      feature_cols = [                                        │        │
│  │          # Player features                                   │        │
│  │          'per', 'usage_rate', 'ts_pct', 'three_freq',       │        │
│  │          'assisted_fg_pct', 'creation_rate',                 │        │
│  │          # Source school features                            │        │
│  │          'from_pace', 'from_offensive_rating',               │        │
│  │          'from_three_pt_rate',                               │        │
│  │          # Destination school features                       │        │
│  │          'to_pace', 'to_offensive_rating',                   │        │
│  │          'to_three_pt_rate', 'to_roster_depth',              │        │
│  │          # Differentials                                     │        │
│  │          'pace_diff', 'style_diff', 'conference_strength_diff'        │
│  │      ]                                                        │        │
│  │                                                               │        │
│  │      X = df[feature_cols].values                             │        │
│  │      y_per = df['target_per_change'].values                  │        │
│  │      y_mpg = df['target_mpg_change'].values                  │        │
│  │                                                               │        │
│  │      # Time series split (respects temporal order)           │        │
│  │      tscv = TimeSeriesSplit(n_splits=5)                      │        │
│  │                                                               │        │
│  │      # Train model for PER prediction                        │        │
│  │      cv_scores = []                                          │        │
│  │                                                               │        │
│  │      for train_idx, val_idx in tscv.split(X):                │        │
│  │          X_train, X_val = X[train_idx], X[val_idx]           │        │
│  │          y_train, y_val = y_per[train_idx], y_per[val_idx]   │        │
│  │                                                               │        │
│  │          model = xgb.XGBRegressor(                           │        │
│  │              n_estimators=200,                               │        │
│  │              max_depth=6,                                    │        │
│  │              learning_rate=0.05,                             │        │
│  │              subsample=0.8,                                  │        │
│  │              colsample_bytree=0.8,                           │        │
│  │              random_state=42                                 │        │
│  │          )                                                    │        │
│  │                                                               │        │
│  │          model.fit(                                          │        │
│  │              X_train, y_train,                               │        │
│  │              eval_set=[(X_val, y_val)],                      │        │
│  │              early_stopping_rounds=20,                       │        │
│  │              verbose=False                                   │        │
│  │          )                                                    │        │
│  │                                                               │        │
│  │          y_pred = model.predict(X_val)                       │        │
│  │          r2 = r2_score(y_val, y_pred)                        │        │
│  │          rmse = np.sqrt(mean_squared_error(y_val, y_pred))   │        │
│  │          cv_scores.append({'r2': r2, 'rmse': rmse})          │        │
│  │                                                               │        │
│  │      # Log cross-validation results                          │        │
│  │      avg_r2 = np.mean([s['r2'] for s in cv_scores])          │        │
│  │      avg_rmse = np.mean([s['rmse'] for s in cv_scores])      │        │
│  │      mlflow.log_metric("cv_r2", avg_r2)                      │        │
│  │      mlflow.log_metric("cv_rmse", avg_rmse)                  │        │
│  │                                                               │        │
│  │      # Train final model on all data                         │        │
│  │      final_model = xgb.XGBRegressor(                         │        │
│  │          n_estimators=200,                                   │        │
│  │          max_depth=6,                                        │        │
│  │          learning_rate=0.05,                                 │        │
│  │          subsample=0.8,                                      │        │
│  │          colsample_bytree=0.8,                               │        │
│  │          random_state=42                                     │        │
│  │      )                                                        │        │
│  │      final_model.fit(X, y_per)                               │        │
│  │                                                               │        │
│  │      # Feature importance                                    │        │
│  │      importance = pd.DataFrame({                             │        │
│  │          'feature': feature_cols,                            │        │
│  │          'importance': final_model.feature_importances_      │        │
│  │      }).sort_values('importance', ascending=False)            │        │
│  │                                                               │        │
│  │      mlflow.log_dict(                                        │        │
│  │          importance.to_dict(),                               │        │
│  │          "feature_importance.json"                           │        │
│  │      )                                                        │        │
│  │                                                               │        │
│  │      # Save model                                            │        │
│  │      mlflow.xgboost.log_model(                               │        │
│  │          final_model,                                        │        │
│  │          "model",                                            │        │
│  │          registered_model_name="transfer_success_predictor"  │        │
│  │      )                                                        │        │
│  │                                                               │        │
│  │      # Compare to baseline (last season's PER)               │        │
│  │      baseline_pred = df['per']  # Just use current PER       │        │
│  │      baseline_rmse = np.sqrt(                                │        │
│  │          mean_squared_error(y_per, baseline_pred)            │        │
│  │      )                                                        │        │
│  │      improvement = (baseline_rmse - avg_rmse) / baseline_rmse * 100   │        │
│  │      mlflow.log_metric("improvement_vs_baseline_pct", improvement)    │        │
│  │                                                               │        │
│  │      # Promote to production if improvement > 5%             │        │
│  │      if improvement > 5:                                     │        │
│  │          client = mlflow.tracking.MlflowClient()             │        │
│  │          client.transition_model_version_stage(              │        │
│  │              name="transfer_success_predictor",              │        │
│  │              version=get_latest_version(),                   │        │
│  │              stage="Production"                              │        │
│  │          )                                                    │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌───────────────────────────────────────────────────────────────────────────┐
│  MODEL 3: NIL Budget Fit Model (Gradient Boosting)                       │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  train_nil_valuation_model.py                                │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │  from sklearn.ensemble import GradientBoostingRegressor      │        │
│  │  import mlflow                                               │        │
│  │                                                               │        │
│  │  with mlflow.start_run(run_name="nil_budget_fit_gbm"):        │        │
│  │                                                               │        │
│  │      # Load NIL budget fit data                              │        │
│  │      query = """                                             │        │
│  │          SELECT                                              │        │
│  │              n.player_id,                                    │        │
│  │              n.program_id,                                   │        │
│  │              n.nil_fit_score,                                │        │
│  │              pf.per,                                         │        │
│  │              pf.usage_rate,                                  │        │
│  │              pf.position,                                    │        │
│  │              p.social_media_followers,                       │        │
│  │              t.nil_pool_size,                                │        │
│  │              t.market_size,                                  │        │
│  │              c.tv_deal_value                                 │        │
│  │          FROM nil_budget_data n                              │        │
│  │          JOIN player_features pf ON n.player_id = pf.player_id        │        │
│  │          JOIN players p ON n.player_id = p.player_id         │        │
│  │          JOIN teams t ON n.program_id = t.team_id            │        │
│  │          JOIN conferences c ON t.conference = c.name         │        │
│  │      """                                                      │        │
│  │      df = pd.read_sql(query, engine)                         │        │
│  │                                                               │        │
│  │      # Feature engineering                                   │        │
│  │      feature_cols = [                                        │        │
│  │          'per', 'usage_rate', 'social_media_followers',      │        │
│  │          'nil_pool_size', 'market_size', 'tv_deal_value'     │        │
│  │      ]                                                        │        │
│  │      # One-hot encode position                               │        │
│  │      position_dummies = pd.get_dummies(                      │        │
│  │          df['position'],                                     │        │
│  │          prefix='pos'                                        │        │
│  │      )                                                        │        │
│  │      X = pd.concat([df[feature_cols], position_dummies], axis=1)      │        │
│  │      y = df['nil_valuation'].values                          │        │
│  │                                                               │        │
│  │      # Train/test split                                      │        │
│  │      from sklearn.model_selection import train_test_split    │        │
│  │      X_train, X_test, y_train, y_test = train_test_split(    │        │
│  │          X, y, test_size=0.2, random_state=42                │        │
│  │      )                                                        │        │
│  │                                                               │        │
│  │      # Train model                                           │        │
│  │      model = GradientBoostingRegressor(                      │        │
│  │          n_estimators=150,                                   │        │
│  │          max_depth=5,                                        │        │
│  │          learning_rate=0.1,                                  │        │
│  │          random_state=42                                     │        │
│  │      )                                                        │        │
│  │      model.fit(X_train, y_train)                             │        │
│  │                                                               │        │
│  │      # Evaluate                                              │        │
│  │      y_pred = model.predict(X_test)                          │        │
│  │      r2 = r2_score(y_test, y_pred)                           │        │
│  │      rmse = np.sqrt(mean_squared_error(y_test, y_pred))      │        │
│  │                                                               │        │
│  │      mlflow.log_metric("test_r2", r2)                        │        │
│  │      mlflow.log_metric("test_rmse", rmse)                    │        │
│  │                                                               │        │
│  │      # Log model                                             │        │
│  │      mlflow.sklearn.log_model(                               │        │
│  │          model,                                              │        │
│  │          "model",                                            │        │
│  │          registered_model_name="nil_budget_fit_model"        │        │
│  │      )                                                        │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌───────────────────────────────────────────────────────────────────────────┐
│  MODEL 4: Collaborative Filtering (Matrix Factorization)                 │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  train_collaborative_filter.py                               │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │  from sklearn.decomposition import TruncatedSVD              │        │
│  │  from scipy.sparse import csr_matrix                         │        │
│  │  import mlflow                                               │        │
│  │                                                               │        │
│  │  with mlflow.start_run(run_name="collaborative_filter_svd"): │        │
│  │                                                               │        │
│  │      # Load historical recruitments (program × player)       │        │
│  │      transfers = pd.read_sql(                                │        │
│  │          "SELECT to_school_id, player_id, success FROM transfer_outcomes",   │        │
│  │          engine                                              │        │
│  │      )                                                        │        │
│  │                                                               │        │
│  │      # Create sparse interaction matrix (Program × Player)   │        │
│  │      program_ids = transfers['to_school_id'].unique()         │        │
│  │      player_ids = transfers['player_id'].unique()            │        │
│  │                                                               │        │
│  │      program_to_idx = {pid: i for i, pid in enumerate(program_ids)}  │        │
│  │      player_to_idx = {pid: i for i, pid in enumerate(player_ids)}    │        │
│  │                                                               │        │
│  │      rows = [program_to_idx[pid]                             │        │
│  │               for pid in transfers['to_school_id']]          │        │
│  │      cols = [player_to_idx[pid]                              │        │
│  │               for pid in transfers['player_id']]             │        │
│  │      data = transfers['success'].values * 5  # Scale 0-5     │        │
│  │                                                               │        │
│  │      interaction_matrix = csr_matrix(                        │        │
│  │          (data, (rows, cols)),                               │        │
│  │          shape=(len(program_ids), len(player_ids))           │        │
│  │      )                                                        │        │
│  │                                                               │        │
│  │      # SVD                                                    │        │
│  │      n_factors = 50                                          │        │
│  │      svd = TruncatedSVD(                                     │        │
│  │          n_components=n_factors,                             │        │
│  │          random_state=42                                     │        │
│  │      )                                                        │        │
│  │      program_factors = svd.fit_transform(interaction_matrix)  │        │
│  │      player_factors = svd.components_.T                      │        │
│  │                                                               │        │
│  │      # Log explained variance                                │        │
│  │      mlflow.log_metric(                                      │        │
│  │          "explained_variance_ratio",                         │        │
│  │          svd.explained_variance_ratio_.sum()                 │        │
│  │      )                                                        │        │
│  │                                                               │        │
│  │      # Save artifacts                                        │        │
│  │      np.save('program_factors.npy', program_factors)          │        │
│  │      np.save('player_factors.npy', player_factors)           │        │
│  │      joblib.dump(program_to_idx, 'program_to_idx.pkl')       │        │
│  │      joblib.dump(player_to_idx, 'player_to_idx.pkl')         │        │
│  │                                                               │        │
│  │      mlflow.log_artifact('program_factors.npy')              │        │
│  │      mlflow.log_artifact('player_factors.npy')               │        │
│  │      mlflow.log_artifact('program_to_idx.pkl')               │        │
│  │      mlflow.log_artifact('player_to_idx.pkl')                │        │
│  │                                                               │        │
│  │      # Register model                                        │        │
│  │      mlflow.sklearn.log_model(                               │        │
│  │          svd,                                                │        │
│  │          "model",                                            │        │
│  │          registered_model_name="collaborative_filter"        │        │
│  │      )                                                        │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────────┐
│  MODEL 5: Team Rating Projection (XGBoost)                               │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  train_team_rating_model.py                                  │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │  import xgboost as xgb                                       │        │
│  │  from sklearn.model_selection import TimeSeriesSplit         │        │
│  │  from sklearn.metrics import mean_squared_error              │        │
│  │  import mlflow                                               │        │
│  │                                                               │        │
│  │  with mlflow.start_run(run_name="team_rating_projection"):   │        │
│  │                                                               │        │
│  │      # Join transfers with pre-transfer team rating features │        │
│  │      # and the actual post-transfer team AdjEM (target)      │        │
│  │      query = """                                             │        │
│  │          SELECT                                              │        │
│  │              t.transfer_id,                                  │        │
│  │              t.player_id,                                    │        │
│  │              t.to_school_id,                                 │        │
│  │              pf.per,                                         │        │
│  │              pf.usage_rate,                                  │        │
│  │              pa.archetype_id,                                │        │
│  │              trf.adjEM          as team_adjEM_before,        │        │
│  │              trf.offensive_rating,                           │        │
│  │              trf.defensive_rating,                           │        │
│  │              trf.conference_strength,                        │        │
│  │              trf.minutes_vacated,                            │        │
│  │              trf.on_off_diff,                                │        │
│  │              -- expected minutes from Bayesian model output  │        │
│  │              bmp.expected_minutes,                           │        │
│  │              -- target: actual team AdjEM change that season │        │
│  │              (tra.adjEM - trf.adjEM) as delta_adjEM          │        │
│  │          FROM transfers t                                    │        │
│  │          JOIN player_features pf                             │        │
│  │              ON t.player_id = pf.player_id                   │        │
│  │              AND pf.season = t.season - 1                    │        │
│  │          JOIN player_archetypes pa                           │        │
│  │              ON t.player_id = pa.player_id                   │        │
│  │              AND pa.season = t.season - 1                    │        │
│  │          JOIN team_rating_features trf                       │        │
│  │              ON t.to_school_id = trf.team_id                 │        │
│  │              AND trf.season = t.season - 1                   │        │
│  │          JOIN team_rating_features tra                       │        │
│  │              ON t.to_school_id = tra.team_id                 │        │
│  │              AND tra.season = t.season                       │        │
│  │          JOIN bayesian_minutes_predictions bmp               │        │
│  │              ON t.player_id = bmp.player_id                  │        │
│  │              AND t.to_school_id = bmp.team_id                │        │
│  │          WHERE t.season BETWEEN 2020 AND 2024                │        │
│  │      """                                                      │        │
│  │      df = pd.read_sql(query, engine)                         │        │
│  │                                                               │        │
│  │      feature_cols = [                                        │        │
│  │          'per', 'usage_rate', 'archetype_id',               │        │
│  │          'team_adjEM_before', 'offensive_rating',            │        │
│  │          'defensive_rating', 'conference_strength',          │        │
│  │          'minutes_vacated', 'on_off_diff', 'expected_minutes'│        │
│  │      ]                                                        │        │
│  │      X = df[feature_cols].values                             │        │
│  │      y = df['delta_adjEM'].values                            │        │
│  │                                                               │        │
│  │      # Temporal cross-validation                             │        │
│  │      tscv = TimeSeriesSplit(n_splits=5)                      │        │
│  │      cv_rmse = []                                            │        │
│  │                                                               │        │
│  │      for train_idx, val_idx in tscv.split(X):                │        │
│  │          X_train, X_val = X[train_idx], X[val_idx]           │        │
│  │          y_train, y_val = y[train_idx], y[val_idx]           │        │
│  │                                                               │        │
│  │          model = xgb.XGBRegressor(                           │        │
│  │              n_estimators=150,                               │        │
│  │              max_depth=5,                                    │        │
│  │              learning_rate=0.05,                             │        │
│  │              subsample=0.8,                                  │        │
│  │              random_state=42                                 │        │
│  │          )                                                    │        │
│  │          model.fit(X_train, y_train,                         │        │
│  │                    eval_set=[(X_val, y_val)],                │        │
│  │                    early_stopping_rounds=15,                 │        │
│  │                    verbose=False)                            │        │
│  │                                                               │        │
│  │          y_pred = model.predict(X_val)                       │        │
│  │          rmse = np.sqrt(mean_squared_error(y_val, y_pred))   │        │
│  │          cv_rmse.append(rmse)                                │        │
│  │                                                               │        │
│  │      avg_rmse = np.mean(cv_rmse)                             │        │
│  │      mlflow.log_metric("cv_rmse_adjEM", avg_rmse)            │        │
│  │                                                               │        │
│  │      # Train final model on all data                         │        │
│  │      final_model = xgb.XGBRegressor(                         │        │
│  │          n_estimators=150, max_depth=5,                      │        │
│  │          learning_rate=0.05, random_state=42                 │        │
│  │      )                                                        │        │
│  │      final_model.fit(X, y)                                   │        │
│  │                                                               │        │
│  │      # Feature importance                                    │        │
│  │      importance = pd.DataFrame({                             │        │
│  │          'feature': feature_cols,                            │        │
│  │          'importance': final_model.feature_importances_      │        │
│  │      }).sort_values('importance', ascending=False)            │        │
│  │      mlflow.log_dict(importance.to_dict(),                   │        │
│  │                      "feature_importance.json")              │        │
│  │                                                               │        │
│  │      mlflow.xgboost.log_model(                               │        │
│  │          final_model, "model",                               │        │
│  │          registered_model_name="team_rating_projector"       │        │
│  │      )                                                        │        │
│  │                                                               │        │
│  │      # Promote if RMSE improves > 5% vs. current production  │        │
│  │      if check_improvement(avg_rmse, "team_rating_projector") > 0.05:   │
│  │          promote_model("team_rating_projector")              │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║                  STAGE 4: MODEL DEPLOYMENT & SERVING                      ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────────────────────────────┐
│  Deployment Pipeline (Triggered after model training)                     │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  1. Model Evaluation Check                                               │
│     → Compare new model vs. current production model                      │
│     → If improvement > 5%: proceed to deployment                         │
│     → If not: keep current model, log metrics                            │
│                                                                           │
│  2. Promote to MLflow Production Stage                                   │
│     → MLflow API call: transition_model_version_stage()                  │
│     → Tags model with: version, timestamp, metrics                       │
│                                                                           │
│  3. Build Docker Image with New Model                                    │
│     → Dockerfile pulls model from MLflow artifact store                  │
│     → Includes FastAPI serving code                                      │
│     → Builds and pushes to AWS ECR                                       │
│                                                                           │
│  4. Update ECS Task Definition                                           │
│     → New task definition with updated image tag                         │
│     → Environment variables (model version, config)                      │
│                                                                           │
│  5. Blue-Green Deployment                                                │
│     → Deploy new task definition to ECS                                  │
│     → Health checks (predict on test samples)                            │
│     → Gradual traffic shift: 10% → 50% → 100%                           │
│     → Automatic rollback if error rate spikes                            │
│                                                                           │
│  6. Post-Deployment Validation                                           │
│     → Run smoke tests                                                    │
│     → Monitor latency, error rate                                        │
│     → Compare predictions to baseline                                    │
│     → Notify Slack #ml-deployments                                       │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌───────────────────────────────────────────────────────────────────────────┐
│  Model Serving API (FastAPI)                                             │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  model_serving_app.py                                        │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │  from fastapi import FastAPI, HTTPException                  │        │
│  │  import mlflow.pyfunc                                        │        │
│  │  import joblib                                               │        │
│  │  import numpy as np                                          │        │
│  │                                                               │        │
│  │  app = FastAPI()                                             │        │
│  │                                                               │        │
│  │  # Load models at startup                                    │        │
│  │  transfer_success_model = mlflow.pyfunc.load_model(          │        │
│  │      f"models:/transfer_success_predictor/Production"        │        │
│  │  )                                                            │        │
│  │  nil_model = mlflow.pyfunc.load_model(                       │        │
│  │      f"models:/nil_valuation_model/Production"               │        │
│  │  )                                                            │        │  │  player_clusterer = joblib.load('player_clusterer.pkl')     │        │
│  │  collab_filter = joblib.load('collaborative_filter.pkl')     │        │
│  │                                                               │        │
│  │  @app.post("/predict/transfer-success")                      │        │
│  │  async def predict_transfer_success(request: TransferRequest):        │
│  │      # Extract features from request                         │        │
│  │      features = extract_features(request)                    │        │
│  │                                                               │        │
│  │      # Predict                                               │        │
│  │      prediction = transfer_success_model.predict(features)   │        │
│  │                                                               │        │
│  │      # Generate confidence interval (if Bayesian)            │        │
│  │      confidence_low = prediction * 0.9                       │        │
│  │      confidence_high = prediction * 1.1                      │        │
│  │                                                               │        │
│  │      return {                                                │        │
│  │          "predicted_per_change": float(prediction[0]),       │        │
│  │          "confidence_interval": {                            │        │
│  │              "low": float(confidence_low),                   │        │
│  │              "high": float(confidence_high)                  │        │
│  │          },                                                   │        │
│  │          "model_version": get_model_version(),               │        │
│  │          "timestamp": datetime.utcnow().isoformat()          │        │
│  │      }                                                        │        │
│  │                                                               │        │
│  │  @app.post("/predict/nil-valuation")                         │        │
│  │  async def predict_nil(request: NILRequest):                 │        │
│  │      features = extract_nil_features(request)                │        │
│  │      prediction = nil_model.predict(features)                │        │
│  │                                                               │        │
│  │      return {                                                │        │
│  │          "predicted_nil_value": float(prediction[0]),        │        │
│  │          "range": {                                          │        │
│  │              "low": float(prediction[0] * 0.7),              │        │
│  │              "high": float(prediction[0] * 1.3)              │        │
│  │          }                                                    │        │
│  │      }                                                        │        │
│  │                                                               │        │
│  │  @app.get("/health")                                         │        │
│  │  async def health_check():                                   │        │
│  │      # Test model inference                                  │        │
│  │      test_features = get_test_features()                     │        │
│  │      try:                                                    │        │
│  │          _ = transfer_success_model.predict(test_features)   │        │
│  │          return {"status": "healthy", "models_loaded": True} │        │
│  │      except Exception as e:                                  │        │
│  │          raise HTTPException(status_code=500, detail=str(e)) │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘

╔═══════════════════════════════════════════════════════════════════════════╗
║                  STAGE 5: MONITORING & FEEDBACK LOOP                      ║
╚═══════════════════════════════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────────────────────────────┐
│  Model Performance Monitoring                                             │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │  monitor_model_performance.py (Daily Airflow Task)           │        │
│  │  ──────────────────────────────────────────────────────────  │        │
│  │                                                               │        │
│  │  # Track prediction accuracy over time                       │        │
│  │  def monitor_predictions():                                  │        │
│  │      # Get recent predictions                                │        │
│  │      recent_preds = pd.read_sql("""                          │        │
│  │          SELECT                                              │        │
│  │              p.player_id,                                    │        │
│  │              p.predicted_per_change,                         │        │
│  │              p.predicted_at,                                 │        │
│  │              t.actual_per_change,                            │        │
│  │              t.transfer_date                                 │        │
│  │          FROM predictions p                                  │        │
│  │          JOIN transfer_outcomes t                            │        │
│  │              ON p.player_id = t.player_id                    │        │
│  │          WHERE p.predicted_at > NOW() - INTERVAL '30 days'   │        │
│  │              AND t.transfer_date > p.predicted_at            │        │
│  │      """, engine)                                            │        │
│  │                                                               │        │
│  │      # Calculate actual vs. predicted                        │        │
│  │      rmse = np.sqrt(mean_squared_error(                      │        │
│  │          recent_preds['actual_per_change'],                  │        │
│  │          recent_preds['predicted_per_change']                │        │
│  │      ))                                                       │        │
│  │                                                               │        │
│  │      # Log to Prometheus                                     │        │
│  │      model_rmse_gauge.set(rmse)                              │        │
│  │                                                               │        │
│  │      # Alert if drift detected                               │        │
│  │      if rmse > BASELINE_RMSE * 1.2:                          │        │
│  │          send_alert(                                         │        │
│  │              "Model performance degradation detected",       │        │
│  │              f"RMSE: {rmse:.2f} (baseline: {BASELINE_RMSE:.2f})"     │        │
│  │          )                                                    │        │
│  │          # Trigger model retraining                          │        │
│  │          trigger_training_dag()                              │        │
│  │                                                               │        │
│  │  # Feature drift detection                                   │        │
│  │  def detect_feature_drift():                                 │        │
│  │      # Compare current feature distributions to training     │        │
│  │      current_features = get_recent_features()                │        │
│  │      training_features = load_training_distribution()        │        │
│  │                                                               │        │
│  │      from scipy.stats import ks_2samp                        │        │
│  │                                                               │        │
│  │      for feature in features:                                │        │
│  │          statistic, pvalue = ks_2samp(                       │        │
│  │              training_features[feature],                     │        │
│  │              current_features[feature]                       │        │
│  │          )                                                    │        │
│  │                                                               │        │
│  │          if pvalue < 0.05:  # Significant drift              │        │
│  │              log_drift_event(feature, statistic, pvalue)     │        │
│  │              send_warning(f"Feature drift in {feature}")     │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════
                        END-TO-END WORKFLOW SUMMARY
═══════════════════════════════════════════════════════════════════════════

STAGE 1: INGESTION (Daily)
├─ Scrape/download APIs: barttorvik, Hoop-Explorer, VerbalCommits; hoopR parquet download (~120MB/season)
├─ Validate schemas (Pydantic)
├─ Clean & normalize (pandas)
└─ Load to PostgreSQL (upsert)

STAGE 2: FEATURE ENGINEERING (Daily after ingestion)
├─ Player features: 30+ metrics (per-100, TS%, usage, shot distribution)
├─ Team features: System profiles (pace, ratings, style)
├─ Transfer features: Outcome metrics (change in PER, MPG)
└─ Team rating features: On/off splits, AdjEM baseline, minutes vacated, conference strength

STAGE 3: MODEL TRAINING (Weekly on Sundays)
├─ Player Clustering (K-Means + PCA)
├─ Transfer Success Prediction (XGBoost)
├─ NIL Budget Fit (Gradient Boosting)
├─ Collaborative Filtering (SVD)
├─ Team Rating Projection (XGBoost) — depends on Bayesian minutes output
└─ Log to MLflow, promote if improvement > 5%

STAGE 4: DEPLOYMENT (After training)
├─ Build Docker image with new models
├─ Blue-green deployment to ECS
├─ Health checks & smoke tests
└─ Gradual traffic shift with rollback capability

STAGE 5: MONITORING (Continuous)
├─ Track prediction accuracy
├─ Detect feature drift
├─ Alert on performance degradation
└─ Trigger retraining if needed

LATENCY TARGETS:
├─ Data ingestion: < 2 hours for daily batch
├─ Feature engineering: < 30 minutes
├─ Model training: < 4 hours
├─ Model serving: < 50ms per prediction
└─ End-to-end (new data → deployed model): < 7 days
```

## Data Flow Summary

**Raw Data** → **Validation** → **Cleaning** → **Database** → **Features** → **Models** → **Predictions** → **API** → **Users** → **Feedback** → **Retraining** → **(loop)**

The system maintains continuous learning through monitoring actual outcomes and retraining models when performance degrades or new patterns emerge.
