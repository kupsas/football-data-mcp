"""Static configuration for the football data collector (leagues, seasons, column maps)."""

from __future__ import annotations

SEASONS = ["2025-2026", "2024-2025", "2023-2024"]


# ══════════════════════════════════════════════════════════════════════════════
#  Understat configuration
# ══════════════════════════════════════════════════════════════════════════════

UNDERSTAT_LEAGUES = {
    "England Premier League": "EPL",
    "Spain La Liga":          "La_liga",
    "Germany Bundesliga":     "Bundesliga",
    "Italy Serie A":          "Serie_A",
    "France Ligue 1":         "Ligue_1",
}

UNDERSTAT_API = "https://understat.com/main/getPlayersStats/"
UNDERSTAT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://understat.com/",
}

# Understat AJAX endpoints (all data now served via these, not embedded HTML)
UNDERSTAT_AJAX_BASE = "https://understat.com/"
UNDERSTAT_AJAX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


# ══════════════════════════════════════════════════════════════════════════════
#  SofaScore configuration
# ══════════════════════════════════════════════════════════════════════════════

# Target leagues for SofaScore collection
SOFASCORE_TARGET_LEAGUES = [
    "England Premier League",
    "Spain La Liga",
    "Germany Bundesliga",
    "Italy Serie A",
    "France Ligue 1",
    "Netherlands Eredivisie",
    "Portugal Primeira Liga",
    "UEFA Champions League",
    "UEFA Europa League",
    "England EFL Championship",
]

# Maps our YYYY-YYYY season string → SofaScore YY/YY format
SOFASCORE_SEASONS = {
    "2025-2026": "25/26",
    "2024-2025": "24/25",
    "2023-2024": "23/24",
}

