# Task Checklist

Order follows dependencies. Each task has a "done when" check.

## Fix first (repo vs. doc mismatches)

- [x] Add `[build-system]` to `pyproject.toml` (`uv_build` or `hatchling`) so entry points install
- [x] Add missing deps: `pydantic-settings`, `typer`, `numpy`
- [x] Fix `readme = "README.md"` (file does not exist)
- [x] Align Python version: doc says 3.12+, `pyproject.toml` says `>=3.13`
- [x] Rename `src/kol_matcher/source/` → `sources/`
- [x] Decide on `AGENT.md`: move content to `README.md`; add `CLAUDE.md` / `AGENTS.md` with coding rules (layer rule, no LLM in core, test command)

## Phase 0 — Walking skeleton

- [x] **T0.1** `config.py`: `Settings(BaseSettings)` with all `.env` keys; fill `.env.example` — done when `Settings()` loads
- [x] **T0.2** `server.py`: FastMCP app + `ping` tool + `main()` — done when MCP Inspector lists the tool
- [x] **T0.3** `cli.py`: Typer `app` with stub `analyze`, `match`, `backtest` — done when `uv run kol-matcher --help` works

## Phase 1 — Contracts (`schemas/`)

- [x] **T1.1** `BrandProfile` with enums, length limits (max 5 themes), field descriptions — done when invalid input gives a clear error and JSON Schema looks right
- [x] **T1.2** `Creator`, `Video`, `CommentStats`, `ScoreBreakdown`, `Flag`, tool I/O models; every metric tagged `measured` / `inferred`
- [x] **T1.3** `profile_hash()` — stable hash of canonical JSON
- [x] **T1.4** `Objective` enum + weights table

## Phase 2 — Storage (`storage/`)

- [x] **T2.1** DuckDB tables: `raw_responses`, `creators`, `creator_snapshots` (append-only, `scraped_at`), `videos`, `comment_stats` (no commenter IDs), `embeddings` (text hash + model), `shortlist_log`
- [x] **T2.2** Cache get/put keyed by actor + input hash, with staleness check
- [x] **T2.3** Retention purge + delete-by-handle (PDPA)

## Phase 3 — Sources (`sources/`, cache-first)

- [x] **T3.1** Website fetcher (httpx + trafilatura)
- [x] **T3.2** Apify Facebook page + posts
- [x] **T3.3** Apify TikTok keyword search + profile videos
- [x] **T3.4** Apify TikTok comments (top candidates only)
- [ ] **T3.5** Replay mode: serve `fixtures/` only, no external calls; record real responses into fixtures once
- [x] **T3.6** Embedding provider interface + Ollama BGE-M3 implementation

## Phase 4 — Core engine (`core/`, no MCP imports)

- [x] **T4.1** `analyze_brand`: collect, clean, return data + `BrandProfile` schema
- [x] **T4.2** Discover: run queries, Thai filter (Unicode regex on bio/captions, region), dedupe, record source query, enforce `MAX_RESULTS_PER_QUERY`
- [x] **T4.3** Enrich: recent videos, tier assignment, inactive filter (60 days)
- [x] **T4.4** Features
  - [x] Relevance: top-3 mean cosine + on-theme share
  - [x] Performance (Wisesight definitions): ER by impression + impressions/follower scored; ER by follower and medians display-only
  - [x] Audience: Thai comment share, purchase-intent rate
  - [x] Credibility: view consistency, recency, sponsored ratio, duplicate/emoji comments
- [x] **T4.5** Score: fixed 0-1 scales per creator (no pool normalization), weighted sum by objective — done when same input gives same output
- [x] **T4.6** Flags: brand safety, competitor mention, expertise titles (unverified), high sponsored ratio
- [ ] **T4.7** Template rationale string
- [ ] **T4.8** Shortlist: recompute metrics from cache, write CSV, append to log

## Phase 5 — MCP tools (`tools/`, thin adapters)

- [ ] **T5.1** `analyze_brand`
- [ ] **T5.2** `search_creators`
- [ ] **T5.3** `evaluate_creators` (cap 50 handles)
- [ ] **T5.4** `get_creator_detail`
- [ ] **T5.5** `save_shortlist`
- [ ] **T5.6** Register all tools in `server.py`
- [ ] Each tool: validates input, calls core, keeps scraped text in separate fields, returns `profile_hash`

## Phase 6 — CLI + skill

- [ ] **T6.1** CLI `analyze` and `match` wired to core
- [ ] **T6.2** `skills/kol-matcher/SKILL.md`: workflow including confirm-profile step

## Phase 7 — Evals

- [ ] **T7.1** Ground-truth CSV for one Thai brand with past collaborations
- [ ] **T7.2** `backtest` command: recall@K, brand name excluded from queries
- [ ] **T7.3** Baseline (AI client + raw Apify) and query-source experiment; fill TBD table

## Phase 8 — Tests + ship

- [ ] **T8.1** Unit tests: scoring, Thai filter, flags, `profile_hash` (on fixtures)
- [ ] **T8.2** Contract tests: tool I/O matches schemas
- [ ] **T8.3** Claude Desktop config verified, demo video, README final

## Scope cut (if time tight)

- **Must ship:** Phases 0–5, replay fixtures, one backtest
- **Cut first:** comments scraper (audience from captions only, labelled), baseline comparison, retention purge, CLI `match`
