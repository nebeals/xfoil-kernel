#!/usr/bin/env python3
"""Generate C81 tables from a YAML manifest using the XFOIL kernel worker."""
from __future__ import annotations

from pathlib import Path
import sys

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from xfoil_kernel_tools.c81_generator import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
