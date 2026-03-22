"""Set up Python virtual environments for all tools that declare one."""
import os
import subprocess
import sys

import yaml

GUI_MANAGER_ROOT = os.path.dirname(os.path.abspath(__file__))


def resolve_tool_path(raw_path: str) -> str:
    joined = os.path.normpath(os.path.join(GUI_MANAGER_ROOT, raw_path))
    if os.name == "nt":
        return os.path.abspath(joined)
    return os.path.realpath(joined)


SETUP_BAT_TEMPLATE = """\
@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo === {tool_name} Setup ===
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo Install Python from https://python.org and check "Add Python to PATH".
    pause
    exit /b 1
)

if not exist "{venv}\\Scripts\\python.exe" (
    echo Creating virtual environment...
    python -m venv "{venv}"
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo Virtual environment already exists.
)

if exist "requirements.txt" (
    echo Installing dependencies...
    "{venv}\\Scripts\\pip" install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: pip install failed.
        pause
        exit /b 1
    )
) else (
    echo No requirements.txt found, skipping pip install.
)

echo.
echo === Setup complete! ===
echo.
pause
exit /b 0
"""


def write_setup_bat(tool_path: str, tool_name: str, venv_rel: str) -> None:
    dest = os.path.join(tool_path, "setup.bat")
    if os.path.isfile(dest):
        print(f"  setup.bat already exists, skipping.")
        return
    content = SETUP_BAT_TEMPLATE.format(tool_name=tool_name, venv=venv_rel)
    with open(dest, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(content)
    print(f"  Created setup.bat in {tool_path}")


def setup_venv(tool_name: str, tool_path: str, venv_rel: str) -> bool:
    if not os.path.isdir(tool_path):
        print(f"  SKIP: tool path not found: {tool_path}")
        return False

    venv_path = os.path.join(tool_path, venv_rel)
    pip = os.path.join(venv_path, "Scripts" if os.name == "nt" else "bin", "pip")
    req = os.path.join(tool_path, "requirements.txt")

    if not os.path.isfile(pip):
        print(f"  Creating venv at {venv_path} ...")
        r = subprocess.run([sys.executable, "-m", "venv", venv_path])
        if r.returncode != 0:
            print(f"  ERROR: venv creation failed")
            return False
    else:
        print(f"  Venv already exists, skipping creation.")

    if os.path.isfile(req):
        print(f"  Installing requirements from {req} ...")
        r = subprocess.run([pip, "install", "--quiet", "-r", req])
        if r.returncode != 0:
            print(f"  ERROR: pip install failed")
            return False
    else:
        print(f"  No requirements.txt found, skipping pip install.")

    return True


def main() -> None:
    tools_dir = os.path.join(GUI_MANAGER_ROOT, "tools")
    if not os.path.isdir(tools_dir):
        print("No tools/ directory found — nothing to do.")
        return

    yaml_files = sorted(
        f for f in os.listdir(tools_dir) if f.endswith(".yaml")
    )

    found = 0
    for fname in yaml_files:
        fpath = os.path.join(tools_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except Exception as e:
            print(f"[{fname}] Could not parse: {e}")
            continue

        if not data or "venv" not in data or "path" not in data:
            continue

        found += 1
        tool_name = data.get("name", fname)
        tool_path = resolve_tool_path(data["path"])
        venv_rel = data["venv"]

        print(f"\n[{tool_name}] Setting up venv '{venv_rel}' in {tool_path}")
        if os.path.isdir(tool_path):
            write_setup_bat(tool_path, tool_name, venv_rel)
        setup_venv(tool_name, tool_path, venv_rel)

    if found == 0:
        print("No tools with a 'venv' field found.")
    else:
        print(f"\nDone. {found} tool(s) processed.")


if __name__ == "__main__":
    main()
