from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://club:club@localhost:5432/court_booking"
    hold_ttl_minutes: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
