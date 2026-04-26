from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from .api import (
    __version__,
    AirfoilSpec,
    AlphaSequenceResult,
    C81GenerationError,
    KernelConfig,
    KernelError,
    PointResult,
    SolveOptions,
    XfoilKernelClient,
    generate_c81_from_manifest,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="xfoil-kernel-api",
        description="Public API command-line tools for the XFOIL kernel.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show worker status and capabilities.")
    _add_config_arguments(status_parser)
    status_parser.add_argument("--json", action="store_true", help="Print the raw status response as JSON.")
    status_parser.set_defaults(func=_run_status)

    sequence_parser = subparsers.add_parser(
        "solve-alpha-sequence",
        help="Solve one alpha sequence for a registered airfoil.",
    )
    _add_config_arguments(sequence_parser)
    _add_airfoil_arguments(sequence_parser)
    _add_solve_option_arguments(sequence_parser)
    sequence_parser.add_argument(
        "--alpha",
        type=float,
        nargs="+",
        required=True,
        help="Alpha sequence in degrees.",
    )
    sequence_parser.add_argument("--json", action="store_true", help="Print the result as JSON.")
    sequence_parser.set_defaults(func=_run_solve_alpha_sequence)

    point_parser = subparsers.add_parser(
        "solve-alpha",
        help="Solve one alpha point, optionally with the API's warm-start sequence.",
    )
    _add_config_arguments(point_parser)
    _add_airfoil_arguments(point_parser)
    _add_solve_option_arguments(point_parser)
    point_parser.add_argument("--alpha", type=float, required=True, help="Alpha in degrees.")
    warm_start = point_parser.add_mutually_exclusive_group()
    warm_start.add_argument(
        "--warm-start-alpha",
        type=float,
        nargs="+",
        default=None,
        help="Explicit warm-start sequence. It must include the requested alpha.",
    )
    warm_start.add_argument(
        "--no-warm-start",
        action="store_true",
        help="Submit only the requested alpha.",
    )
    point_parser.add_argument("--json", action="store_true", help="Print the point result as JSON.")
    point_parser.set_defaults(func=_run_solve_alpha)

    c81_parser = subparsers.add_parser(
        "generate-c81",
        help="Generate C81 tables from a YAML manifest through the public API.",
    )
    c81_parser.add_argument("manifest", type=Path, help="YAML C81 generation manifest.")
    _add_worker_override_arguments(c81_parser)
    c81_parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Write common converged alpha points when requested points are missing.",
    )
    c81_parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    c81_parser.set_defaults(func=_run_generate_c81)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except C81GenerationError as exc:
        print(f"{args.command} failed: {exc}", file=sys.stderr)
        return 2
    except (KernelError, ValueError) as exc:
        print(f"{args.command} failed: {exc}", file=sys.stderr)
        return 1


def _add_config_arguments(parser: argparse.ArgumentParser) -> None:
    _add_worker_override_arguments(parser)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)


