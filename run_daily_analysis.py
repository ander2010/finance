#!/usr/bin/env python3
"""
Run the daily stock analysis manually.
Usage:
    python run_daily_analysis.py           # normal
    python run_daily_analysis.py --force   # re-analyze even if already done today
    python run_daily_analysis.py --no-email
"""

import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PYTHON   = BASE_DIR / "venv" / "bin" / "python"
MANAGE   = BASE_DIR / "manage.py"

cmd = [str(PYTHON), str(MANAGE), "cron_daily_analysis"] + sys.argv[1:]

print(f"Running: {' '.join(cmd)}\n")
subprocess.run(cmd)
