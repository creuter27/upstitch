import os
import subprocess
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from db import User

router = APIRouter()

TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
# Root of the gui-manager project (parent of backend/ and tools/)
GUI_MANAGER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SKIP_DIRS = {".tmp", "__pycache__", ".venv", ".git"}
SKIP_EXTS = {".pyc"}


def resolve_tool_path(raw_path: str) -> str:
    """Resolve a tool's path (possibly relative) to an absolute path.

    os.path.realpath() on Windows resolves Tresorit virtual-drive paths (T:\\)
    to their UNC form (\\\\tresoritdrive_...\\...), which breaks pushd.
    Use normpath+abspath instead to keep the drive-letter form.
    On Mac/Linux realpath is still used to resolve any symlinks.
    """
    joined = os.path.normpath(os.path.join(GUI_MANAGER_ROOT, raw_path))
    if os.name == "nt":
        return os.path.abspath(joined)
    return os.path.realpath(joined)


def _normalize_command(command: str, tool_path: str = "") -> str:
    """On Windows, rewrite Unix venv paths to their Windows equivalents.

    Venvs live outside Tresorit at %LOCALAPPDATA%\\upstitch-venvs\\<ProjectName>.
    The project name is the basename of the tool's resolved path.
    """
    if os.name != "nt":
        return command
    import re
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if tool_path and localappdata:
        tool_name = os.path.basename(tool_path)
        ext_venv = os.path.join(localappdata, "upstitch-venvs", tool_name)
        # .venv/bin/python  →  C:\Users\...\AppData\Local\upstitch-venvs\<name>\Scripts\python
        command = re.sub(
            r"\.venv/bin/(python3?)",
            lambda m: os.path.join(ext_venv, "Scripts", m.group(1)),
            command,
        )
    else:
        # fallback: fix path separators only
        command = re.sub(r"(\.venv)/bin/(python3?)", r"\1\\Scripts\\\2", command)

    # ./foo.sh → foo.bat  (shell scripts don't run on Windows CMD)
    command = re.sub(r"^\./([^\s]+)\.sh\b", r"\1.bat", command)

    return command


def load_tool_manifests() -> list[dict]:
    """Load all *.yaml files from the tools directory."""
    manifests = []
    tools_path = Path(TOOLS_DIR)
    if not tools_path.exists():
        return manifests
    for yaml_file in sorted(tools_path.glob("*.yaml"), key=lambda p: p.name):
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data:
                # Always expose an absolute path so the frontend can cd to it directly
                if "path" in data:
                    data["path"] = resolve_tool_path(data["path"])
                for fn in data.get("functions", []):
                    if "command" in fn:
                        fn["command"] = _normalize_command(fn["command"], tool_path=data.get("path", ""))
                manifests.append(data)
        except Exception as e:
            print(f"[tools_api] Failed to load {yaml_file}: {e}")
    manifests.sort(key=lambda m: m.get("sidebar_order", 99))
    return manifests


def get_tool_by_id(tool_id: str) -> dict | None:
    for manifest in load_tool_manifests():
        if manifest.get("id") == tool_id:
            return manifest
    return None


def build_file_tree(base_path: str, rel_path: str) -> dict[str, Any]:
    """Recursively build a file tree node."""
    abs_path = os.path.join(base_path, rel_path) if rel_path else base_path
    name = os.path.basename(abs_path) or abs_path

    if os.path.isfile(abs_path):
        ext = os.path.splitext(name)[1].lower()
        if ext in SKIP_EXTS:
            return None
        return {"name": name, "path": abs_path, "type": "file"}

    if os.path.isdir(abs_path):
        dir_name = os.path.basename(abs_path)
        if dir_name in SKIP_DIRS:
            return None
        children = []
        try:
            entries = sorted(os.listdir(abs_path))
        except PermissionError:
            entries = []
        for entry in entries:
            if entry in SKIP_DIRS or entry.startswith("."):
                continue
            child_abs = os.path.join(abs_path, entry)
            child_node = build_file_tree(child_abs, "")
            if child_node is not None:
                children.append(child_node)
        return {"name": name, "path": abs_path, "type": "dir", "children": children}

    return None


