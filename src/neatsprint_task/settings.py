from neatsprint_lib.settings import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "task"


settings = Settings()
