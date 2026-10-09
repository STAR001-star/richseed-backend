import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# In production (Render), set the DATABASE_URL environment variable to your
# Postgres connection string (from Neon, Supabase, or Render's own Postgres).
# Locally, if DATABASE_URL isn't set, it falls back to a SQLite file so you
# don't need Postgres installed on your own laptop just to test things.
SQLALCHEMY_DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./richseed.db")

# Some providers (Neon, Supabase, Heroku-style) hand out URLs starting with
# "postgres://" — SQLAlchemy 2.x needs "postgresql://" instead.
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
