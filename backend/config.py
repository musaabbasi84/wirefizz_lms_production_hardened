import os
from dotenv import load_dotenv

load_dotenv()

def as_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-me-too")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/wirefizz_lms")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    JWT_TOKEN_LOCATION = ["cookies"]
    JWT_ACCESS_COOKIE_NAME = "wf_access"
    JWT_REFRESH_COOKIE_NAME = "wf_refresh"
    JWT_ACCESS_TOKEN_EXPIRES = 15 * 60
    JWT_REFRESH_TOKEN_EXPIRES = 7 * 24 * 60 * 60
    JWT_COOKIE_SECURE = as_bool("JWT_COOKIE_SECURE", False)
    JWT_COOKIE_SAMESITE = os.getenv("JWT_COOKIE_SAMESITE", "Lax")
    JWT_COOKIE_CSRF_PROTECT = True
    JWT_ACCESS_CSRF_COOKIE_NAME = "wf_access_csrf"
    JWT_REFRESH_CSRF_COOKIE_NAME = "wf_refresh_csrf"
    JWT_CSRF_HEADER_NAME = "X-CSRF-TOKEN"

    CORS_ORIGINS = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if x.strip()]
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024
    RATELIMIT_DEFAULT = os.getenv("RATELIMIT_DEFAULT", "200 per minute")
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "redis://127.0.0.1:6379/0") if os.getenv("APP_ENV", "development").lower() == "production" else os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    AUTO_APPROVE_REGISTRATION = as_bool("AUTO_APPROVE_REGISTRATION", False if os.getenv("APP_ENV", "development").lower() == "production" else True)
    APP_ENV = os.getenv("APP_ENV", "development")
    UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "uploads"))
