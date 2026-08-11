from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost/moonlight_autopilot"
    llm_gateway_base_url: str = Field(
        default="http://localhost:9999/v1", validation_alias="LLM_GATEWAY_URL"
    )
    llm_gateway_api_key: str = Field(default="", validation_alias="LLM_GATEWAY_KEY")

    # Client Table source DB (Koushik's side — read-only, never migrated by us).
    client_db_host: str = Field(
        default="localhost", validation_alias="CONVERSATIONAL_EXPERIENCE_RDS_URL"
    )
    client_db_user: str = Field(default="", validation_alias="CONVERSATIONAL_EXPERIENCE_RDS_USER")
    client_db_password: str = Field(
        default="", validation_alias="CONVERSATIONAL_EXPERIENCE_RDS_WEATHERMAN_PASSWORD"
    )
    client_db_name: str = Field(
        default="weatherman", validation_alias="CONVERSATIONAL_EXPERIENCE_RDS_WEATHERMAN_DB"
    )

    @property
    def client_db_url(self) -> URL:
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.client_db_user,
            password=self.client_db_password,
            host=self.client_db_host,
            database=self.client_db_name,
        )

    avoma_base_url: str = Field(
        default="https://api.avoma.com", validation_alias="AVOMA_BASE_URL"
    )
    avoma_api_key: str = Field(default="", validation_alias="AVOMA_API_KEY")


settings = Settings()
