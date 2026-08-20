"""
db_config.py
Centralised DB connection settings + engine factory.
Keeps credentials out of every individual script (fixes the
'hardcoded credentials' issue flagged in earlier code reviews).
"""

import os
from sqlalchemy import create_engine

# Prefer environment variables; fall back to local docker-compose defaults
DB_USER = os.getenv("KYC_DB_USER", "kyc_user")
DB_PASS = os.getenv("KYC_DB_PASS", "kyc_pass")
DB_HOST = os.getenv("KYC_DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("KYC_DB_PORT", "5432")
DB_NAME = os.getenv("KYC_DB_NAME", "kyc_db")

DB_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


def get_engine():
    """Return a SQLAlchemy engine (pooled, reusable)."""
    return create_engine(
        DB_URL,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
