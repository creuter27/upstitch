import os
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from db import User
from models import FileContent

router = APIRouter()

# Security: only allow paths within the code directory (parent of gui-manager's parent).
# On Windows, use abspath (not realpath/resolve) to keep the drive-letter form (T:\)
# instead of resolving to UNC (\\tresoritdrive_...\...) — the two forms are incompatible
# for is_relative_to(), causing false 403 denials on Tresorit virtual drives.
def _abspath(p: str) -> Path:
    """Normalise a path without resolving symlinks or virtual-drive mappings.

    On Windows, os.path.realpath / Path.resolve() converts Tresorit's T:\\ drive
    to its UNC form (\\\\tresoritdrive_...\\...).  Using abspath keeps the
    drive-letter form so paths from the file-tree (also abspath) stay comparable.
    """
    if os.name == "nt":
        return Path(os.path.abspath(p))
    return Path(p).resolve()

ALLOWED_BASE = _abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)

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
    resolved = _abspath(path)
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
