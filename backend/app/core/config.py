from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    project_name: str = "SafeBank Agent API"
    api_version: str = "0.1.0"
    environment: str = "local"


settings = Settings()