# SofaScore camelCase field → our snake_case column name
SS_COL_RENAME = {
    # identifiers
    "player id":               "sofascore_id",
    "team id":                 "sofascore_team_id",
    # rating
    "rating":                  "sofascore_rating",
    "totalRating":             "sofascore_rating_total",
    "countRating":             "sofascore_rating_count",
    "totwAppearances":         "totw_appearances",
    # appearances / time
    "appearances":             "games",
    "matchesStarted":          "starts",
    "minutesPlayed":           "minutes",
    # attacking
    "goals":                   "goals",
    "assists":                 "assists",
    "goalsAssistsSum":         "goals_assists",
    "ownGoals":                "own_goals",
    "goalsFromInsideTheBox":   "goals_inside_box",
    "goalsFromOutsideTheBox":  "goals_outside_box",
    "headedGoals":             "goals_headed",
    "leftFootGoals":           "goals_left_foot",
    "rightFootGoals":          "goals_right_foot",
    "penaltyGoals":            "goals_penalty",
    "freeKickGoal":            "goals_freekick",
    "hitWoodwork":             "hit_woodwork",
    "bigChancesMissed":        "big_chances_missed",
    "scoringFrequency":        "scoring_frequency",
    "goalConversionPercentage":"goal_conversion_pct",
    "setPieceConversion":      "set_piece_conversion",
    # xG (SofaScore model — we prefer Understat for Big5, but keep for other leagues)
    "expectedGoals":           "xg",
    "expectedAssists":         "xag",
    # shooting
    "totalShots":              "shots",
    "shotsOnTarget":           "shots_on_target",
    "shotsOffTarget":          "shots_off_target",
    "shotsFromInsideTheBox":   "shots_inside_box",
    "shotsFromOutsideTheBox":  "shots_outside_box",
    "blockedShots":            "blocked_shots",
    "outfielderBlocks":        "outfield_blocks",
    "shotFromSetPiece":        "shots_set_piece",
    # passing
    "totalPasses":             "passes_total",
    "accuratePasses":          "passes_completed",
    "inaccuratePasses":        "passes_inaccurate",
    "accuratePassesPercentage":"pass_completion_pct",
    "accurateFinalThirdPasses":"passes_final_third",
    "totalOppositionHalfPasses":"passes_opp_half_total",
    "accurateOppositionHalfPasses":"passes_opp_half",
    "totalOwnHalfPasses":      "passes_own_half_total",
    "accurateOwnHalfPasses":   "passes_own_half",
    "totalLongBalls":          "long_balls_total",
    "accurateLongBalls":       "long_balls_completed",
    "accurateLongBallsPercentage":"long_balls_pct",
    "totalCross":              "crosses_total",
    "accurateCrosses":         "crosses_completed",
    "accurateCrossesPercentage":"crosses_pct",
    "totalChippedPasses":      "chipped_passes_total",
    "accurateChippedPasses":   "chipped_passes_completed",
    # creation
    "keyPasses":               "key_passes",
    "passToAssist":            "pass_to_assist",
    "totalAttemptAssist":      "attempt_assists",
    "bigChancesCreated":       "big_chances_created",
    # defensive
    "tackles":                 "tackles",
    "tacklesWon":              "tackles_won",
    "tacklesWonPercentage":    "tackles_won_pct",
    "interceptions":           "interceptions",
    "clearances":              "clearances",
    "errorLeadToGoal":         "errors_leading_to_goal",
    "errorLeadToShot":         "errors_leading_to_shot",
    "dribbledPast":            "dribbled_past",
    # aerial
    "aerialDuelsWon":          "aerials_won",
    "aerialDuelsWonPercentage":"aerials_won_pct",
    "aerialLost":              "aerials_lost",
    # dribbling / duels
    "successfulDribbles":      "dribbles_completed",
    "successfulDribblesPercentage":"dribbles_pct",
    "totalContest":            "dribbles_attempted",
    "groundDuelsWon":          "ground_duels_won",
    "groundDuelsWonPercentage":"ground_duels_won_pct",
    "totalDuelsWon":           "duels_won",
    "totalDuelsWonPercentage": "duels_won_pct",
    "duelLost":                "duels_lost",
    # possession / pressing
    "touches":                 "touches",
    "possessionLost":          "possession_lost",
    "possessionWonAttThird":   "possession_won_att_third",
    "dispossessed":            "dispossessed",
    "ballRecovery":            "ball_recoveries",
    # fouls / discipline
    "fouls":                   "fouls",
    "wasFouled":               "fouled",
    "offsides":                "offsides",
    "yellowCards":             "yellow_cards",
    "yellowRedCards":          "yellow_red_cards",
    "redCards":                "red_cards",
    "directRedCards":          "direct_red_cards",
    # penalty (outfield)
    "penaltiesTaken":          "pens_taken",
    "penaltyConceded":         "pens_conceded",
    "penaltyWon":              "pens_won",
    "penaltyConversion":       "pen_conversion_pct",
    "attemptPenaltyMiss":      "pen_miss",
    "attemptPenaltyPost":      "pen_post",
    "attemptPenaltyTarget":    "pen_on_target",
    # goalkeeper
    "saves":                   "saves",
    "savesCaught":             "saves_caught",
    "savesParried":            "saves_parried",
    "savedShotsFromInsideTheBox": "saves_inside_box",
    "savedShotsFromOutsideTheBox":"saves_outside_box",
    "goalsConceded":           "goals_conceded",
    "goalsConcededInsideTheBox":"goals_conceded_inside_box",
    "goalsConcededOutsideTheBox":"goals_conceded_outside_box",
    "goalsPrevented":          "goals_prevented",
    "cleanSheet":              "clean_sheets",
    "highClaims":              "high_claims",
    "crossesNotClaimed":       "crosses_not_claimed",
    "punches":                 "punches",
    "runsOut":                 "runs_out",
    "successfulRunsOut":       "runs_out_successful",
    "goalKicks":               "goal_kicks",
    "penaltySave":             "pens_saved",
    "penaltyFaced":            "pens_faced",
}

# Stats already authoritative from Understat — don't overwrite from SofaScore for Big5 leagues
UNDERSTAT_AUTHORITATIVE = frozenset({
    "goals", "assists", "shots", "minutes", "games",
    "yellow_cards", "red_cards", "key_passes",
    "xg", "xag", "npxg", "npg", "xg_chain", "xg_buildup",
})


# ══════════════════════════════════════════════════════════════════════════════
#  Manual TM name overrides
#  Keys: (norm(unified_player_name), league)  →  norm(tm_name)
#  Used when the player is known by a different name/nickname on each platform.
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
#  EA FC / SoFIFA player attributes (Kaggle → slim parquet)
# ══════════════════════════════════════════════════════════════════════════════

