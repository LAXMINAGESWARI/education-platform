from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from education_platform.db.url import with_credentials

# backend/src/education_platform/core/config.py → backend/ is parents[3], repo root parents[4]
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_MATERIALS_DIR = _REPO_ROOT / "docs" / "curriculum"
_DEFAULT_DATABASE_URL = "postgresql+asyncpg://education:education@localhost:5432/education"
_DEFAULT_UPLOAD_DIR = _BACKEND_ROOT / "var" / "uploads"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CurioEd"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]
    # Starlette fullmatch against Origin. Blank keeps the explicit list only.
    # Quick Cloudflare tunnels set a trycloudflare.com pattern; that hostname
    # is chosen after the process starts.
    cors_origin_regex: str = ""
    materials_dir: Path = _DEFAULT_MATERIALS_DIR
    database_url: str = _DEFAULT_DATABASE_URL
    jwt_secret: str = "dev-only-change-me-use-32bytes-min!!"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60
    refresh_token_days: int = 14
    mastery_pass_percent: float = 70.0
    feedback_weak_threshold_percent: float = 70.0
    feedback_relative_sd: float = 1.0
    feedback_min_class_n: int = 5
    feedback_min_student_n: int = 3
    demo_student_email: str = "student@demo.school"
    demo_student_password: str = "demo1234"
    demo_admin_email: str = "admin@demo.school"
    demo_admin_password: str = "demo1234"
    upload_dir: Path = _DEFAULT_UPLOAD_DIR
    embedding_model_name: str = "all-MiniLM-L6-v2"
    max_upload_bytes: int = 20 * 1024 * 1024
    ingest_allowed_content_types: list[str] = ["application/pdf"]
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-4o-mini"
    adk_model: str = "openrouter/openai/gpt-4o-mini"
    adk_max_review_rounds: int = 4
    worker_lease_seconds: int = 300
    # Different model family from adk_model, so the check is not grading its own items.
    blind_solver_model: str = "anthropic/claude-haiku-5.5"
    chat_context_limit_tokens: int = 20_000
    chat_retrieval_limit: int = 6

    # Attendance below this percentage of the term makes a student exam-ineligible.
    # Sourced from Attendance Policy v3 section 2.1; the early-warning engine reads it.
    attendance_eligibility_percent: float = 75.0

    # A separate, least-privilege Postgres role for the text-to-SQL pipeline's own reads
    # (see execute_sql.py and migration c9d0e1f2a3b4) -- SELECT-only, granted on exactly
    # `load_schema.REQUIRED_TABLES`, with `users.password_hash`/`refresh_sessions.token_hash`
    # excluded at the column-privilege level and `question_answer_keys` excluded entirely.
    # `text_to_sql_db_password` reads the `TEXT_TO_SQL_DB_PASSWORD` env var (pydantic-settings'
    # default field->env mapping), and migration c9d0e1f2a3b4 reads that exact same env var
    # when it creates the role -- set it once before `alembic upgrade head` in any real
    # deployment and both sides agree automatically. The literal default below is a dev-only
    # fallback for when nothing sets that env var, same tier as `jwt_secret` above; it is
    # never used once the env var is set.
    text_to_sql_db_user: str = "text_to_sql_reader"
    text_to_sql_db_password: str = "text_to_sql_reader"

    @property
    def is_development(self) -> bool:
        return self.environment.lower() == "development"

    @property
    def openrouter_configured(self) -> bool:
        return bool(self.openrouter_api_key and self.openrouter_api_key.strip())

    @property
    def text_to_sql_database_url(self) -> str:
        """`database_url`'s own host/port/database, authenticated as the restricted
        text-to-SQL reader role instead of the application's full-access role.
        """
        return with_credentials(
            self.database_url,
            user=self.text_to_sql_db_user,
            password=self.text_to_sql_db_password,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
