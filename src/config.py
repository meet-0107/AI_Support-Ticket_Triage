from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_PATH: str = "tickets.db"
    DUPLICATE_THRESHOLD: float = 0.9
    LLM_TIMEOUT_SECONDS: float = 10.0
    LLM_PROVIDER: str = "gemini"
    EMBEDDING_PROVIDER: Optional[str] = None
    LLM_PROVIDER_API_KEY: Optional[str] = None
    LLM_MODEL_NAME: str = ""
    LLM_EMBEDDING_MODEL_NAME: str = ""
    LLM_API_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"
    LLM_LOCAL_BASE_URL: str = "http://localhost:11434"
    LLM_BASE_URL: str = "http://localhost:11434"

    @property
    def effective_embedding_provider(self) -> str:
        return self.EMBEDDING_PROVIDER or self.LLM_PROVIDER
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    CONFIDENCE_THRESHOLD: float = 0.7
    STUCK_TIMEOUT_SECONDS: float = 30.0
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"
    CELERY_TASK_ALWAYS_EAGER: bool = False
    REDIS_API_KEY: Optional[str] = None
    REDIS_HOST: Optional[str] = None
    REDIS_PORT: int = 6379
    REDIS_SSL: bool = True
    UPSTASH_REDIS_REST_URL: Optional[str] = None
    UPSTASH_REDIS_REST_TOKEN: Optional[str] = None

    @property
    def broker_url(self) -> str:
        """Constructs Celery broker URL from Upstash, REDIS_HOST/KEY, or CELERY_BROKER_URL."""
        if self.UPSTASH_REDIS_REST_URL and self.UPSTASH_REDIS_REST_TOKEN:
            host = (
                self.UPSTASH_REDIS_REST_URL.replace("https://", "")
                .replace("http://", "")
                .strip("/")
            )
            return f"rediss://default:{self.UPSTASH_REDIS_REST_TOKEN}@{host}:6379/0?ssl_cert_reqs=CERT_NONE"

        if self.REDIS_HOST and self.REDIS_API_KEY:
            protocol = "rediss" if self.REDIS_SSL else "redis"
            ssl_param = "?ssl_cert_reqs=CERT_NONE" if self.REDIS_SSL else ""
            return f"{protocol}://default:{self.REDIS_API_KEY}@{self.REDIS_HOST}:{self.REDIS_PORT}/0{ssl_param}"

        url = self.CELERY_BROKER_URL
        if url.startswith("rediss://") and "ssl_cert_reqs" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}ssl_cert_reqs=CERT_NONE"
        return url

    @property
    def result_backend(self) -> str:
        """Constructs Celery result backend URL."""
        if self.UPSTASH_REDIS_REST_URL and self.UPSTASH_REDIS_REST_TOKEN:
            host = (
                self.UPSTASH_REDIS_REST_URL.replace("https://", "")
                .replace("http://", "")
                .strip("/")
            )
            return f"rediss://default:{self.UPSTASH_REDIS_REST_TOKEN}@{host}:6379/0?ssl_cert_reqs=CERT_NONE"

        if self.REDIS_HOST and self.REDIS_API_KEY:
            protocol = "rediss" if self.REDIS_SSL else "redis"
            ssl_param = "?ssl_cert_reqs=CERT_NONE" if self.REDIS_SSL else ""
            return f"{protocol}://default:{self.REDIS_API_KEY}@{self.REDIS_HOST}:{self.REDIS_PORT}/0{ssl_param}"

        url = self.CELERY_RESULT_BACKEND
        if url.startswith("rediss://") and "ssl_cert_reqs" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}ssl_cert_reqs=CERT_NONE"
        return url


settings = Settings()
