from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# SQLite file lives alongside the app. Swap this URL for a Postgres URL later
# (e.g. postgresql://user:pass@host/dbname) with zero other code changes needed.
SQLALCHEMY_DATABASE_URL = "sqlite:///./richseed.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