# Each entry: download from Kaggle, optionally filter by fifa_version, map to our season.
EAFC_SOURCES: list[dict] = [
    {
        "id": "fifa23",
        "owner_slug": "stefanoleone992",
        "dataset_slug": "fifa-23-complete-player-dataset",
        "file_name": "male_players.csv",
        "fifa_version_filter": 23,
        "season": "2023-2024",
        "parquet_name": "eafc__2023_2024",
        "schema": "stefanoleone992",
    },
    {
        "id": "eafc24",
        "owner_slug": "stefanoleone992",
        "dataset_slug": "ea-sports-fc-24-complete-player-dataset",
        "file_name": "male_players.csv",
        "fifa_version_filter": 24,
        "season": "2024-2025",
        "parquet_name": "eafc__2024_2025",
        "schema": "stefanoleone992",
    },
    {
        "id": "eafc25",
        "owner_slug": "aniss7",
        "dataset_slug": "fifa-player-data-from-sofifa-2025-06-03",
        "file_name": "player-data-full-2025-june.csv",
        "fifa_version_filter": None,
        "season": "2025-2026",
        "parquet_name": "eafc__2025_2026",
        "schema": "aniss7_2025",
    },
]

# aniss7 / SoFIFA June 2025 dump (~76 cols, flat stat names — not 110-col stefanoleone992 layout).
EAFC_COL_RENAME_ANISS7_2025: dict[str, str] = {
    "player_id": "eafc_id",
    "name": "player",
    "full_name": "player_long",
    "positions": "positions",
    "country_name": "nationality",
    "club_name": "club_name",
    "club_league_name": "club_league_name",
    "work_rate": "work_rate_raw",
    "play_styles": "player_traits",
    "specialities": "player_traits_alt",
    "defensive_awareness": "marking",
    "crossing": "crossing",
    "finishing": "finishing",
    "heading_accuracy": "heading_accuracy",
    "short_passing": "short_passing",
    "volleys": "volleys",
    "dribbling": "dribbling",
    "curve": "curve",
    "long_passing": "long_passing",
    "ball_control": "ball_control",
    "acceleration": "acceleration",
    "sprint_speed": "sprint_speed",
    "agility": "agility",
    "reactions": "reactions",
    "balance": "balance",
    "shot_power": "shot_power",
    "jumping": "jumping",
    "stamina": "stamina",
    "strength": "strength",
    "long_shots": "long_shots",
    "aggression": "aggression",
    "interceptions": "interceptions",
    "positioning": "positioning",
    "vision": "vision",
    "composure": "composure",
    "standing_tackle": "standing_tackle",
    "sliding_tackle": "sliding_tackle",
    "gk_diving": "gk_diving",
    "gk_handling": "gk_handling",
    "gk_kicking": "gk_kicking",
    "gk_positioning": "gk_positioning",
    "gk_reflexes": "gk_reflexes",
}

# Columns we keep from SoFIFA-style dumps (after rename). Used for DuckDB SELECT * then pandas trim.
EAFC_OUTPUT_COLUMNS: list[str] = [
    "eafc_id", "player", "_name_norm", "club_name", "club_league_name", "_club_norm",
    "age", "nationality", "positions",
    "overall_rating", "potential", "height_cm", "weight_kg",
    "pace", "sprint_speed", "acceleration", "stamina", "strength", "agility", "balance", "jumping",
    "shooting", "finishing", "long_shots", "volleys",
    "passing", "short_passing", "long_passing", "crossing", "curve",
    "dribbling", "ball_control", "heading_accuracy",
    "defending", "standing_tackle", "sliding_tackle", "interceptions", "marking",
    "reactions", "composure", "vision", "positioning", "aggression",
    "preferred_foot", "weak_foot", "skill_moves",
    "work_rate_attacking", "work_rate_defending", "player_traits", "body_type",
    "season",
]

