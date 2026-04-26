#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


KERNEL_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = KERNEL_ROOT / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

from xfoil_kernel_tools.worker import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
