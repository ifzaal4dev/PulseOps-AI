"""Application settings, read from PULSEOPS_* environment variables.

Local runs and tests default to a single-file SQLite database. The Docker
stack uses PostgreSQL; switching only needs PULSEOPS_DATABASE_URL because all
queries go through SQLAlchemy.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PULSEOPS_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./pulseops.db"
    cors_origin: str = "http://localhost:3000"
    # POST /api/demo/reset drops and reseeds the whole database. Convenient
    # for local demos; set PULSEOPS_ENABLE_DEMO_RESET=false on any shared
    # deployment.
    enable_demo_reset: bool = True

    # --- Hybrid reasoning. Provider API keys come ONLY from
    # environment variables (GEMINI_API_KEY/GOOGLE_API_KEY, OPENROUTER_API_KEY,
    # ANTHROPIC_API_KEY) — never from settings files, logs, or NEXT_PUBLIC_*.
    reasoning_mode: str = "deterministic"  # deterministic | llm | hybrid
    llm_provider: str = "gemini"  # gemini | openrouter | anthropic
    # Provider-specific model id. Deliberately EMPTY by default: when
    # reasoning_mode != deterministic you must configure it explicitly
    # (documented examples in .env.example) — no invented universal default.
    llm_model: str = ""
    llm_timeout_seconds: float = 20.0
    llm_max_output_tokens: int = 1500
    llm_temperature: float = 0.1
    max_diagnostic_rounds: int = 1  # bounded next-best-test rounds per diagnosis
    reasoning_prompt_version: str = "prompt_v3"


settings = Settings()
