# football-data-mcp

A football analytics toolkit for Claude (and similar LLM tools) — player scouting, comparisons, market-value filters, expected-goals tables, match-by-match form, team attacking profiles, match search, shot maps, and more.

Built on top of [ScraperFC](https://github.com/oseymour/ScraperFC) by Owen Seymour.

---

## What it does

Combines player and match statistics into one dataset you can explore in conversation with Claude.

**Coverage:** 10 leagues · 3 seasons (2023-24, 2024-25, 2025-26) · 18,800+ player records

**Season stats** (one row per player per season):

| Kind of data | Where it’s available |
|--------------|-------------------|
| Goals, assists, minutes, shots, cards | All 10 leagues |
| Expected goals (xG), expected assists (xAG) | All 10 leagues — **Understat** model in top five; **SofaScore** model elsewhere (`xg_source` / `xag_source` columns) |
| Non-penalty xG, build-up xG, xG chain | Top five leagues only (Understat; not comparable to SofaScore xG) |
| Advanced passing & chance creation | Top five + Netherlands + Portugal — not Championship or European cups |
| Player ratings, duels, dribbles, big chances, and 80+ other performance metrics | All 10 leagues |
| Market value, contract end date, height, nationality | Domestic leagues — weakest for Champions League and Europa League |

**Match-by-match stats** (optional extra step when collecting data):

| Kind of data | Where it’s available |
|--------------|-------------------|
| Last N games, form, ratings, shot locations, team xG for/against | All 10 leagues (after match data is collected) |
| League tables ranked by xG (home / away / overall) | Top five leagues only |

---

## Leagues covered

All leagues include three seasons: **2023-24**, **2024-25**, and **2025-26**.

| League | Season-level data | Match-by-match |
|--------|-------------------|----------------|
| England Premier League | **Full** — xG, build-up, advanced passing, ratings, market value | Yes |
| Spain La Liga | **Full** | Yes |
| Germany Bundesliga | **Full** | Yes |
| Italy Serie A | **Full** | Yes |
| France Ligue 1 | **Full** | Yes |
| Netherlands Eredivisie | **Strong** — xG, advanced passing, ratings, market value (no build-up xG) | Yes |
| Portugal Primeira Liga | **Strong** | Yes |
| England EFL Championship | **Basic** — ratings and core stats; lighter xG; market value often available | Yes |
| UEFA Champions League | **Basic** — ratings and core stats; no market value | Yes |
| UEFA Europa League | **Basic** — ratings and core stats; no market value | Yes |

**Full** = goals and minutes, full xG suite including build-up, advanced passing metrics, player ratings, and market value.

**Strong** = same as Full except build-up xG.

**Basic** = goals, minutes, player ratings, and xG-style metrics; limited advanced passing; European cups lack market value.

Market value and contract data are most complete for the eight main domestic leagues (all except Championship and the two European cups).

---

## The 16 tools

Once connected, Claude can answer questions using **16 built-in tools**.

### Season-level player data

| Tool | What you can ask |
|------|-----------------|
| `get_player` | "Show me everything on Bukayo Saka" |
| `scout_position` | "Top 10 forwards in the Bundesliga this season by xG" |
| `compare_players` | "Compare Salah and Son across all stats" |
| `find_similar_players` | "Find players similar to Bellingham under €80m" |
| `get_league_table` | "xG league table for Serie A, home games only" (top five leagues) |
| `get_match` | Shot map and line-ups for a specific match (top five leagues) |
| `get_sofascore_match` | Deep stats for one fixture — players, teams, shots |
| `get_club_elo` | "How strong is Real Madrid right now?" |
| `get_player_history` | Per-match form (xG, goals, assists) from Understat; TM value/contract via `get_player` |
| `data_status` | What data you have loaded and how complete it is |

### Match-by-match analytics

Requires match data to be collected first. Works across all 10 leagues.

| Tool | What you can ask |
|------|-----------------|
| `get_player_match_log` | "Salah's last 10 Premier League matches with ratings and xG" |
| `get_player_form` | "Haaland's average rating and xG per 90 over recent games" |
| `get_team_stats` | "Arsenal's average xG for and against this season" |
| `compare_teams` | "Compare Liverpool and Man City on xG and possession" |
| `search_matches` | "High-xG Premier League games this season" |
| `get_player_shot_map` | "Shot locations and xG for Kane in the Bundesliga" |

---

## Setup

Everything runs on your computer: download the stats, then connect Claude Desktop or Cursor so it can answer questions using the **16 tools**.

### 1. Install

```bash
pip install football-data-mcp
```

That installs two commands you can run from any folder:

- **`collect-data`** — downloads and builds the dataset
- **`soccer-mcp`** — starts the connection Claude and Cursor use

**Working on the code?** Clone this repo and run `pip install -e .` in the project folder instead.

### 2. Collect the data

First-time full download takes a while (some sites open a headless browser in the background).

Stats are collected from **Understat**, **SofaScore**, **ClubElo**, **Transfermarkt**, and **Capology** (see `CHANGELOG.md` for recent pipeline changes).

The unified player file also uses the **[REEP](https://github.com/robbyhecht/reep)** (Robust Entity Exchange Protocol) crosswalk (`data/reference/reep_people.csv`) to link player IDs across sources when names differ — for example matching Understat season rows to SofaScore dribble/passing stats, and SofaScore IDs to EA FC / SoFIFA attributes.

```bash
collect-data
```

Useful shortcuts:

```bash
# Only refresh one part of the data
collect-data --sofascore-only
collect-data --understat-only
collect-data --transfermarkt-only

# Extra: league xG tables, match shots, line-ups
collect-data --understat-tables-only
collect-data --understat-matches-only

# Rebuild the merged player file from files you already downloaded
collect-data --rebuild-only
collect-data --rebuild-only --export-csv   # also write a spreadsheet copy
```

### 3. Connect Claude Desktop or Cursor

Add the data connection to your app’s config. After `pip install`, **`soccer-mcp`** should be on your PATH (same program as `python3 -m soccer_server`).

**Claude Desktop** (macOS config file):

`~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "soccer-data": {
      "command": "soccer-mcp"
    }
  }
}
```

**Cursor** — `~/.cursor/mcp.json` or `.cursor/mcp.json` in a project:

```json
{
  "mcpServers": {
    "soccer-data": {
      "command": "soccer-mcp"
    }
  }
}
```

If the app cannot find `soccer-mcp`, use the full path from `which soccer-mcp` as `"command"`, or:

```json
"command": "python3",
"args": ["-m", "soccer_server"]
```

**Quit and reopen** Claude or Cursor after saving. You should see all **16 tools** after step 2 has finished downloading data.

<!--
### Hosted service (coming later) — `mcp.kupsas.com`

Use the managed MCP endpoint (no local scraping). You need an **API key** and a short client config.

**Endpoint:** `https://mcp.kupsas.com/football-data/mcp`  
**Health check:** `https://mcp.kupsas.com/football-data/health`

#### Get an API key

Until a web dashboard ships (`platform.kupsas.com`), mint a key from the terminal:

1. Sign in to the hosted Supabase project (Google SSO or email) and obtain a short-lived **access JWT** (Supabase Auth token endpoint or your app).
2. Exchange it for a long-lived MCP API key:

```bash
export SUPABASE_JWT="paste-your-supabase-access-token-here"

curl -sS -X POST "https://mcp.kupsas.com/football-data/api/keys" \
  -H "Authorization: Bearer $SUPABASE_JWT" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-laptop"}'
```

Copy the `"key"` from the JSON response **once** — it cannot be retrieved again. Store it in a password manager or an env var (do not commit it to git).

```bash
export FOOTBALL_MCP_API_KEY="paste-key-here"
```

#### Cursor (recommended — native HTTPS)

Edit `~/.cursor/mcp.json` (or `.cursor/mcp.json` in a repo):

```json
{
  "mcpServers": {
    "football-hosted": {
      "url": "https://mcp.kupsas.com/football-data/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_API_KEY"
      }
    }
  }
}
```

Restart Cursor → **Settings → MCP** should show **connected** and list tools. In **Agent** chat, name tools explicitly when needed, e.g. *“Use the get_league_table MCP tool for England Premier League 2024-2025.”*

#### Claude Desktop (requires `mcp-remote` bridge)

Claude Desktop does **not** support `"url"` / remote HTTP in config — only local subprocesses. Use **[mcp-remote](https://www.npmjs.com/package/mcp-remote)** so Claude talks stdio to a local bridge that calls HTTPS:

```json
{
  "mcpServers": {
    "football-hosted": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://mcp.kupsas.com/football-data/mcp",
        "--header",
        "Authorization: Bearer YOUR_MCP_API_KEY"
      ]
    }
  }
}
```

Requires **Node.js** / `npx`. **Quit Claude completely** (Cmd+Q), reopen, wait for first-time `npx` download. Logs: `~/Library/Logs/Claude/mcp*.log`.

#### Verify from the terminal (optional)

```bash
curl -sS "https://mcp.kupsas.com/football-data/health" | python3 -m json.tool

curl -sS -X POST "https://mcp.kupsas.com/football-data/mcp" \
  -H "Authorization: Bearer $FOOTBALL_MCP_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'
```

Expect HTTP **200** (401 = bad key; 406 = server needs `json_response=True` — contact the operator).

#### Hosted troubleshooting (short)

| Issue | Fix |
| --- | --- |
| Claude: server missing | Use `mcp-remote` config above, not `"type": "http"` |
| 401 | Mint a new key; check Bearer prefix |
| Cursor: connected but no tool use | Agent mode; mention tool name in the prompt |

More detail for operators: private deploy docs (Phase 6 + client §9).

-->
---

## Contributing

This project builds on [ScraperFC](https://github.com/oseymour/ScraperFC). Bug fixes to the underlying scrapers are contributed back upstream — if you find something broken in a scraper, consider opening an issue or PR there too.

For issues specific to the pipeline (`collect_data` package / `collect-data` / `collect_data.py`) or the MCP server (`soccer_server` package / `soccer-mcp` / ``python -m soccer_server``), open an issue here.

---

## Credits

- [ScraperFC](https://github.com/oseymour/ScraperFC) by Owen Seymour — the foundation this project builds on
- Data sources: [Understat](https://understat.com), [SofaScore](https://sofascore.com), [ClubElo](http://clubelo.com), [Transfermarkt](https://transfermarkt.us), [Capology](https://capology.com)
