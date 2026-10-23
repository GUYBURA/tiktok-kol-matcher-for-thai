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
    max_creators_per_evaluate: int = 20
    cache_ttl_hours: float = 24
