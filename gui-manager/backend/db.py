import json
import os
from datetime import datetime

from passlib.context import CryptContext
from sqlalchemy import Column, DateTime, Integer, String, Boolean, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)

DATABASE_URL = f"sqlite:///{os.path.join(DATA_DIR, 'app.db')}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    # JSON string, e.g. '["admin","billbee"]'
    permissions = Column(Text, default='[]')
    created_at = Column(DateTime, default=datetime.utcnow)

    def get_permissions(self) -> list[str]:
        try:
            return json.loads(self.permissions or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    def set_permissions(self, perms: list[str]) -> None:
        self.permissions = json.dumps(perms)


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True, unique=True, index=True, nullable=False)
    value = Column(Text, default="")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_db():
    """FastAPI dependency — yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables and seed default admin user if none exist."""
    Base.metadata.create_all(bind=engine)

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    db: Session = SessionLocal()
    try:
        # Seed admin user
        if db.query(User).count() == 0:
            admin = User(
                username="admin",
                hashed_password=pwd_context.hash("admin"),
                is_active=True,
                permissions=json.dumps(["admin", "billbee"]),
            )
            db.add(admin)
            db.commit()
            print("[db] Seeded default admin user (username=admin, password=admin)")

        # Seed default settings
        defaults = {
            "start_url": "https://google.com",
        }
        for key, value in defaults.items():
            existing = db.query(Setting).filter(Setting.key == key).first()
            if existing is None:
                db.add(Setting(key=key, value=value))
        db.commit()
    finally:
        db.close()
