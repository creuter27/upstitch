import os
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from db import User
from models import FileContent

router = APIRouter()

# Security: only allow paths within the code directory (parent of gui-manager's parent).
# Resolves dynamically so the repo can be moved without editing this file.
# Layout: <code_dir>/gui-manager/backend/files_api.py → code_dir = parent.parent.parent
ALLOWED_BASE = Path(__file__).resolve().parent.parent.parent

LANGUAGE_MAP = {
    ".py": "python",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".sh": "shell",
    ".bash": "shell",
    ".txt": "plaintext",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
    ".env": "shell",
}


def detect_language(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return LANGUAGE_MAP.get(ext, "plaintext")


def validate_path(path: str) -> str:
    """Resolve and validate that the path is within the allowed base."""
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(ALLOWED_BASE):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be within {ALLOWED_BASE}",
        )
    return str(resolved)


@router.get("/api/files/read")
async def read_file(
    path: str,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Read a file and return its content with language detection."""
    resolved = validate_path(path)
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail=f"File not found: {resolved}")
    try:
        async with aiofiles.open(resolved, "r", encoding="utf-8", errors="replace") as f:
            content = await f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")
    return {
        "path": resolved,
        "content": content,
        "language": detect_language(resolved),
    }


@router.post("/api/files/write")
async def write_file(
    body: FileContent,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Write content to a file."""
    resolved = validate_path(body.path)
    try:
        async with aiofiles.open(resolved, "w", encoding="utf-8") as f:
            await f.write(body.content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {e}")
    return {"ok": True}
