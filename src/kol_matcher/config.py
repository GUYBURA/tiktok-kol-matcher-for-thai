from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    apify_token: SecretStr | None = None
    # Apify caps total memory of concurrent runs (free plan: 16 GB). Actors default to 4 GB; all four
    # are pay-per-event, so a smaller run costs the same. 4 runs x 2 GB leaves headroom for stragglers.
    apify_max_concurrent_runs: int = Field(default=4, ge=1)
    apify_run_memory_mb: int = Field(default=2048, ge=1024)  # TikTok scraper minimum is 1024
    cache_path: str = "data/cache.duckdb"
    replay_mode: bool = False
    fixtures_path: str = "fixtures"
    shortlist_dir: str = "data/shortlists"   # git-ignored: shortlists hold creator data (PDPA)
    embedding_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    embedding_model: str = "bge-m3"
    max_results_per_query: int = 30
    max_creators_per_evaluate: int = 5    # each creator is ~$0.04 of Apify on a cold cache
    cache_ttl_hours: float = 24
    # Remote MCP clients (claude.ai through Claude Desktop) give up on a tool call after ~60 s, but a
    # cold search or evaluation takes minutes. Slow tools answer "still working" after this many
    # seconds and keep running; the same call again picks up the same job.
    tool_wait_seconds: float = Field(default=45, gt=0)
