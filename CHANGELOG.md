# Changelog

## Unreleased

- **Removed FBref collection.** The pipeline no longer scrapes or merges FBref data (formerly dropped due to rate limits and Chrome dependency). Unified player stats are built from Understat (Big 5 base), SofaScore, and financial sources (Transfermarkt, Capology), with ClubElo for team strength context.
- **Removed Transfermarkt career history.** No `transfermarkt_mv_history__*` or `transfermarkt_transfers__*` parquets, ceapi MV/transfer backfill, or MCP `type=value`/`type=transfers`. Flat `transfermarkt__{league}__{season}.parquet` profiles (current value, contract, team) and `--transfermarkt-only` collection are unchanged.
