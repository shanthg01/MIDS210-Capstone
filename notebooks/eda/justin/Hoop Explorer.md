## This provides insight into the Hoop Explorer platform and dataset, that we are exploring.

Good, I have enough to give you a thorough, well-grounded comparison. Here's the breakdown:

---

## What Hoop Explorer Offers That the Others Don't

For a transfer portal analytics platform specifically, Hoop Explorer's differentiation is concentrated in a few specific areas that directly address what makes transfer evaluation hard.

### 1. RAPM — The Core Differentiator

Hoop Explorer is described as the only open website for on/off, lineup, shot chart, positional, and RAPM analysis of college basketball. This matters enormously for your use case.

RAPM (Regularized Adjusted Plus-Minus) is the single most informative metric for answering the transfer portal's core question: *how much does this player actually contribute, independent of teammates and opponents?* A recruit from a weak program who put up gaudy stats might have a near-zero or negative RAPM. Hoop Explorer surfaces this at the player level, with a RAPM breakdown into four-factor components so you can see whether a player's impact is offensive, defensive, or both — and *what kind* of offensive/defensive impact it is.

None of the other three sources offer this natively:
- **CBBpy** — a Python scraper that grabs play-by-play, boxscore, and game metadata. Raw material only. No RAPM, no on-off, no lineup processing.
- **cbbData** — a rich collection of Barttorvik data including comprehensive game-by-game logs, box scores, and advanced metrics dating back to 2008. Excellent player game logs, but no RAPM, no lineup-level analysis.
- **Barttorvik/T-Rank** — detailed player statistics including box and advanced statistics per game back to the 2008 season, with splits by game result, type, location, or month. Also offers extensive transfer histories for over 5,000 players back to the 2011-12 season. Strong on longitudinal player tracking, but again — no RAPM, no lineup impact.

---

### 2. Lineup Analysis with Positional Groupings

Hoop Explorer lets you deep-dive into every lineup combination a team has used, including combining and comparing lineup stats across flexible positional groupings — frontcourt, backcourt, and custom combinations.

For transfer analytics, this is powerful. If you're evaluating a guard transferring out of a particular system, you can look at how lineups with that player performed vs. lineups without them — and whether their positional role fits the target roster's needs. None of the other three datasets present pre-computed lineup stats; you'd have to derive them from raw PBP yourself using CBBpy.

---

### 3. On/Off Analysis Out-of-the-Box

Hoop Explorer's on/off analysis measures how team stats shift with each player on and off the court. Unlike the Splits Analysis page which shows more detailed stats for a selected player on/off, the on/off analysis page provides a broader view across all players, also breaking down RAPM into four-factors components.

This is the connective tissue between RAPM and actual usage. For a transfer portal platform, you want to know: when this player plays, does the team get better or worse? By how much, and in what way? You can compute on/off from CBBpy's PBP data, but it requires significant engineering work.

---

### 4. Play Style Classification

Hoop Explorer categorizes any team or player's offense and defense into intuitive styles: Post-Up, Transition, Perimeter Sniper, Pick & Roll, and more — available at both team and player level. Each player is classified into a positional role based on box score stats and height, powering many of Hoop Explorer's advanced features.

For a transfer portal platform, this is a fit/identity tool. You can answer: does this incoming transfer play the same style as the player they're replacing? Does their play style clash with or complement what the new team runs? Barttorvik gives four-factors at the team level; none of the three give player-level play style labels.

---

### 5. Cross-Season Player Profiles and Career Trajectories

Player profiles on Hoop Explorer go back to 2018 and include career stats, shot charts, play style breakdowns, and cross-season comparisons. The cross-season chart feature includes both current and prior season data, allowing questions like "how did they improve?" to be answered visually.

Barttorvik via cbbData goes back further for game logs, but Hoop Explorer has this packaged as a player-profile view with longitudinal trajectory built in — critical for evaluating whether a portal player is ascending or declining.

---

### 6. Game-Level Impact Reports

Game reports on Hoop Explorer include per-player offensive and defensive impact ratings, time-series lineup charts throughout the game, and full style breakdowns. Using the RAPM formula, it produces a single-game impact number that's digestible — for example, showing a player's offensive impact (+4.9 points) and defensive impact (+9.3 points) for a total impact score (+14.1) in a specific game.

This lets you audit whether a transfer's season stats were carried by a handful of elite performances or represent consistent contribution.

---

## Where the Others Beat Hoop Explorer

To be balanced about it:

