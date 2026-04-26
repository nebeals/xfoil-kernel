from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from xfoil_kernel import (
    AirfoilSpec,
    KernelConfig,
    KernelError,
    SolveOptions,
    XfoilKernelClient,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Solve a small alpha sequence through the public XFOIL kernel API.",
    )
    parser.add_argument("--naca", default="0012", help="NACA airfoil code.")
    parser.add_argument(
        "--alpha",
        type=float,
        nargs="+",
        default=[-4.0, -2.0, 0.0, 2.0, 4.0],
        help="Alpha sequence in degrees.",
    )
    parser.add_argument("--reynolds", type=float, default=1_000_000.0)
    parser.add_argument("--mach", type=float, default=0.0)
    parser.add_argument("--ncrit", type=float, default=9.0)
    parser.add_argument("--xtr-top", type=float, default=1.0)
    parser.add_argument("--xtr-bottom", type=float, default=1.0)
    parser.add_argument("--panel-count", type=int, default=180)
    parser.add_argument("--itmax", type=int, default=100)
    parser.add_argument("--runtime-root", type=Path, default=Path("runs/examples/api-worker"))
    parser.add_argument("--driver-executable", type=Path, default=None)
    parser.add_argument("--session-executable", type=Path, default=None)
    parser.add_argument("--one-shot", action="store_true")
    args = parser.parse_args(argv)

    config = KernelConfig(
        driver_executable=args.driver_executable,
        session_executable=args.session_executable,
        runtime_root=args.runtime_root,
        use_session=not args.one_shot,
    )
    options = SolveOptions(
        viscous=True,
        reynolds_number=args.reynolds,
        mach_number=args.mach,
        ncrit=args.ncrit,
        xtr_top=args.xtr_top,
        xtr_bottom=args.xtr_bottom,
        itmax=args.itmax,
        panel_count=args.panel_count,
    )

    try:
        with XfoilKernelClient(config) as client:
            client.register_airfoil(f"NACA{args.naca}", AirfoilSpec.naca(args.naca))
            result = client.solve_alpha_sequence(
                f"NACA{args.naca}",
                alpha_deg=args.alpha,
                options=options,
            )
    except (KernelError, ValueError) as exc:
        print(f"solve failed: {exc}")
        return 1

    print(f"complete: {result.complete}")
    if result.missing_alpha_deg:
        missing = ", ".join(f"{alpha:g}" for alpha in result.missing_alpha_deg)
        print(f"missing_alpha_deg: {missing}")

    print("alpha_deg        cl          cd          cm    converged")
    for point in result.points:
        print(
            f"{point.alpha_deg:9.3f} "
            f"{point.cl:10.6f} "
            f"{point.cd:10.6f} "
            f"{point.cm:10.6f} "
            f"{str(point.converged):>10s}"
        )
    return 0 if result.complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
