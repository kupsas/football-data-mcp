# football-data-mcp

A multi-source football data pipeline and MCP server that lets Claude (and any MCP-compatible AI assistant) answer real football analytics questions — player scouting, similarity search, market value filtering, xG tables, match shot maps, and more.

Built on top of [ScraperFC](https://github.com/oseymour/ScraperFC) by Owen Seymour.

---

## What it does

Pulls data from four sources, merges them into a single unified dataset, and serves it through a Model Context Protocol (MCP) server:

| Source | What it contributes |
|--------|-------------------|
| **SofaScore** | 80+ match stats per player: rating, progressive carries, big chances, dribbles, aerials, accurate long balls, etc. |
| **FBref** | xG, npxG, xA, progressive passes received, GCA, SCA, pass completion % |
| **Understat** | xg_chain, xg_buildup (involvement in build-up play) |
| **Transfermarkt** | Market value, contract expiration, height, nationality, position |

**Coverage:** 10 leagues · 3 seasons (2023-24, 2024-25, 2025-26) · 18,800+ player records · 146 columns

---

## The 8 MCP tools

Once connected, Claude can use these tools directly in conversation:

| Tool | What you can ask |
|------|-----------------|
| `get_player` | "Show me everything on Bukayo Saka" |
| `scout_position` | "Top 10 pressing forwards in the Bundesliga this season" |
| `compare_players` | "Compare Salah and Son across all stats" |
| `find_similar_players` | "Find players similar to Bellingham under €80m" |
| `get_league_table` | "xG league table for Serie A, home games only" |
| `get_match` | "Shot map from the El Clasico in March" |
| `get_player_history` | "Haaland's xG per game across the season" |
| `data_status` | Coverage check across all data sources |

---

## Setup

### 1. Install dependencies

```bash
pip install -e .
pip install rapidfuzz
```

### 2. Collect the data

```bash
# Full collection (takes a while — runs headless Chrome for FBref + SofaScore)
python3 collect_data.py

# Individual sources
python3 collect_data.py --sofascore-only
python3 collect_data.py --understat-only
python3 collect_data.py --transfermarkt-only

# Supplementary data (xG tables, match shots, rosters)
python3 collect_data.py --understat-tables-only
python3 collect_data.py --understat-matches-only

# Rebuild the unified CSV from already-collected raw files
python3 collect_data.py --rebuild-only
```

### 3. Connect to Claude Desktop

Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "soccer-data": {
      "command": "python3",
      "args": ["/path/to/football-data-mcp/soccer_server.py"]
    }
  }
}
```

On macOS the config file lives at:
`~/Library/Application Support/Claude/claude_desktop_config.json`

Restart Claude Desktop. The 8 tools will appear automatically.

---

## Data files

Raw files are stored in `data/raw/` and the merged dataset at `data/unified_player_stats.csv`.

---

## Leagues covered

| League | Seasons |
|--------|---------|
| England Premier League | 2023-24, 2024-25, 2025-26 |
| England EFL Championship | 2023-24, 2024-25, 2025-26 |
| Spain La Liga | 2023-24, 2024-25, 2025-26 |
| Germany Bundesliga | 2023-24, 2024-25, 2025-26 |
| Italy Serie A | 2023-24, 2024-25, 2025-26 |
| France Ligue 1 | 2023-24, 2024-25, 2025-26 |
| Netherlands Eredivisie | 2023-24, 2024-25, 2025-26 |
| Portugal Primeira Liga | 2023-24, 2024-25, 2025-26 |
| UEFA Champions League | 2023-24, 2024-25, 2025-26 |
| UEFA Europa League | 2023-24, 2024-25, 2025-26 |

Transfermarkt financial data (market value, contract, nationality) covers the 8 domestic leagues for 2024-25 at 99.6% match rate.

---

## Project structure

```
football-data-mcp/
├── collect_data.py          # Full data pipeline (scrape → merge → save)
├── soccer_server.py         # MCP server (8 tools)
├── src/ScraperFC/           # ScraperFC scrapers (upstream: oseymour/ScraperFC)
└── data/
    ├── unified_player_stats.csv   # Main merged dataset (gitignored)
    └── raw/                       # Per-source parquet files (gitignored)
```

---

## Contributing

This project builds on [ScraperFC](https://github.com/oseymour/ScraperFC). Bug fixes to the underlying scrapers are contributed back upstream — if you find something broken in a scraper, consider opening an issue or PR there too.

For issues specific to the pipeline (`collect_data.py`) or the MCP server (`soccer_server.py`), open an issue here.

---

## Credits

- [ScraperFC](https://github.com/oseymour/ScraperFC) by Owen Seymour — the foundation this project builds on
- Data sources: [FBref](https://fbref.com), [SofaScore](https://sofascore.com), [Understat](https://understat.com), [Transfermarkt](https://transfermarkt.us)