def _add_worker_override_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--driver-executable", type=Path, default=None)
    parser.add_argument("--session-executable", type=Path, default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--use-session", dest="use_session", action="store_true", default=True)
    mode.add_argument("--one-shot", dest="use_session", action="store_false")
    parser.add_argument("--runtime-root", type=Path, default=None)
    parser.add_argument("--kernel-root", type=Path, default=None)


def _add_airfoil_arguments(parser: argparse.ArgumentParser) -> None:
    airfoil = parser.add_mutually_exclusive_group(required=True)
    airfoil.add_argument("--naca", help="NACA airfoil code, for example 0012.")
    airfoil.add_argument("--coordinates-file", type=Path, help="Coordinate airfoil .dat file.")
    parser.add_argument("--airfoil-id", default=None, help="Registration id. Defaults from the airfoil source.")
    parser.add_argument(
        "--no-panel",
        action="store_true",
        help="For coordinate files, use supplied points directly instead of LOAD -> PANGEN.",
    )


def _add_solve_option_arguments(parser: argparse.ArgumentParser) -> None:
    viscous = parser.add_mutually_exclusive_group()
    viscous.add_argument("--viscous", dest="viscous", action="store_true", default=True)
    viscous.add_argument("--inviscid", dest="viscous", action="store_false")
    parser.add_argument("--reynolds", type=float, default=None, help="Reynolds number.")
    parser.add_argument("--mach", type=float, default=0.0, help="Mach number.")
    parser.add_argument("--ncrit", type=float, default=9.0, help="Common e^n Ncrit value.")
    parser.add_argument("--ncrit-top", type=float, default=None, help="Advanced top-surface Ncrit override.")
    parser.add_argument("--ncrit-bottom", type=float, default=None, help="Advanced bottom-surface Ncrit override.")
    parser.add_argument("--xtr-top", type=float, default=1.0, help="Top forced-transition x/c.")
    parser.add_argument("--xtr-bottom", type=float, default=1.0, help="Bottom forced-transition x/c.")
    parser.add_argument("--itmax", type=int, default=50, help="Viscous iteration limit.")
    parser.add_argument("--panel-count", type=int, default=160, help="Generated panel count.")


def _run_status(args: argparse.Namespace) -> int:
    with XfoilKernelClient(_config_from_args(args)) as client:
        status = client.status()
    if args.json:
        print(json.dumps(status, indent=2))
        return 0

    capabilities = status.get("capabilities", {})
    print(f"protocol_version: {status.get('protocol_version')}")
    print(f"implementation: {status.get('implementation')}")
    print(f"mode: {status.get('mode')}")
    print(f"session_active: {status.get('session_active')}")
    print(f"registered_airfoils: {', '.join(status.get('registered_airfoils', [])) or '<none>'}")
    print(f"commands: {', '.join(capabilities.get('commands', []))}")
    print(f"solve_options: {', '.join(capabilities.get('solve_options', []))}")
    return 0


def _run_solve_alpha_sequence(args: argparse.Namespace) -> int:
    airfoil_id, airfoil = _airfoil_from_args(args)
    options = _solve_options_from_args(args)
    with XfoilKernelClient(_config_from_args(args)) as client:
        client.register_airfoil(airfoil_id, airfoil)
        result = client.solve_alpha_sequence(
            airfoil_id,
            alpha_deg=args.alpha,
            options=options,
            timeout_seconds=args.timeout_seconds,
        )

    _print_sequence_result(result, json_output=bool(args.json))
    return 0 if result.complete else 2


def _run_solve_alpha(args: argparse.Namespace) -> int:
    airfoil_id, airfoil = _airfoil_from_args(args)
    options = _solve_options_from_args(args)
    warm_start: bool | Sequence[float]
    if args.warm_start_alpha is not None:
        warm_start = args.warm_start_alpha
    else:
        warm_start = not bool(args.no_warm_start)

    with XfoilKernelClient(_config_from_args(args)) as client:
        client.register_airfoil(airfoil_id, airfoil)
        point = client.solve_alpha(
            airfoil_id,
            alpha_deg=args.alpha,
            options=options,
            warm_start=warm_start,
            timeout_seconds=args.timeout_seconds,
        )

    _print_point_result(point, json_output=bool(args.json))
    return 0


def _run_generate_c81(args: argparse.Namespace) -> int:
    report = generate_c81_from_manifest(
        args.manifest,
        driver_executable=args.driver_executable,
        session_executable=args.session_executable,
        use_session=args.use_session,
        runtime_root=args.runtime_root,
        kernel_root=args.kernel_root,
        allow_incomplete=(True if args.allow_incomplete else None),
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0 if report.ok else 1

    print(f"report: {report.report_file}")
    for table in report.tables:
        status = "ok" if table.get("ok") else "failed"
        completeness = "complete" if table.get("complete") else "incomplete"
        print(f"{table['id']}: {status}, {completeness}")
        for path in table.get("written_files", []):
            print(f"  wrote {path}")
    return 0 if report.ok else 1


def _config_from_args(args: argparse.Namespace) -> KernelConfig:
    return KernelConfig(
        driver_executable=args.driver_executable,
        session_executable=args.session_executable,
        runtime_root=args.runtime_root,
        use_session=bool(args.use_session),
        timeout_seconds=args.timeout_seconds,
        kernel_root=args.kernel_root,
    )


def _airfoil_from_args(args: argparse.Namespace) -> tuple[str, AirfoilSpec]:
    if args.naca is not None:
        airfoil = AirfoilSpec.naca(args.naca)
        airfoil_id = args.airfoil_id or f"NACA{airfoil.code}"
        return airfoil_id, airfoil

    airfoil = AirfoilSpec.coordinates_file(
        args.coordinates_file,
        panel=not bool(args.no_panel),
    )
    airfoil_id = args.airfoil_id or Path(args.coordinates_file).stem
    return str(airfoil_id), airfoil


def _solve_options_from_args(args: argparse.Namespace) -> SolveOptions:
    return SolveOptions(
        viscous=bool(args.viscous),
        reynolds_number=args.reynolds,
        mach_number=args.mach,
        ncrit=args.ncrit,
        ncrit_top=args.ncrit_top,
        ncrit_bottom=args.ncrit_bottom,
        xtr_top=args.xtr_top,
        xtr_bottom=args.xtr_bottom,
        itmax=args.itmax,
        panel_count=args.panel_count,
    )


def _print_sequence_result(result: AlphaSequenceResult, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result.to_dict(), indent=2))
        return

    print(f"complete: {result.complete}")
    if result.missing_alpha_deg:
        missing = ", ".join(f"{alpha:g}" for alpha in result.missing_alpha_deg)
        print(f"missing_alpha_deg: {missing}")
    print("alpha_deg        cl          cd          cm    converged")
    for point in result.points:
        _print_point_row(point)


def _print_point_result(point: PointResult, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(point.to_dict(), indent=2))
        return

    print("alpha_deg        cl          cd          cm    converged")
    _print_point_row(point)


def _print_point_row(point: PointResult) -> None:
    print(
        f"{point.alpha_deg:9.3f} "
        f"{point.cl:10.6f} "
        f"{point.cd:10.6f} "
        f"{point.cm:10.6f} "
        f"{str(point.converged):>10s}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
