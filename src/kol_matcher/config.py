from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    apify_token: SecretStr | None = None
    cache_path: str = "data/cache.duckdb"
    replay_mode: bool = False
    embedding_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    embedding_model: str = "bge-m3"
    max_results_per_query: int = 30
    max_creators_per_evaluate: int = 50
    cache_ttl_hours: float = 24
