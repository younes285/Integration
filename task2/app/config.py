from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_client_id: str = Field(min_length=1)
    app_origin: str = "http://localhost:8000"
    database_url: str = "sqlite:///./users.db"
    session_cookie_secure: bool = False
    session_lifetime_seconds: int = Field(default=3600, ge=60, le=86400)
    csrf_secret_key: str = Field(min_length=32)

    @model_validator(mode="after")
    def validate_security(self):
        origin = urlsplit(self.app_origin)
        if (origin.scheme not in {"http", "https"} or not origin.hostname
                or origin.path or origin.query or origin.fragment or origin.username):
            raise ValueError("APP_ORIGIN must be an origin without a path or trailing slash")
        if self.csrf_secret_key == "replace-with-strong-random-secret":
            raise ValueError("Generate a random CSRF_SECRET_KEY; do not use the placeholder")
        if not self.session_cookie_secure and (
            origin.scheme != "http" or origin.hostname not in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("Insecure cookies are allowed only for local HTTP development")
        if origin.scheme == "https" and not self.session_cookie_secure:
            raise ValueError("HTTPS requires secure cookies")
        return self