# stefanoleone992 / SoFIFA 110-column schema → snake_case (subset used in pipeline).
EAFC_COL_RENAME_STEFANO: dict[str, str] = {
    "player_id": "eafc_id",
    "sofifa_id": "eafc_id",
    "short_name": "player",
    "long_name": "player_long",
    "player_positions": "positions",
    "overall": "overall_rating",
    "nationality_name": "nationality",
    "club_name": "club_name",
    "club_league_name": "club_league_name",
    "league_name": "club_league_name",
    "club_team": "club_name",
    "club": "club_name",
    "work_rate": "work_rate_raw",
    "player_traits": "player_traits",
    "physic": "physicality",
    "attacking_finishing": "finishing",
    "attacking_long_shots": "long_shots",
    "attacking_volleys": "volleys",
    "skill_dribbling": "dribbling",
    "skill_curve": "curve",
    "skill_ball_control": "ball_control",
    "skill_heading_accuracy": "heading_accuracy",
    "power_shot_power": "shot_power",
    "power_jumping": "jumping",
    "power_stamina": "stamina",
    "power_strength": "strength",
    "power_long_shots": "long_shots_power",
    "movement_acceleration": "acceleration",
    "movement_sprint_speed": "sprint_speed",
    "movement_agility": "agility",
    "movement_reactions": "reactions",
    "movement_balance": "balance",
    "mentality_aggression": "aggression",
    "mentality_interceptions": "interceptions",
    "mentality_positioning": "positioning",
    "mentality_vision": "vision",
    "mentality_composure": "composure",
    "mentality_marking": "marking",
    "defending_standing_tackle": "standing_tackle",
    "defending_sliding_tackle": "sliding_tackle",
    "attacking_crossing": "crossing",
    "attacking_short_passing": "short_passing",
    "attacking_long_passing": "long_passing",
    "goalkeeping_diving": "gk_diving",
    "goalkeeping_handling": "gk_handling",
    "goalkeeping_kicking": "gk_kicking",
    "goalkeeping_positioning": "gk_positioning",
    "goalkeeping_reflexes": "gk_reflexes",
    "goalkeeping_speed": "gk_speed",
}

# rovnez FC26 schema (flat names, 110 cols).
EAFC_COL_RENAME_ROVNEZ: dict[str, str] = {
    "player_id": "eafc_id",
    "short_name": "player",
    "long_name": "player_long",
    "player_positions": "positions",
    "overall": "overall_rating",
    "nationality_name": "nationality",
    "club_name": "club_name",
    "club_league_name": "club_league_name",
    "league_name": "club_league_name",
    "club_team": "club_name",
    "club": "club_name",
    "work_rate": "work_rate_raw",
    "player_tags": "player_traits",
    "player_traits": "player_traits",
    "pace": "pace",
    "shooting": "shooting",
    "passing": "passing",
    "dribbling": "dribbling",
    "defending": "defending",
    "physic": "physicality",
    "acceleration": "acceleration",
    "sprint_speed": "sprint_speed",
    "positioning": "positioning",
    "vision": "vision",
    "composure": "composure",
    "interceptions": "interceptions",
    "heading_accuracy": "heading_accuracy",
    "standing_tackle": "standing_tackle",
    "sliding_tackle": "sliding_tackle",
    "crossing": "crossing",
    "short_passing": "short_passing",
    "long_passing": "long_passing",
    "curve": "curve",
    "finishing": "finishing",
    "long_shots": "long_shots",
    "volleys": "volleys",
    "ball_control": "ball_control",
    "aggression": "aggression",
    "reactions": "reactions",
    "stamina": "stamina",
    "strength": "strength",
    "agility": "agility",
    "balance": "balance",
    "jumping": "jumping",
    "marking": "marking",
}

# Prefix for EA FC columns merged into unified (avoids clashing with FBref/SofaScore names).
EAFC_UNIFIED_PREFIX = "eafc_"