**Barttorvik/cbbData** wins on raw depth. Barttorvik's data goes back to 2008 for game-by-game stats with player splits by game result, type, location, and month, which gives you more historical context. It also has transfer histories for over 5,000 players back to 2011-12 and player recruiting rankings back to 2007-08 — directly useful for portal work. Hoop Explorer's profiles only go back to 2018.

**CBBpy** wins on raw PBP access. It's designed to grab play-by-play, boxscore, and game metadata for any NCAA D1 men's or women's basketball game, giving you the building blocks to engineer any custom metric you want — if you have the engineering resources.

**cbbData** wins on programmatic access. It's an API-first, structured Python/R library with clean tidy outputs. Hoop Explorer has CSV export, but it's a web UI first, not an API.

---

## The Strategic Implication for Your Platform

The right framing isn't "which one is best" — it's that **Hoop Explorer fills the impact measurement gap** that the others leave open. CBBpy and cbbData give you the raw data and box score context. Barttorvik gives you team and player efficiency benchmarks and historical transfer context. But **Hoop Explorer is the only open source that pre-computes RAPM, lineup impact, on/off deltas, and play style tags at the player level** — which are precisely the metrics that distinguish "good stats on a bad team" from "genuine contributor who will translate."

For a transfer portal platform, a practical stack would be: Barttorvik (via cbbData) for historical player logs and prior transfer context → CBBpy for raw PBP if you need to build custom models → Hoop Explorer as your impact validation layer (RAPM, on/off, lineup fit, play style) to qualify and rank portal candidates.

## Useful doc insight into the CSV fields and explanations

Where both clauses are optional.

The "<<query>>" corresponds to the "where" clause in the LINQ expression, and the "<<ordering>>" to "orderByDescending" (default), or "orderBy" (ascending, if the ordering contains the string "ASC"). 

