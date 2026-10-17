"""
make_hf_space.py
----------------
Generates a synchronized, deployment-ready `hf_space/` bundle from the repository root.
Ensures zero drift between repo root and Hugging Face Space package.
"""

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "hf_space"

# Files and directories required for Hugging Face Space deployment
DEPLOY_FILES = [
    "README.md",
    "requirements.txt",
    "app.py",
    "configuration.py",
    "storage.py",
    "lead_finder.py",
    "website_audit.py",
    "lead_model.py",
    "templates.py",
    "ui.py",
    "http_tracking.py",
    "safe_logging.py",
    "migrate.py",
    "diagnose_db.py",
    "import_leads.py",
    ".env.example",
    "DEPLOY.md",
]

DEPLOY_DIRS = [
    ".streamlit",
    "migrations",
]


def build_space():
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True, exist_ok=True)

    copied_count = 0
    for filename in DEPLOY_FILES:
        src = ROOT / filename
        if src.exists():
            shutil.copy2(src, DEST / filename)
            copied_count += 1
        else:
            print(f"Warning: source file missing: {filename}")

    for dirname in DEPLOY_DIRS:
        src_dir = ROOT / dirname
        if src_dir.exists():
            shutil.copytree(src_dir, DEST / dirname, dirs_exist_ok=True)
            copied_count += 1
        else:
            print(f"Warning: source directory missing: {dirname}")

    print(f"Successfully generated hf_space/ with {copied_count} deployment items from repository root.")


if __name__ == "__main__":
    build_space()
