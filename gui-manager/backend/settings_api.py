from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from auth import get_current_user
from db import Setting, User, get_db
from models import SettingUpdate

router = APIRouter()


@router.get("/api/settings")
def get_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Return all settings as a key→value dict."""
    rows = db.query(Setting).all()
    return {row.key: row.value for row in rows}


@router.put("/api/settings")
def update_setting(
    body: SettingUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Upsert a single setting."""
    existing = db.query(Setting).filter(Setting.key == body.key).first()
    if existing:
        existing.value = body.value
    else:
        db.add(Setting(key=body.key, value=body.value))
    db.commit()
    return {"ok": True}
