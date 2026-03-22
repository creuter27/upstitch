# Agent Instructions

> Mirrors the same 3-layer architecture used across all projects in ~/code.

## The 3-Layer Architecture

**Layer 1: Directive (What to do)**
- SOPs in `directives/` — what to do, edge cases, learnings

**Layer 2: Orchestration (Decision making)**
- This is you. Read directives, call execution scripts, handle errors.

**Layer 3: Execution (Doing the work)**
- Deterministic Python scripts in `execution/`
- Environment variables in `.env`

## Operating Principles
1. Check `execution/` before writing new scripts
2. Self-anneal: fix → test → update directive
3. Update directives with learnings (API constraints, edge cases)

## File Organization
- `.tmp/` — intermediate files (geocode cache, debug output). Never commit.
- `data/` — persistent state: `last_run.json`, `feedback.jsonl`
- `execution/` — Python scripts
- `directives/` — SOPs

## Python
- Always use `python3.14` (never `python3`)
- Venv: `.venv/` in project root
- Run: `.venv/bin/python main.py`

## Entry Point
```bash
.venv/bin/python main.py                    # process new orders since last run
.venv/bin/python main.py --dry-run          # show issues without updating Billbee
.venv/bin/python main.py --since 2026-01-01 # override last_run date
.venv/bin/python main.py --skip-geocode     # skip OpenCage API calls
```
