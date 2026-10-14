"""Central configuration.

Every tunable value lives here and is read from environment variables, so the same
container image runs locally, in CI and in production with different settings and
no code changes (twelve factor style).
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Secrets (never committed; Secret Manager in production)
    anthropic_api_key: str = ""
    database_url: str = ""
    ip_hash_salt: str = "change-me"

    # Models. Kept in config so you can swap models without touching code.
    small_model: str = "claude-haiku-4-5"
    large_model: str = "claude-sonnet-5-5"
    judge_model: str = "claude-sonnet-5-5"
    max_output_tokens: int = 600

    # Prices in USD per million tokens. Check Anthropic's pricing page and update.
    small_input_price: float = 1.0
    small_output_price: float = 5.0
    large_input_price: float = 3.0
    large_output_price: float = 15.0

    # Embeddings: small local model, so embedding costs nothing.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    model_cache_dir: str = "/models"

    # Retrieval
    candidates_per_search: int = 20   # results from each of vector and keyword search
    top_k: int = 6                    # chunks passed to the LLM
    rrf_k: int = 60                   # standard Reciprocal Rank Fusion constant
    scope_threshold: float = 0.55     # tune with: python -m evals.run_evals --mode retrieval

    # Cost and abuse controls
    rate_limit_per_hour: int = 20
    daily_budget_usd: float = 0.30   # about $9 a month worst case
    cache_ttl_days: int = 7

    # Web
    allowed_origins: str = "http://localhost:3000"

    prompt_version: str = "v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
