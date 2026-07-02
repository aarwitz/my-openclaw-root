from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from pathlib import Path


def canonical_workspace_db_path() -> Path:
    # backend/database.py -> backend -> task-manager -> dwight workspace root
    return Path(__file__).resolve().parents[2] / "taskmanager.db"


db_url = os.environ.get("TASKMANAGER_DB_URL", "").strip()
db_path = os.environ.get("TASKMANAGER_DB_PATH", "").strip()

if db_url:
    SQLALCHEMY_DATABASE_URL = db_url
elif db_path:
    resolved = Path(db_path).expanduser().resolve()
    SQLALCHEMY_DATABASE_URL = f"sqlite:///{resolved}"
else:
    SQLALCHEMY_DATABASE_URL = f"sqlite:///{canonical_workspace_db_path()}"

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