# ---------------------------------------------------------------------------
# Reorder helpers
# ---------------------------------------------------------------------------


def get_external_python(tool_path: str) -> str:
    """Return the path to Python in the tool's external upstitch-venvs directory."""
    tool_name = os.path.basename(tool_path)
    if os.name == "nt":
        localappdata = os.environ.get("LOCALAPPDATA", "")
        return os.path.join(localappdata, "upstitch-venvs", tool_name, "Scripts", "python.exe")
    home = os.path.expanduser("~")
    return os.path.join(home, ".local", "share", "upstitch-venvs", tool_name, "bin", "python")


def catalog_cache_exists(tool_path: str, mfr_code: str) -> bool:
    """Return True if .tmp/{mfr}_catalog.json exists — indicates sheet was created."""
    cache = os.path.join(tool_path, ".tmp", f"{mfr_code}_catalog.json")
    return os.path.isfile(cache)


def load_manufacturers(tool_path: str) -> list[dict]:
    """Load manufacturers with reordering config from mappings/products.yaml."""
    products_yaml = os.path.join(tool_path, "mappings", "products.yaml")
    if not os.path.exists(products_yaml):
        return []
    with open(products_yaml, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    result = []
    for code, info in (data.get("manufacturers") or {}).items():
        if not info or not info.get("reorderingURL"):
            continue
        tokens = info.get("tokens") or []
        result.append({
            "code": code,
            "name": tokens[0] if tokens else code,
            "reorderingURL": info["reorderingURL"],
            "useNoCrawl": bool(info.get("useNoCrawl", False)),
            "pythonCmd": get_external_python(tool_path),
        })
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/tools")
def list_tools(current_user: User = Depends(get_current_user)) -> list[dict]:
    """Return all tool manifests."""
    return load_tool_manifests()


@router.get("/api/tools/{tool_id}")
def get_tool(tool_id: str, current_user: User = Depends(get_current_user)) -> dict:
    """Return a single tool manifest by ID."""
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    return manifest


@router.get("/api/tools/{tool_id}/manufacturers")
def get_tool_manufacturers(
    tool_id: str, current_user: User = Depends(get_current_user)
) -> list[dict]:
    """Return manufacturers with reordering config for a tool."""
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    tool_path = resolve_tool_path(manifest.get("path", ""))
    return load_manufacturers(tool_path)


@router.get("/api/tools/{tool_id}/manufacturers/{code}/sheet-exists")
def check_sheet_exists(
    tool_id: str, code: str, current_user: User = Depends(get_current_user)
) -> dict:
    """Check whether the Google Sheet for a manufacturer exists via gspread."""
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    tool_path = resolve_tool_path(manifest.get("path", ""))
    python = get_external_python(tool_path)
    exec_dir = os.path.join(tool_path, "execution")
    sheet_name = f"Billbee Artikelmanager {code}"
    script = (
        "import sys, os; "
        f"sys.path.insert(0, {repr(exec_dir)}); "
        "from google_sheets_client import open_sheet_by_name; "
        f"open_sheet_by_name({repr(sheet_name)}); "
        "print('exists')"
    )
    try:
        result = subprocess.run(
            [python, "-c", script],
            capture_output=True, text=True, timeout=20
        )
        exists = result.stdout.strip() == "exists"
    except Exception:
        exists = False
    return {"exists": exists}


@router.get("/api/tools/{tool_id}/filetree")
def get_tool_filetree(
    tool_id: str, current_user: User = Depends(get_current_user)
) -> list[dict]:
    """Return a nested file tree for the tool's file_tree entries."""
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")

    raw_path = manifest.get("path", "")
    # Resolve relative paths against the gui-manager root so yamls can use ../ProjectName
    tool_path = os.path.realpath(os.path.join(GUI_MANAGER_ROOT, raw_path))
    file_tree_entries: list[str] = manifest.get("file_tree", [])

    results = []
    for entry in file_tree_entries:
        # Normalize: strip trailing slash for path joining, but keep dir semantics
        entry_clean = entry.rstrip("/")
        abs_entry = os.path.join(tool_path, entry_clean) if not os.path.isabs(entry_clean) else entry_clean

        node = build_file_tree(abs_entry, "")
        if node is not None:
            results.append(node)

    return results
