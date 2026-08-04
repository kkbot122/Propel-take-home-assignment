from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "propel-backend"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://propel:propel@localhost:5432/propel"
    redis_url: str = "redis://localhost:6379/0"
    dependency_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_pool_overflow: int = Field(default=5, ge=0, le=100)
    telemetry_stream_name: str = Field(default="propel:telemetry", min_length=1, max_length=128)
    telemetry_request_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    telemetry_max_request_bytes: int = Field(default=16_384, ge=1_024, le=1_048_576)
    telemetry_consumer_group: str = Field(
        default="propel-telemetry-workers", min_length=1, max_length=128
    )
    telemetry_consumer_name: str = Field(default="telemetry-worker-1", min_length=1, max_length=128)
    telemetry_consumer_batch_size: int = Field(default=50, ge=1, le=500)
    telemetry_consumer_block_ms: int = Field(default=1_000, ge=1, le=30_000)
    telemetry_pending_idle_ms: int = Field(default=5_000, ge=0, le=300_000)
    telemetry_max_deliveries: int = Field(default=3, ge=1, le=100)
    telemetry_dead_letter_stream_name: str = Field(
        default="propel:telemetry:dead-letter", min_length=1, max_length=128
    )
    analysis_due_set_name: str = Field(default="propel:analysis:due", min_length=1, max_length=128)
    analysis_debounce_seconds: float = Field(default=10.0, ge=0, le=300)
    analysis_live_freshness_seconds: float = Field(default=1_920, gt=0, le=86_400)
    analysis_retry_delay_seconds: float = Field(default=5.0, gt=0, le=300)
    analysis_dt_fault_ratio: float = Field(default=0.6, gt=0, le=1)
    analysis_dt_min_branches: int = Field(default=2, ge=1, le=100)
    analysis_feeder_fault_ratio: float = Field(default=0.6, gt=0, le=1)
    analysis_feeder_min_dts: int = Field(default=2, ge=2, le=100)
    analysis_correlation_window_seconds: float = Field(default=10.0, gt=0, le=300)
    scheduled_outage_early_grace_seconds: float = Field(default=600, ge=0, le=3_600)
    scheduled_outage_overrun_grace_seconds: float = Field(default=2_400, ge=0, le=14_400)
    worker_retry_delay_seconds: float = Field(default=1.0, gt=0, le=30)
    restoration_threshold: float = Field(default=0.8, gt=0, le=1)
    restoration_stabilization_seconds: float = Field(default=10.0, ge=0, le=300)
    simulator_telemetry_url: str = Field(
        default="http://127.0.0.1:8000/api/telemetry", min_length=1, max_length=512
    )
    simulator_request_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    simulator_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
