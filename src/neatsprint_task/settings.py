from neatsprint_lib.settings import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "task"
    api_prefix: str = "tasks"
    bootstrap_token: str | None = None
    service_token: str | None = None
    team_internal_url: str = "http://neatsprint-team:8000"
    outbox_enabled: bool = True
    outbox_stream: str = "neatsprint.events"
    s3_endpoint: str | None = None
    s3_public_endpoint: str | None = None
    s3_bucket: str = "neatsprint"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"


settings = Settings()