MANUAL_TM_OVERRIDES: dict[tuple[str, str], str] = {
    # ── England EFL Championship ─────────────────────────────────────────────
    ("vinicius souza",        "England EFL Championship"): "vini souza",
    ("frederick issaka",      "England EFL Championship"): "freddie issaka",
    ("solomon brynn",         "England EFL Championship"): "sol brynn",
    ("alexander gilbert",     "England EFL Championship"): "alex gilbert",
    # ── England Premier League ───────────────────────────────────────────────
    ("kepa",                  "England Premier League"):   "kepa arrizabalaga",
    ("matthew cash",          "England Premier League"):   "matty cash",
    ("savio",                 "England Premier League"):   "savinho",
    ("chimuanya ugochukwu",   "England Premier League"):   "lesley ugochukwu",   # full: Lesley Chimuanya Ugochukwu
    ("jaden philogenebidace", "England Premier League"):   "jaden philogene",
    ("andy irving",           "England Premier League"):   "andrew irving",
    # ── France Ligue 1 ──────────────────────────────────────────────────────
    ("mat ryan",              "France Ligue 1"):           "mathew ryan",
    ("mathis cherki",         "France Ligue 1"):           "rayan cherki",       # SofaScore wrong first name
    ("tinotenda kadewere",    "France Ligue 1"):           "tino kadewere",
    ("hianga039a m039bock",   "France Ligue 1"):           "hiangaa mbock",      # HTML entity artifact
    ("zabi gueu",             "France Ligue 1"):           "patrick zabi",       # TM uses reversed name order
    ("john finn",             "France Ligue 1"):           "john patrick",       # TM: "John Patrick" at Reims
    # ── Germany Bundesliga ──────────────────────────────────────────────────
    ("alex grimaldo",         "Germany Bundesliga"):       "alejandro grimaldo",
    ("bote baku",             "Germany Bundesliga"):       "ridle baku",         # full: Bote Baku Ridle
    ("julian chabot",         "Germany Bundesliga"):       "jeff chabot",        # TM uses middle name Jeff
    ("jamie bynoegittens",    "Germany Bundesliga"):       "jamie gittens",
    ("jannfiete arp",         "Germany Bundesliga"):       "fiete arp",
    # ── Italy Serie A ───────────────────────────────────────────────────────
    ("franck zambo",          "Italy Serie A"):            "frank anguissa",     # André-Frank Zambo Anguissa
    ("ndary adopo",           "Italy Serie A"):            "michel adopo",       # Ndary Michel Adopo
    ("valentin castellanos",  "Italy Serie A"):            "taty castellanos",   # Taty = nickname for Valentín
    ("zito",                  "Italy Serie A"):            "zito luvumbo",
    ("alejandro jimenez",     "Italy Serie A"):            "alex jimenez",
    ("keita",                 "Italy Serie A"):            "keita balde",        # Keita Baldé Diao at Monza
    ("tasos douvikas",        "Italy Serie A"):            "anastasios douvikas",
    ("kouadio kone",          "Italy Serie A"):            "manu kone",          # Emmanuel Kouadio Koné → Manu Koné
    # ── Portugal Primeira Liga ───────────────────────────────────────────────
    ("jason",                 "Portugal Primeira Liga"):   "jason remeseiro",
    ("dudu",                  "Portugal Primeira Liga"):   "dudu teodora",
    ("fahem benaissayahia",   "Portugal Primeira Liga"):   "fahem benaissa",
    ("francisco goncalves",   "Portugal Primeira Liga"):   "chico goncalves",    # Chico = Francisco
    # ── Spain La Liga ────────────────────────────────────────────────────────
    ("kylian mbappelottin",   "Spain La Liga"):            "kylian mbappe",
    ("abdelkabir abqar",      "Spain La Liga"):            "abdel abqar",
    ("abdessamad ezzalzouli", "Spain La Liga"):            "abde ezzalzouli",
    ("yuri",                  "Spain La Liga"):            "yuri berchiche",
    ("raba",                  "Spain La Liga"):            "dani raba",
    ("raul",                  "Spain La Liga"):            "raul jimenez",
    ("adria alti",            "Spain La Liga"):            "adria altimira",
    ("alfon",                 "Spain La Liga"):            "alfon gonzalez",
    ("peque",                 "Spain La Liga"):            "peque fernandez",
    ("nyom",                  "Spain La Liga"):            "allan nyom",
    ("munir",                 "Spain La Liga"):            "munir el haddadi",
    ("tasos douvikas",        "Spain La Liga"):            "anastasios douvikas",
    ("peter",                 "Spain La Liga"):            "peter federico",
    ("nianzou kouassi",       "Spain La Liga"):            "tanguy nianzou",     # Tanguy Nianzou Kouassi
    ("ezequiel avila",        "Spain La Liga"):            "chimy avila",        # Chimy = nickname for Ezequiel
}
