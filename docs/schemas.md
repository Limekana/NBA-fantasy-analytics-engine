# Data schemas

Every dataframe crossing a module boundary conforms to one of these. Defined in
`src/schemas.py`; keeping them in one place is what lets ingestion adapters be
swapped without touching scoring, projection or draft code.

## `game_log` — one row per player per game

**Raw fact. Never edited in place (Engineering Rule 2).**

| Column | Type | Required | Notes |
|---|---|---|---|
| `player_id` | string | yes | Source-native; derived from the name if absent |
| `player_name` | string | yes | |
| `season` | string | yes | `"2025-26"` |
| `game_id` | string | yes | Derived from date+team if absent |
| `game_date` | datetime | yes | |
| `team` | string | yes | Tricode, e.g. `BOS` |
| `opponent` | string | no | Derived from `MATCHUP` when present |
| `home` | bool | no | Derived from `MATCHUP` when present |
| `started` | bool | no | Not supplied by `PlayerGameLogs` |
| `minutes` | float | yes | Accepts `34:12` or `34.2` |
| `points` `rebounds` `assists` `steals` `blocks` | float | yes | |
| `turnovers` `personal_fouls` | float | yes | Scored **negatively** — handoff §25 forbids ignoring these |
| `free_throws_made` `three_pointers_made` | float | yes | |
| `field_goals_attempted` `free_throws_attempted` `three_pointers_attempted` `offensive_rebounds` `defensive_rebounds` `usage_rate` | float | no | Used for role work when available |
| `is_synthetic` | bool | no | True marks generated data; propagates to every output |

## `players` — one row per player

| Column | Type | Notes |
|---|---|---|
| `player_id` | string | Joins to `game_log` |
| `player_name` | string | |
| `sleeper_id` | string | Join key for anything league-specific |
| `team` `position` | string | `position` is primary: PG/SG/SF/PF/C |
| `positions` | string | Pipe-delimited eligibility, `"PG\|SG"` |
| `age` | float | Drives the age curve; defaults to 27 with a recorded assumption |
| `injury_status` | string | From Sleeper |

## `schedule`

| Column | Type |
|---|---|
| `game_id` | string |
| `game_date` | datetime |
| `season` | string |
| `home_team` `away_team` | string |

A `team`/`game_date` pair is also accepted.

## `adp`

Provenance is part of the record, not metadata (handoff §14).

| Column | Type | Notes |
|---|---|---|
| `player_name` | string | Joined via `normalise_name` (strips accents, suffixes, punctuation) |
| `adp` | float | |
| `source` | string | Required — never merge sources anonymously |
| `retrieved_at` | string | ISO 8601 |
| `league_format` | string | e.g. `"10-team"` |
| `scoring_format` | string | e.g. `"sleeper_points_lockin"` |

## Outputs

`outputs/draft_board.csv` carries every column required by handoff §26:

`player_name`, `team`, `position`, `model_rank`, `tier`, `projected_fp_game`,
`projected_games`, `projected_season_value`, `median_fp`, `floor`, `ceiling`,
`std_dev`, `double_double_rate`, `triple_double_rate`, `40_point_rate`,
`50_point_rate`, `15_assist_rate`, `lockin_value`, `adp`, `adp_vs_model`, `risk`

plus these additions:

| Column | Meaning |
|---|---|
| `lock_in_advantage` | `lockin_value` − raw FP/game. **The Lock-In edge.** |
| `lockin_perfect` | Clairvoyant upper bound — never a projection |
| `lockin_auto` | What you score if you never lock (Sleeper takes the last game) |
| `games_per_week` | Expected games per fantasy week |
| `vor` | Value over replacement at the position |
| `positional_scarcity` | How early the position runs dry |
| `value_flag` | `undervalued` / `fairly_valued` / `overvalued` / `no_adp` |
| `archetype` | Discovered by clustering, not hand-assigned |
| `assumption_notes` | Every assumption applied to this player |
| `used_real_schedule` | Whether real schedule data backed the Lock-In value |
| `is_synthetic` | **True means do not draft from this row** |
