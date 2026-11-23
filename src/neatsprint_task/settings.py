from neatsprint_lib.settings import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "task"
    api_prefix: str = "tasks"
    bootstrap_token: str | None = None


settings = Settings()
