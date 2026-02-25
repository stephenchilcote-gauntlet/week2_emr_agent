"""CLI wrapper for grounded mutmut survivor analysis."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.eval.mutant_analysis import run_cli


if __name__ == "__main__":
    raise SystemExit(run_cli())
