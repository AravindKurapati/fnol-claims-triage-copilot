"""Environment-driven settings. Every threshold, price, model name and path lives here.

Rule R4: Gemini is the only model provider. There is no code path that constructs any other
provider's client, and `Settings.validate_provider_rules()` fails loudly if one is configured.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


class ConfigError(RuntimeError):
    """Raised at startup when the environment cannot support a run."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # --- model provider (Gemini only — Rule R4) ---
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    gemini_model: str = Field(default="gemini-3.5-flash-lite", alias="GEMINI_MODEL")
    gemini_judge_model: str = Field(default="gemini-3.1-flash-lite", alias="GEMINI_JUDGE_MODEL")
    gemini_temperature: float = Field(default=0.1, alias="GEMINI_TEMPERATURE")

    # --- cost basis for the golden-signals report (AC-09) ---
    # gemini-3.5-flash-lite list price, USD per 1K tokens ($0.30 / $2.50 per 1M), as published in
    # the `genai-prices` package (pydantic) on 2026-09-18. Override per model via env.
    price_per_1k_input: float = Field(default=0.00030, alias="PRICE_PER_1K_INPUT")
    price_per_1k_output: float = Field(default=0.00250, alias="PRICE_PER_1K_OUTPUT")
    # gemini-3.1-flash-lite (DeepEval judge) list price ($0.25 / $1.50 per 1M), same source.
    judge_price_per_1k_input: float = Field(default=0.00025, alias="JUDGE_PRICE_PER_1K_INPUT")
    judge_price_per_1k_output: float = Field(default=0.00150, alias="JUDGE_PRICE_PER_1K_OUTPUT")
    # Client-side pacing, requests per minute per model. The free tier allows 5 RPM on
    # gemini-3.6-flash and more on the flash-lite models (docs/failure-analysis.md F-03).
    gemini_rpm: int = Field(default=12, alias="GEMINI_RPM")

    # --- triage thresholds (SPEC-02 §2.5) ---
    fast_track_max_amount: float = Field(default=50_000, alias="FAST_TRACK_MAX_AMOUNT")
    high_value_threshold: float = Field(default=500_000, alias="HIGH_VALUE_THRESHOLD")
    investigate_min_indicators: int = Field(default=3, alias="INVESTIGATE_MIN_INDICATORS")
    fraud_medium_score: float = Field(default=0.35, alias="FRAUD_MEDIUM_SCORE")
    fraud_high_score: float = Field(default=0.65, alias="FRAUD_HIGH_SCORE")

    # --- graph limits (SPEC-02 §3.2, SPEC-06 §2.3) ---
    max_steps: int = Field(default=25, alias="MAX_STEPS")
    max_rag_hops: int = Field(default=3, alias="MAX_RAG_HOPS")
    context_budget_tokens: int = Field(default=6000, alias="CONTEXT_BUDGET_TOKENS")

    # --- resilience (NFR-04) ---
    tool_timeout_s: float = Field(default=20.0, alias="TOOL_TIMEOUT_S")
    model_timeout_s: float = Field(default=45.0, alias="MODEL_TIMEOUT_S")
    max_retries: int = Field(default=2, alias="MAX_RETRIES")

    # --- local storage (file-based only — Rule R4) ---
    checkpoint_db: str = Field(default="var/checkpoints.sqlite", alias="CHECKPOINT_DB")
    memory_db: str = Field(default="var/memory.sqlite", alias="MEMORY_DB")
    chroma_dir: str = Field(default="var/chroma", alias="CHROMA_DIR")
    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2", alias="EMBEDDING_MODEL"
    )

    # --- observability (Phase 4) ---
    phoenix_project_name: str = Field(default="fnol-triage", alias="PHOENIX_PROJECT_NAME")
    phoenix_collector_endpoint: str = Field(
        default="http://localhost:6006", alias="PHOENIX_COLLECTOR_ENDPOINT"
    )
    phoenix_enabled: bool = Field(default=True, alias="PHOENIX_ENABLED")

    # --- data generation (Rule R5) ---
    data_seed: int = Field(default=20260918, alias="DATA_SEED")

    # ---------- derived paths ----------
    @property
    def root(self) -> Path:
        return PROJECT_ROOT

    @property
    def data_dir(self) -> Path:
        return PROJECT_ROOT / "data"

    @property
    def policy_corpus_dir(self) -> Path:
        return self.data_dir / "policy_corpus"

    @property
    def sample_claims_dir(self) -> Path:
        return self.data_dir / "sample_claims"

    @property
    def logs_dir(self) -> Path:
        return PROJECT_ROOT / "logs"

    @property
    def runs_dir(self) -> Path:
        return PROJECT_ROOT / "runs"

    @property
    def checkpoint_path(self) -> Path:
        return self._abs(self.checkpoint_db)

    @property
    def memory_path(self) -> Path:
        return self._abs(self.memory_db)

    @property
    def chroma_path(self) -> Path:
        return self._abs(self.chroma_dir)

    def _abs(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else PROJECT_ROOT / p

    # ---------- startup validation ----------
    def require_api_key(self) -> str:
        """Fail fast and legibly when the only approved provider is unconfigured."""
        if not self.google_api_key:
            raise ConfigError(
                "GOOGLE_API_KEY is not set.\n"
                "  1. cp .env.example .env\n"
                "  2. put your Gemini API key in GOOGLE_API_KEY\n"
                "Gemini is the only approved model provider for this project (Rule R4)."
            )
        return self.google_api_key

    @staticmethod
    def validate_provider_rules() -> None:
        """Rule R4 guard: no other provider may be configured, even accidentally."""
        forbidden = [k for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY") if os.environ.get(k)]
        if forbidden:
            raise ConfigError(
                f"Disallowed provider credentials present in the environment: {', '.join(forbidden)}. "
                "Gemini is the only approved model provider (Rule R4)."
            )

    def ensure_dirs(self) -> None:
        for p in (self.logs_dir, self.runs_dir, self.checkpoint_path.parent,
                  self.memory_path.parent, self.chroma_path):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
