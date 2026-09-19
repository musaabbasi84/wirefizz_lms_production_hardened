import os
from dotenv import load_dotenv

load_dotenv()


def as_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_database_url(url):
    """Accept the connection strings that Neon / Render / Heroku hand out.

    SQLAlchemy + psycopg (v3) needs the "postgresql+psycopg://" scheme, but
    providers commonly give "postgres://" or "postgresql://".
    """
    url = (url or "").strip().strip('"').strip("'")
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def parse_origins(raw):
    origins = []
    for item in (raw or "").split(","):
        item = item.strip().strip('"').strip("'").rstrip("/")
        if item:
            origins.append(item)
    return origins


APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"


class Config:
    APP_ENV = APP_ENV
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-me-too")
    SQLALCHEMY_DATABASE_URI = normalize_database_url(
        os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/wirefizz_lms",
        )
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Neon (and other serverless Postgres) close idle connections. Without
    # pre-ping the first request after idle time fails with "SSL connection
    # has been closed unexpectedly".
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
        "pool_size": int(os.getenv("DB_POOL_SIZE", "5")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "5")),
    }

    # Authentication: JWTs travel in the "Authorization: Bearer <token>" header.
    # The SPA (Vercel) and the API (Render) live on different sites, so
    # third-party cookies are blocked by Safari / Firefox / Chrome and cannot be
    # relied on. Header auth works everywhere and needs no CSRF token.
    JWT_TOKEN_LOCATION = ["headers"]
    JWT_HEADER_NAME = "Authorization"
    JWT_HEADER_TYPE = "Bearer"
    JWT_ACCESS_TOKEN_EXPIRES = int(os.getenv("JWT_ACCESS_TOKEN_SECONDS", str(60 * 60)))
    JWT_REFRESH_TOKEN_EXPIRES = int(os.getenv("JWT_REFRESH_TOKEN_SECONDS", str(7 * 24 * 60 * 60)))

    CORS_ORIGINS = parse_origins(
        os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    )
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024
    RATELIMIT_DEFAULT = os.getenv("RATELIMIT_DEFAULT", "300 per minute")
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_ENABLED = as_bool("RATELIMIT_ENABLED", True)
    AUTO_APPROVE_REGISTRATION = as_bool("AUTO_APPROVE_REGISTRATION", not IS_PRODUCTION)
    # Render / Vercel / nginx sit in front of the app and put the real client IP
    # in X-Forwarded-For. Needed for correct per-user rate limiting & audit IPs.
    TRUST_PROXY = as_bool("TRUST_PROXY", IS_PRODUCTION)
    UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "uploads"))
