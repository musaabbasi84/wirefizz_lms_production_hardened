from flask import request
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from flask_limiter import Limiter

from config import Config

db = SQLAlchemy()
jwt = JWTManager()
cors = CORS()


def client_ip():
    """Real client IP (first X-Forwarded-For entry when behind a trusted proxy)."""
    if Config.TRUST_PROXY:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first[:64]
    return (request.remote_addr or "unknown")[:64]


limiter = Limiter(key_func=client_ip)
