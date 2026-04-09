import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from auth import get_current_user
from db import User
from models import PackagingUpdate, StockQueryRequest, StockUpdateRequest, AddStockApplyRequest

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


@router.get("/api/tools/{tool_id}/manufacturers/{code}/add-stock-preview")
def get_add_stock_preview(
    tool_id: str, code: str, current_user: User = Depends(get_current_user),
    tab: str = "",
) -> dict:
    """
    Preview Billbee stock update from the newest (or specified) order tab.

    Reads checked rows from '{CODE} Orders / Order YYYY-MM-DD', fetches live
    Billbee stock, and returns a table of pending changes.

    Returns {"tab": "...", "items": [...], "errors": [...]}.
    """
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    tool_path = resolve_tool_path(manifest.get("path", ""))
    python = get_external_python(tool_path)
    script = os.path.join(tool_path, "execution", "gui_add_stock_preview.py")

    args = ["--manufacturer", code.upper()]
    if tab:
        args += ["--tab", tab]

    # Allow ~1 s per item plus overhead; use 300 s cap for unknown list size
    return _run_tool_script(python, script, args, timeout=300)


@router.post("/api/tools/{tool_id}/manufacturers/{code}/add-stock-apply")
def post_add_stock_apply(
    tool_id: str,
    code: str,
    body: AddStockApplyRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Apply the ordered quantities to Billbee stock.

    Body: {"items": [{"sku": "...", "billbeeId": N, "qty": N}, ...]}
    Returns {"ok": true, "updated": N, "errors": [...]}.
    """
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    tool_path = resolve_tool_path(manifest.get("path", ""))
    python = get_external_python(tool_path)
    script = os.path.join(tool_path, "execution", "gui_add_stock_apply.py")

    items_json = json.dumps(body.items)
    timeout = max(30, len(body.items) * 2 + 15)
    return _run_tool_script(python, script,
                            ["--manufacturer", code.upper(), "--items", items_json],
                            timeout=timeout)


@router.get("/api/tools/{tool_id}/packaging")
def get_packaging(tool_id: str, current_user: User = Depends(get_current_user)) -> dict:
    """Return package type mappings and available package types for a tool."""
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    tool_path = resolve_tool_path(manifest.get("path", ""))

    mapping_file = os.path.join(tool_path, "data", "package_type_mapping.json")
    raw_mappings: dict = {}
    if os.path.isfile(mapping_file):
        with open(mapping_file, "r", encoding="utf-8") as f:
            raw_mappings = json.load(f) or {}

    package_types_file = os.path.join(tool_path, "data", "package_types.yaml")
    package_types: list = []
    if os.path.isfile(package_types_file):
        with open(package_types_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            package_types = data.get("package_types", [])

    mappings = [
        {"comboKey": k, "name": v.get("name", ""), "id": v.get("id"), "setAt": v.get("set_at")}
        for k, v in raw_mappings.items()
    ]
    return {"mappings": mappings, "packageTypes": package_types}


@router.post("/api/tools/{tool_id}/packaging/update")
def update_packaging(
    tool_id: str,
    body: PackagingUpdate,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Update the package type for a combo key."""
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    tool_path = resolve_tool_path(manifest.get("path", ""))

    mapping_file = os.path.join(tool_path, "data", "package_type_mapping.json")
    raw_mappings: dict = {}
    if os.path.isfile(mapping_file):
        with open(mapping_file, "r", encoding="utf-8") as f:
            raw_mappings = json.load(f) or {}

    # Resolve id from package_types.yaml
    package_types_file = os.path.join(tool_path, "data", "package_types.yaml")
    pkg_id = None
    if os.path.isfile(package_types_file):
        with open(package_types_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            for pt in data.get("package_types", []):
                if pt.get("name") == body.name:
                    pkg_id = pt.get("id")
                    break

    raw_mappings[body.comboKey] = {
        "name": body.name,
        "id": pkg_id,
        "set_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(mapping_file, "w", encoding="utf-8") as f:
        json.dump(raw_mappings, f, indent=2, ensure_ascii=False)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Design import rules
# ---------------------------------------------------------------------------

_RULES_FILE_REL = os.path.join("config", "design_import_rules.yaml")


@router.get("/api/tools/{tool_id}/design-rules")
def get_design_rules(tool_id: str, current_user: User = Depends(get_current_user)) -> dict:
    """Return design import rules from config/design_import_rules.yaml."""
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    tool_path = resolve_tool_path(manifest.get("path", ""))
    rules_file = os.path.join(tool_path, _RULES_FILE_REL)
    rules = []
    if os.path.isfile(rules_file):
        with open(rules_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            rules = data.get("rules", [])
    return {"rules": rules}


@router.post("/api/tools/{tool_id}/design-rules")
def save_design_rules(
    tool_id: str,
    body: dict,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Overwrite config/design_import_rules.yaml with the provided rules list."""
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    tool_path = resolve_tool_path(manifest.get("path", ""))
    rules_file = os.path.join(tool_path, _RULES_FILE_REL)

    # Read the existing file so we can preserve the top-level comment block
    existing_header = ""
    if os.path.isfile(rules_file):
        with open(rules_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    existing_header += line
                else:
                    break

    rules = body.get("rules", [])
    yaml_body = yaml.dump({"rules": rules}, allow_unicode=True,
                          default_flow_style=False, sort_keys=False)

    with open(rules_file, "w", encoding="utf-8") as f:
        if existing_header:
            f.write(existing_header + "\n")
        f.write(yaml_body)

    return {"ok": True, "count": len(rules)}


# ---------------------------------------------------------------------------
# Inventory helpers
# ---------------------------------------------------------------------------


def _run_tool_script(python: str, script: str, args: list[str], timeout: int = 120) -> dict:
    """Run a tool's Python script and return parsed JSON from stdout."""
    result = subprocess.run(
        [python, script] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "Script exited with non-zero status"
        raise HTTPException(status_code=500, detail=detail)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        snippet = result.stdout[:300]
        raise HTTPException(status_code=500, detail=f"Script output parse error: {exc} | output: {snippet}")


# ---------------------------------------------------------------------------
# Inventory endpoints
# ---------------------------------------------------------------------------


@router.get("/api/tools/{tool_id}/inventory/manufacturers")
def get_inventory_manufacturers(
    tool_id: str,
    current_user: User = Depends(get_current_user),
) -> list[str]:
    """Return all manufacturer codes defined in mappings/products.yaml."""
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    tool_path = resolve_tool_path(manifest.get("path", ""))
    products_yaml = os.path.join(tool_path, "mappings", "products.yaml")
    if not os.path.exists(products_yaml):
        return []
    with open(products_yaml, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return list((data.get("manufacturers") or {}).keys())


@router.get("/api/tools/{tool_id}/inventory/products")
def get_inventory_products(
    tool_id: str,
    manufacturers: str = "",
    category: str = "",
    size: str = "",
    color: str = "",
    variant: str = "",
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Load non-BOM products from the manufacturers' Google Sheets.

    Query params:
      manufacturers  Comma-separated manufacturer codes (default: all from products.yaml)
      category       Filter by Produktkategorie (substring, optional)
      size           Filter by Produktgröße (substring, optional)
      color          Filter by Produktfarbe (substring, optional)
      variant        Filter by Produktvariante (substring, optional)

    Returns {"products": [...], "errors": [...]}.
    """
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    tool_path = resolve_tool_path(manifest.get("path", ""))
    python = get_external_python(tool_path)
    script = os.path.join(tool_path, "execution", "gui_read_sheet_products.py")

    mfr_codes = [m.strip() for m in manufacturers.split(",") if m.strip()]
    if not mfr_codes:
        raise HTTPException(status_code=400, detail="At least one manufacturer code required")

    args = ["--manufacturers"] + mfr_codes
    if category: args += ["--category", category]
    if size:     args += ["--size",     size]
    if color:    args += ["--color",    color]
    if variant:  args += ["--variant",  variant]

    return _run_tool_script(python, script, args, timeout=120)


@router.get("/api/tools/{tool_id}/inventory/products/billbee")
async def get_inventory_products_billbee(
    tool_id: str,
    manufacturers: str = "",
    category: str = "",
    size: str = "",
    color: str = "",
    variant: str = "",
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """
    Stream non-BOM products from the Billbee API as SSE (text/event-stream).

    Each event is a JSON line:
      {"type":"product","data":{...}}
      {"type":"error","data":{"manufacturer":"*","error":"..."}}
      {"type":"done","total":N}
    """
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    tool_path = resolve_tool_path(manifest.get("path", ""))
    python = get_external_python(tool_path)
    script = os.path.join(tool_path, "execution", "gui_read_billbee_products.py")

    mfr_codes = [m.strip() for m in manufacturers.split(",") if m.strip()]
    if not mfr_codes:
        raise HTTPException(status_code=400, detail="At least one manufacturer code required")

    args = ["--manufacturers"] + mfr_codes
    if category: args += ["--category", category]
    if size:     args += ["--size",     size]
    if color:    args += ["--color",    color]
    if variant:  args += ["--variant",  variant]

    async def generate():
        proc = await asyncio.create_subprocess_exec(
            python, script, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async for raw in proc.stdout:
                line = raw.decode().strip()
                if line:
                    yield f"data: {line}\n\n"
        except asyncio.CancelledError:
            proc.terminate()
            raise
        finally:
            await proc.wait()
            if proc.returncode not in (0, None, -15):
                err = (await proc.stderr.read()).decode().strip()
                error_evt = json.dumps({"type": "error", "data": {"manufacturer": "*", "error": err}})
                yield f"data: {error_evt}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/tools/{tool_id}/inventory/stock/query")
async def query_inventory_stock(
    tool_id: str,
    body: StockQueryRequest,
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """
    Stream live stock levels from Billbee as SSE (text/event-stream).

    Body: {"products": [{"sku": "...", "billbeeId": 12345}, ...]}

    Each event is a JSON line:
      {"type":"stock",    "sku":"...", "stock":N, "stockId":N}
      {"type":"progress", "scanned":N, "total":M, "found":F}   (large lists only)
      {"type":"error",    "data":{"sku":"...", "error":"..."}}
      {"type":"done",     "total":N}
    """
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    tool_path = resolve_tool_path(manifest.get("path", ""))
    python = get_external_python(tool_path)
    script = os.path.join(tool_path, "execution", "gui_get_stock.py")

    products_json = json.dumps(body.products)

    async def generate():
        proc = await asyncio.create_subprocess_exec(
            python, script, "--products", products_json,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async for raw in proc.stdout:
                line = raw.decode().strip()
                if line:
                    yield f"data: {line}\n\n"
        except asyncio.CancelledError:
            proc.terminate()
            raise
        finally:
            await proc.wait()
            if proc.returncode not in (0, None, -15):
                err = (await proc.stderr.read()).decode().strip()
                error_evt = json.dumps({"type": "error", "data": {"sku": "*", "error": err}})
                yield f"data: {error_evt}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/tools/{tool_id}/inventory/stock/update")
def update_inventory_stock(
    tool_id: str,
    body: StockUpdateRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Update the stock level for a single SKU in Billbee.

    Body: {sku, billbeeId, delta?, newQuantity?, reason?}
    Exactly one of delta or newQuantity must be provided.
    Returns {"ok": true, "sku": "...", "previousStock": N, "newStock": N}.
    """
    if body.delta is None and body.newQuantity is None:
        raise HTTPException(status_code=400, detail="Provide delta or newQuantity")
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    tool_path = resolve_tool_path(manifest.get("path", ""))
    python = get_external_python(tool_path)
    script = os.path.join(tool_path, "execution", "gui_update_stock.py")

    args = ["--sku", body.sku, "--billbee-id", str(body.billbeeId), "--reason", body.reason]
    if body.delta is not None:
        args += ["--delta", str(body.delta)]
    else:
        args += ["--new-quantity", str(body.newQuantity)]

    return _run_tool_script(python, script, args, timeout=30)


@router.get("/api/tools/{tool_id}/inventory/sheet-tabs")
def get_inventory_sheet_tabs(
    tool_id: str,
    sheet: str = "",
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return the tab names of a Google Sheet. Returns {"tabs": [...], "error": null|str}."""
    if not sheet:
        raise HTTPException(status_code=400, detail="sheet is required")
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    tool_path = resolve_tool_path(manifest.get("path", ""))
    python = get_external_python(tool_path)
    script = os.path.join(tool_path, "execution", "gui_sheet_tabs.py")
    return _run_tool_script(python, script, ["--sheet", sheet], timeout=30)


@router.get("/api/tools/{tool_id}/inventory/sheet-import")
def get_inventory_sheet_import(
    tool_id: str,
    sheet: str = "",
    tab: str = "",
    sku_col: str = "SKU",
    qty_col: str = "Qty",
    manufacturer: str = "",
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Read SKUs + quantities from a Google Sheet tab, resolve Billbee IDs via
    the manufacturer's ProductList, and fetch live Billbee stock.

    Query params:
      sheet        Source Google Sheet name (required)
      tab          Source tab name (required)
      sku_col      Column name for SKU (default: SKU)
      qty_col      Column name for quantity (default: Qty)
      manufacturer Manufacturer code for ProductList lookup (required)

    Returns {"items": [{sku, billbeeId, billbeeStock, qty}], "errors": [...]}.
    """
    if not sheet or not tab or not manufacturer:
        raise HTTPException(status_code=400, detail="sheet, tab and manufacturer are required")
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")
    tool_path = resolve_tool_path(manifest.get("path", ""))
    python = get_external_python(tool_path)
    script = os.path.join(tool_path, "execution", "gui_sheet_import.py")

    args = [
        "--sheet", sheet,
        "--tab", tab,
        "--sku-col", sku_col,
        "--qty-col", qty_col,
        "--manufacturer", manufacturer,
    ]
    # Allow ~1 s per expected item plus overhead; cap at 300 s
    return _run_tool_script(python, script, args, timeout=300)


@router.get("/api/tools/{tool_id}/filetree")
def get_tool_filetree(
    tool_id: str, current_user: User = Depends(get_current_user)
) -> list[dict]:
    """Return a nested file tree for the tool's file_tree entries."""
    manifest = get_tool_by_id(tool_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found")

    raw_path = manifest.get("path", "")
    # manifest["path"] is already an absolute resolved path from load_tool_manifests;
    # use resolve_tool_path to keep T:\ form on Windows (realpath would convert to UNC).
    tool_path = resolve_tool_path(raw_path)
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
