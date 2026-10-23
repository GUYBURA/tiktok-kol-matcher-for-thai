## Project Structure

```
tiktok-kol-matcher/
├── src/kol_matcher/      # Application package
│   ├── core/             # Matching engine (no MCP dependency)
│   ├── schemas/          # Shared data contracts (Pydantic)
│   ├── sources/          # External data collectors
│   ├── storage/          # DuckDB cache and persistence
│   ├── tools/            # MCP tool adapters
│   ├── config.py         # Settings loaded from .env
│   ├── server.py         # MCP server entry point
│   └── cli.py            # Command-line entry point
├── skills/               # Agent skill describing the workflow
├── evals/                # Backtest and baseline comparison
├── fixtures/             # Demo data for offline replay
├── tests/                # Unit and contract tests
├── docs/                 # Diagrams and design notes
└── data/                 # Local cache (git-ignored)
```

**Dependency rule:** imports flow one way — `tools → core → sources / storage`.
`schemas` is shared by every layer. `core` never imports MCP code.

### `src/kol_matcher/core/`
The matching engine. Holds the business logic for each pipeline stage:
collecting brand sources, discovering candidates (Thai-creator filtering and
de-duplication), enriching creators with their recent videos, computing
embeddings and features (engagement, relevance, audience proxy, credibility),
and scoring with objective-based weights. Because it has no MCP dependency,
the same logic runs from the MCP server, the CLI, and the test suite.

### `src/kol_matcher/schemas/`
Shared data contracts defined with Pydantic: `BrandProfile`, creator and video
models, and tool inputs/outputs such as score breakdowns. The server is
stateless, so `BrandProfile` is the main contract between the AI client and
the engine, and every tool validates it on input. Field descriptions in these
models become the JSON Schema that AI clients read.

### `src/kol_matcher/sources/`
Adapters for external data: Apify actors for TikTok (search, profiles,
comments) and Facebook (page info and posts), plus a website text fetcher.
Each adapter checks the cache before calling an external service and returns
typed models from `schemas/`.

### `src/kol_matcher/storage/`
DuckDB-backed cache and persistence: raw API responses, creators, append-only
creator snapshots, videos, aggregated comment statistics, embeddings (keyed by
text hash and model name), and the shortlist log written by `save_shortlist`.
Only aggregated comment statistics are stored; commenter identities are never
saved.

### `src/kol_matcher/tools/`
MCP tool definitions, one file per tool: `analyze_brand`, `search_creators`,
`evaluate_creators`, `get_creator_detail`, and `save_shortlist`. Tools are thin
adapters: they validate input, call `core/`, and shape the response for AI
clients. No business logic lives here.

### `config.py`, `server.py`, `cli.py`
- `config.py` loads settings from environment variables: API tokens, cache
  path, budget caps, and embedding provider.
- `server.py` creates the FastMCP server and registers every tool from `tools/`.
- `cli.py` calls `core/` directly. It runs backtests and baseline comparisons,
  and runs the pipeline without an AI client.

### `skills/`
`SKILL.md` describing the recommended workflow for AI clients: analyze the
brand, draft the profile, confirm it with the user, search, evaluate, review
evidence, and save the shortlist.

### `evals/`
Evaluation scripts. The backtest measures recall@K against creators who have
previously worked with a brand. The baseline comparison runs the same task
with an AI client using raw Apify tools, without this server.

### `fixtures/`
Committed demo data (brand profile JSON files and cached API responses), so
the project can be demonstrated and tested offline without API keys.

### `tests/`
Mirrors the `src/` layout. `core` tests run against fixtures instead of live
APIs; `tools` tests check that inputs and outputs match the schemas.

### `docs/`
Architecture diagrams and design notes.

### `data/` (git-ignored)
Local DuckDB cache created at runtime. Never committed.

### Root files

| File | Purpose |
|---|---|
| `.env.example` | Template listing the required environment variables |
| `.env` | Local secrets (git-ignored) |
| `pyproject.toml` | Project metadata, dependencies, and script entry points |
| `uv.lock` | Locked dependency versions for reproducible installs |
| `.python-version` | Python version used by uv |