You can have secondary/tertiary sorts by adding multiple SORT_BY clauses (eg "SORT_BY roster.height ASC SORT_BY def_blk" will list players shortest to tallest, and within each height range their block% (best first).

A good guide to LINQ is here. Basically you can do the usual numeric and variable comparisons, combined using "&&" (I also allow AND) and "||" (I also allow OR).

Note: all %s are in the range 0-1 unless otherwise specified, eg you would do "off_team_poss_pct > 0.50" to mean "Players who played more than 50% of their team's minutes". You could also write "off_team_poss_pct > 50%" for a functionally equivalent expression that is more readable.

In the Player Comparison Chart, all of the fields unless otherwise specified should have the prefix "prev_" for the earlier season and "next_" for the later season. Eg if the season is 20/21 then "prev_team" is a player's team in 19/20, and "next_team" their 20/21 team.

Useful #1: For many numeric stats outside  the "advanced metadata" (not all - notably not any "scramble" or "transition" stats, see below), you can prefix "rank_" to get the rank of that stat in D1 (or vs high/medium/low tiers if you have selected one of those), or "pctile_" to get the percentile (a high percentile is a low rank). Note this is currently only in the Player Leaderboard, not the Player Comparison chart.

 Useful #2: You can also query team stats (see Team Stats Explorer fields), by prepending "team_stats." to the fields in this the preceding link (numeric stats only, outside of "advanced metadata"). To query ranks/pctiles, use "rank_team_stats." or "pctile_team_stats."

The following fields are supported:

Basic metadata
"player_name" - the player's name in the format "Surname, First Name"
"player_code" - an internal code which is approximately: first two characters of first name, last 10 of other names .. eg "Edey, Zach" is "ZaEdey" 
"conf" - the conference in which the team plays
"team" - the team name
"year" - the year in which the season starts
Using "ALL" for the query part will return all players.
Advanced metadata
"posClass" - the positional role of the player (see this page for details on positional roles: "PG", "s-PG", "CG", "WG", "WF", "S-P4", "PF/C", "C")
"posConfidences" - an array of %fit for the player as "PG" (index [0]) through "C" (index[4]). 
"posFreqs" - the % of the time the positional categorizer believes the player actually spent at the "PG" (index [0]) through "C" (index[4]) spots. Eg "posFreqs[4] > 50%" filters players who spent more than half the time as the lineups' center.
(You can also use "_1_" through "_5_" as the indices; or "_PG_" through "_C_")
"roster.number", "roster.height", "roster.year_class" (Fr, So, Jr, Sr), "roster_pos" (G, F, C) - roster information (height is in the format <<ft>>-<<in>>)
"tier" - the peer group of the player's team ("High" - high majors + strong teams; "Medium" - better mid major leagues and decent teams; "Low" - low majors).
"transfer_src", "transfer_dest" - the team from which the player is transferring, and to which team - undefined if they have not (yet!) transferred; "NBA" if they have declared.
In the Player Comparison chart it is always available (no next_ or prev_ prefix), equivalently use "prev_team" and "next_team", and eg "prev_team != next_team".
"roster.height" is expressed is "<<ft>>-<<in>>". The operators take into account that (eg) "6-2" < "6-11" so standard ordering/comparison should work as expected.
Opposition strength
"off_adj_opp", "def_adj_opp" - the KenPom adjusted efficiency of the opposition, weighted by possessions
Possessions
"off_poss", "off_team_poss_pct" - player offensive possessions, % of offensive possessions for which the player was on the floor.
"def_poss", "def_team_poss_pct" - same but for defensive possessions
4 Factors

"off_efg", "off_to", "off_ftr", "off_drb" - effective field goal, TO%, free throw rate (FTAs/FGAs), defensive rebounding rate.
Overall player evaluation
Some general notes: 

Stats with "_margin" are the difference between the offensive and defensive numbers for that stat.
Stats with "_prod" are multiplied by the possession%, ie they give that players production taking how often they played into account.
"Adjusted Rating+" ("adj_rtg") is almost the same as Torvik's PORPAGATU, except per 100 team possessions, instead of per game, and 0 is an average D1 player instead of a "replacement player" - ie a roster full of players with "adj_rtg" of 0 would have a KenPom margin of 0.
The "rapm" stat is described here.
"_rank" is the ranking of that stat amongst all qualifying players in the same tier.
Here is a list of the fields:

"adj_rtg_margin", "adj_prod_margin", "adj_rapm_margin", "adj_rapm_prod_margin"
"adj_rtg_margin_rank", "adj_prod_margin_rank", "adj_rapm_margin_rank", "adj_rapm_prod_margin_rank"
"off_rtg", "off_adj_rtg", "off_adj_prod", "off_adj_rapm", "off_adj_rapm_prod"
"off_adj_rtg_rank", "off_adj_prod_rank", "off_adj_rapm_rank", "off_adj_rapm_prod_rank"
"def_rtg", "def_adj_rtg", "def_adj_prod", "def_adj_rapm", "def_adj_prod"
"def_adj_rtg_rank", "def_adj_prod_rank", "def_adj_rapm_rank", "def_adj_rapm_prod_rank"
In the Player Leaderboard, for transfers you can append "_pred" to: "off_adj_rapm", "def_adj_rapm", "off_rtg_pred", "off_usage_pred", and "adj_rapm_margin_pred" to get an approximate balanced prediction for how that player would fare at a NCAAT-bound high major the following season.

In the Player Comparison Chart you instead use the prefix "pred_ok_" (balanced), or "pred_good_" (optimistic), or "pred_bad_" (pessimistic), with the same set of fields. This is instead of "next_"/"prev_".
Shot creation
"off_usage" - the player's usage (0.20 is average, 0.25 is primary scorer, 0.30 is "carry team on back")
"off_assist" - the % of points scored by the player's team-mates for which they assisted
 "off_ast_rim", "off_ast_mid", "off_ast_3p" - the % of the player's assists to shots at the rim, in the mid-range, and from 3 respectively
"off_2primr", "off_2pmidr", "off_3pr" - the % of the player's shots taken at the rim, in the mid-range, and from 3 respectively
"off_orb" - the % of the opponent's misses the player rebounds
Shot making
"off_3p", "off_2p", "off_2pmid", "off_2prim", "off_ft" - player %s from 3, 2, rim, mid-range and on FTs respectively
"off_3p_ast", "off_2p_ast", "off_2pmid_ast", "off_2prim_ast" - the % of the made shots of each type that were assisted.
Defense
"def_stl", "def_blk" - player steal and block %s

"def_fc" - Fouls called on player per 50 possessions (approx 1 game at 30mpg)

Stats in post-offensive-rebound "scrambles"
These can be approximately described as possessions that end shortly after an offensive rebound (but typically not if the offense is recycled).

"off_scramble_2p", "off_scramble_2p_ast", "off_scramble_3p", "off_scramble_3p_ast", "off_scramble_2prim", "off_scramble_2prim_ast", "off_scramble_2pmid", "off_scramble_2pmid_ast", "off_scramble_ft", "off_scramble_ftr", "off_scramble_2primr", "off_scramble_2pmidr", "off_scramble_3pr", "off_scramble_assist"- same as the similarly named stats above but in "scramble" situations
Stats in transition offense
"off_trans_2p", "off_trans_2p_ast", "off_trans_3p", "off_trans_3p_ast", "off_trans_2prim", "off_trans_2prim_ast", "off_trans_2pmid", "off_trans_2pmid_ast", "off_trans_ft", "off_trans_ftr", "off_trans_2primr", "off_trans_2pmidr", "off_trans_3pr", "off_trans_assist"- same as the similarly named stats above but in transition offense only
Play Style Breakdown Statistics
In the following stats, "_pct" stats are the number of "plays" per 100 player possessions for the given play type (a "play" is not quite the same as a "possession" because of offensive rebounding - each play is a missed or made shot, a TO, or a shooting foul), "_usg" stats are the number of players per 100 team possessions (ie take a player's usage into account).
 
Finally "_ppp" stats are "points per play" (ie 1.0 is one point per play, unlike the efficiency "_ppp" in the previous sections, where 100 would be one point per possession) 

It is recommended to use "rank_" or "pctile_" as much as possible because the different play types have very different usage and efficiency profiles - eg 50%-ile of "Rim Attack" is ~25 plays per 100 vs "High Low" is ~2 play per 100! The efficiency of cuts is much higher than that of post-ups, etc.
 
"off_style_rim_attack_pct", "off_style_rim_attack_ppp", "off_style_rim_attack_usg"- "Rim Attack", Drives and slashes to the rim from the perimeter, includes pull-ups and floaters
"off_style_attack_kick_pct", "off_style_attack_kick_ppp", "off_style_attack_kick_usg" - "Attack & Kick", Ball-handler passes to the perimeter for 3P, usually after the defense collapses on a drive (The player is the passer)
"off_style_perimeter_sniper_pct",  "off_style_perimeter_sniper_ppp", "off_style_perimeter_sniper_usg" - "Perimeter Sniper", A 3P shooter fed by a drive or post-up (The player is the shooter from Attack & Kick / Inside-Out)
"off_style_dribble_jumper_pct", "off_style_dribble_jumper_ppp", "off_style_dribble_jumper_usg" - "Dribble Jumper", 3P shots off the dribble, eg off ISOs or defenders going under screens
"off_style_mid_range_pct", "off_style_mid_range_ppp", "off_style_mid_range_usg" - "Mid-Range", The offense finds space in the mid-range, from backcourt/wing passes or sagging defenders
"off_style_hits_cutter_pct", "off_style_hits_cutter_ppp", "off_style_hits_cutter_usg" - "Hits Cutter", A perimeter player cuts to the basket eg via a backdoor cut (the player is the passer)
"off_style_perimeter_cut_pct", "off_style_perimeter_cut_ppp", "off_style_perimeter_cut_usg" - "Perimeter Cut", A perimeter player cuts to the basket eg via a backdoor cut (The player is the shooter)
 "off_style_pnr_passer_pct", "off_style_pnr_passer_ppp", "off_style_pnr_passer_usg" - "PnR Passer", A frontcourt player cuts to the basket, usually after a screen, eg in PnR (The player is the passer)
"off_style_big_cut_roll_pct", "off_style_big_cut_roll_ppp", "off_style_big_cut_roll_usg" - "Big Cut & Roll", A frontcourt player cuts to the basket, usually after a screen, eg in PnR (The player is the shooter)
"off_style_post_up_pct", "off_style_post_up_ppp", "off_style_post_up_usg" - "Post-Up", A frontcourt player backs his defender down to the rim (includes drives if you are lucky enough to have a center who can do that!)
"off_style_post_kick_pct", "off_style_post_kick_ppp", "off_style_post_kick_usg" - "Inside Out", A frontcourt player is doubled (usually), but finds an open shooter on the perimeter or mid-range
"off_style_pick_pop_pct", "off_style_pick_pop_ppp", "off_style_pick_pop_usg" - "Pick & Pop",  An assisted 3P from a frontcourt player, sometimes after setting a screen
"off_style_high_low_pct", "off_style_high_low_ppp", "off_style_high_low_usg" - "High-Low", Two bigs connect for a shot at the rim
"off_style_reb_scramble_pct", "off_style_reb_scramble_ppp", "off_style_reb_scramble_usg" - "Scramble", Shots taken directly off a rebound (can include a kick-out for 3P)
"off_style_transition_pct", "off_style_transition_ppp", "off_style_transition_usg" - "Transition", Rim-to-rim, off turnovers, etc

