from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .baseline import (
    DEFAULT_CASES_PATH,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_REFERENCE_ROOT,
    write_reference_baselines,
)
from .driver import DEFAULT_KERNEL_RUN_ROOT, compare_to_reference


def compare_kernel_driver(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare direct-call driver summaries to pristine references.")
    parser.add_argument("--run-root", type=Path, default=DEFAULT_KERNEL_RUN_ROOT)
    parser.add_argument("--reference-root", type=Path, default=DEFAULT_REFERENCE_ROOT)
    args = parser.parse_args(argv)

    for reference_path in sorted(args.reference_root.glob("*.json")):
        reference = json.loads(reference_path.read_text())
        case_id = str(reference["case_id"])
        summary_path = args.run_root / case_id / "summary.json"
        if not summary_path.exists():
            print(f"{case_id}: missing driver summary")
            continue
        summary = json.loads(summary_path.read_text())
        differences = compare_to_reference(summary, reference)
        max_dcl = max((abs(row["d_cl"]) for row in differences), default=float("nan"))
        max_dcd = max((abs(row["d_cd"]) for row in differences), default=float("nan"))
        max_dcm = max((abs(row["d_cm"]) for row in differences), default=float("nan"))
        max_dxtr = max(
            (
                max(abs(row["d_xtr_top"]), abs(row["d_xtr_bottom"]))
                for row in differences
                if "d_xtr_top" in row and "d_xtr_bottom" in row
            ),
            default=float("nan"),
        )
        missing = summary.get("missing_alpha_deg", [])
        print(
            f"{case_id}: matched={len(differences)} "
            f"max|dCL|={max_dcl:.6g} max|dCD|={max_dcd:.6g} max|dCM|={max_dcm:.6g} "
            f"max|dXtr|={max_dxtr:.6g} "
            f"missing={missing}"
        )
    return 0


def write_references(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write tracked reference baseline JSON files.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--reference-root", type=Path, default=DEFAULT_REFERENCE_ROOT)
    args = parser.parse_args(argv)

    written = write_reference_baselines(
        cases_path=args.cases,
        output_root=args.output_root,
        reference_root=args.reference_root,
    )
    for path in written:
        print(path)
    return 0
