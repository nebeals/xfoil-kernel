from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping, Sequence

from .baseline import (
    BaselineCase,
    DEFAULT_CASES_PATH,
    load_cases,
)
from .build import DEFAULT_KERNEL_DRIVER_BUILD_ROOT
from .paths import KERNEL_ROOT


DEFAULT_KERNEL_DRIVER_EXECUTABLE = DEFAULT_KERNEL_DRIVER_BUILD_ROOT / "bin" / "xfoil_kernel_driver"
DEFAULT_KERNEL_RUN_ROOT = KERNEL_ROOT / "runs" / "kernel-driver"
_MISSING_EXPONENT_MARKER_RE = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))([+-]\d+)$")
_HEADER_FIELD_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=")
_ALPHA_TOLERANCE = 1.0e-6


@dataclass(frozen=True)
class KernelDriverPoint:
    """One result row emitted by the direct-call kernel driver."""

    index: int
    alpha_deg: float
    cl: float
    cd: float
    cm: float
    cdp: float
    converged: bool
    rms_bl: float
    xtr_top: float
    xtr_bottom: float
    transition_forced_top: bool
    transition_forced_bottom: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "alpha_deg": self.alpha_deg,
            "cl": self.cl,
            "cd": self.cd,
            "cm": self.cm,
            "cdp": self.cdp,
            "converged": self.converged,
            "rms_bl": self.rms_bl,
            "xtr_top": self.xtr_top,
            "xtr_bottom": self.xtr_bottom,
            "transition_forced_top": self.transition_forced_top,
            "transition_forced_bottom": self.transition_forced_bottom,
        }


def build_case_namelist(
    case: BaselineCase,
    *,
    kernel_root: Path = KERNEL_ROOT,
    coordinate_file: Path | None = None,
) -> str:
    """Build the namelist consumed by the first direct-call Fortran driver."""

    airfoil = case.airfoil
    airfoil_type = str(airfoil.get("type", "")).lower()
    if airfoil_type == "naca":
        naca_code = int(airfoil["code"])
        coordinate_value = ""
    elif airfoil_type == "coordinates":
        naca_code = 0
        if coordinate_file is None:
            coordinate_file = _resolve_kernel_path(str(airfoil["path"]), kernel_root)
        coordinate_value = str(coordinate_file)
    else:
        raise ValueError(f"Unsupported airfoil type '{airfoil_type}' in case {case.id}.")

    options = dict(case.options)
    ncrit_top, ncrit_bottom = _ncrit_values(options)
    alpha_values = ", ".join(f"{alpha:.12g}" for alpha in case.alpha_deg)
    return (
        "&xkcase\n"
        f"  airfoil_type = '{airfoil_type}'\n"
        f"  coordinate_file = '{coordinate_value}'\n"
        f"  naca_code = {naca_code}\n"
        f"  viscous = {_fortran_bool(bool(options.get('viscous', False)))}\n"
        f"  reynolds_number = {float(options.get('reynolds_number', 0.0)):.12g}\n"
        f"  mach_number = {float(options.get('mach_number', 0.0)):.12g}\n"
        f"  ncrit_top = {ncrit_top:.12g}\n"
        f"  ncrit_bottom = {ncrit_bottom:.12g}\n"
        f"  xtr_top = {float(options.get('xtr_top', 1.0)):.12g}\n"
        f"  xtr_bottom = {float(options.get('xtr_bottom', 1.0)):.12g}\n"
        f"  itmax = {int(options.get('itmax', 50))}\n"
        f"  panel_count = {int(options.get('panel_count', 160))}\n"
        f"  panel_airfoil = {_fortran_bool(bool(airfoil.get('panel', True)))}\n"
        f"  n_alpha = {len(case.alpha_deg)}\n"
        f"  alpha_deg = {alpha_values}\n"
        "/\n"
    )


def parse_kernel_driver_output(text: str) -> list[KernelDriverPoint]:
    """Parse result rows from the direct-call driver transcript."""

    points: list[KernelDriverPoint] = []
    for line in text.splitlines():
        if not line.startswith("XK_POINT "):
            continue
        parts = line.split()
        if len(parts) != 13:
            raise ValueError(f"Unexpected XK_POINT row with {len(parts)} fields: {line!r}")
        points.append(
            KernelDriverPoint(
                index=int(parts[1]),
                alpha_deg=_parse_kernel_float(parts[2]),
                cl=_parse_kernel_float(parts[3]),
                cd=_parse_kernel_float(parts[4]),
                cm=_parse_kernel_float(parts[5]),
                cdp=_parse_kernel_float(parts[6]),
                converged=_parse_fortran_bool(parts[7]),
                rms_bl=_parse_kernel_float(parts[8]),
                xtr_top=_parse_kernel_float(parts[9]),
                xtr_bottom=_parse_kernel_float(parts[10]),
                transition_forced_top=_parse_fortran_bool(parts[11]),
                transition_forced_bottom=_parse_fortran_bool(parts[12]),
            )
        )
    return points


def parse_kernel_header(text: str) -> dict[str, Any]:
    """Parse the first XK_HEADER row from a kernel transcript."""

    for line in text.splitlines():
        if line.startswith("XK_HEADER "):
            return _parse_kernel_header_line(line)
    return {}


def parse_kernel_failure_markers(text: str) -> list[dict[str, str]]:
    """Extract known textual failure markers from a kernel transcript."""

    markers = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if "viscal:" in lower and "convergence failed" in lower:
            markers.append({"code": "viscous_nonconvergence", "message": stripped})
        elif "specal:" in lower and "convergence failed" in lower:
            markers.append({"code": "alpha_specification_failed", "message": stripped})
        elif "speccl:" in lower and "convergence failed" in lower:
            markers.append({"code": "cl_specification_failed", "message": stripped})
        elif "paneling convergence failed" in lower:
            markers.append({"code": "paneling_convergence_failed", "message": stripped})
    return markers


def build_nonconvergence_diagnostics(
    requested_alpha: Sequence[float],
    points: Sequence[KernelDriverPoint],
    *,
    options: Mapping[str, Any] | None = None,
    header: Mapping[str, Any] | None = None,
    failure_markers: Sequence[Mapping[str, str]] = (),
) -> list[dict[str, Any]]:
    """Build structured diagnostics for requested alpha points without converged results."""

    options = dict(options or {})
    header = dict(header or {})
    point_by_index = {point.index: point for point in points}
    diagnostics = []
    for request_index, requested in enumerate(requested_alpha, start=1):
        requested_value = float(requested)
        point = point_by_index.get(request_index)
        if point is None:
            point = _find_point_for_requested_alpha(requested_value, points)
        if point is not None and point.converged:
            continue
        if point is None:
            diagnostic = {
                "index": request_index,
                "requested_alpha_deg": requested_value,
                "reason": "no_result_row",
                "message": "Kernel did not emit a result row for the requested alpha.",
            }
        else:
            diagnostic = _point_nonconvergence_diagnostic(
                requested_alpha=requested_value,
                point=point,
                options=options,
                header=header,
            )
        if failure_markers:
            diagnostic["failure_markers"] = [dict(marker) for marker in failure_markers]
        diagnostics.append(diagnostic)
    return diagnostics


def run_kernel_case(
    case: BaselineCase,
    *,
    driver_executable: Path = DEFAULT_KERNEL_DRIVER_EXECUTABLE,
    run_root: Path = DEFAULT_KERNEL_RUN_ROOT,
    kernel_root: Path = KERNEL_ROOT,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Run one baseline case through the direct-call driver."""

    case_dir = run_root / case.id
    case_dir.mkdir(parents=True, exist_ok=True)
    coordinate_file = _prepare_coordinate_airfoil(case, case_dir, kernel_root)
    namelist = build_case_namelist(case, kernel_root=kernel_root, coordinate_file=coordinate_file)

    input_path = case_dir / "input.nml"
    transcript_path = case_dir / "transcript.txt"
    summary_path = case_dir / "summary.json"
    input_path.write_text(namelist)

    driver_executable = driver_executable.resolve()
    if not driver_executable.exists():
        raise FileNotFoundError(
            f"Kernel driver executable not found at {driver_executable}. "
            "Build it first with scripts/build_kernel_driver.py."
        )

    completed = subprocess.run(
        [str(driver_executable)],
        input=namelist,
        text=True,
        cwd=case_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds,
        check=False,
    )
    transcript_path.write_text(completed.stdout)
    points = parse_kernel_driver_output(completed.stdout)
    diagnostics = parse_kernel_header(completed.stdout)
    failure_markers = parse_kernel_failure_markers(completed.stdout)
    converged_alpha = [point.alpha_deg for point in points if point.converged]
    missing_alpha = _missing_requested_alpha(case.alpha_deg, converged_alpha)
    nonconvergence_diagnostics = build_nonconvergence_diagnostics(
        case.alpha_deg,
        points,
        options=case.options,
        header=diagnostics,
        failure_markers=failure_markers,
    )
    summary: dict[str, Any] = {
        "case_id": case.id,
        "returncode": completed.returncode,
        "ok": completed.returncode == 0,
        "complete": completed.returncode == 0 and not missing_alpha,
        "input_file": str(input_path),
        "transcript_file": str(transcript_path),
        "requested_alpha_deg": list(case.alpha_deg),
        "converged_alpha_deg": converged_alpha,
        "missing_alpha_deg": missing_alpha,
        "points": [point.to_dict() for point in points],
        "diagnostics": diagnostics,
        "nonconvergence_diagnostics": nonconvergence_diagnostics,
        "failure_markers": failure_markers,
    }
    if completed.returncode != 0:
        summary["error"] = f"Kernel driver exited with return code {completed.returncode}."
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def compare_to_reference(summary: Mapping[str, Any], reference: Mapping[str, Any]) -> list[dict[str, float]]:
    """Return coefficient differences for points present in a pristine reference."""

    points_by_alpha = {
        round(float(point["alpha_deg"]), 8): point
        for point in summary.get("points", [])
        if point.get("converged")
    }
    differences: list[dict[str, float]] = []
    for reference_point in reference.get("polar", {}).get("points", []):
        alpha = round(float(reference_point["alpha_deg"]), 8)
        point = points_by_alpha.get(alpha)
        if point is None:
            continue
        difference = {
            "alpha_deg": float(reference_point["alpha_deg"]),
            "d_cl": float(point["cl"]) - float(reference_point["cl"]),
            "d_cd": float(point["cd"]) - float(reference_point["cd"]),
            "d_cm": float(point["cm"]) - float(reference_point["cm"]),
        }
        if (
            reference.get("options", {}).get("viscous", False)
            and "xtr_top" in reference_point
            and "xtr_bottom" in reference_point
        ):
            difference["d_xtr_top"] = float(point["xtr_top"]) - float(reference_point["xtr_top"])
            difference["d_xtr_bottom"] = float(point["xtr_bottom"]) - float(reference_point["xtr_bottom"])
        differences.append(difference)
    return differences


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run baseline cases through the direct-call kernel driver.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_KERNEL_RUN_ROOT)
    parser.add_argument("--kernel-root", type=Path, default=KERNEL_ROOT)
    parser.add_argument("--driver-executable", type=Path, default=DEFAULT_KERNEL_DRIVER_EXECUTABLE)
    parser.add_argument("--case", action="append", dest="case_ids", help="Case id to run. May be repeated.")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    if args.case_ids:
        selected = set(args.case_ids)
        cases = [case for case in cases if case.id in selected]
        missing = selected - {case.id for case in cases}
        if missing:
            raise SystemExit(f"Unknown case id(s): {', '.join(sorted(missing))}")

    for case in cases:
        try:
            summary = run_kernel_case(
                case,
                driver_executable=args.driver_executable,
                run_root=args.run_root,
                kernel_root=args.kernel_root,
                timeout_seconds=args.timeout,
            )
        except FileNotFoundError as exc:
            print(f"{case.id}: failed")
            print(str(exc))
            return 2
        status = "ok" if summary.get("ok") else "failed"
        completeness = "complete" if summary.get("complete") else "incomplete"
        print(f"{case.id}: {status}, {completeness}")
    return 0


def _prepare_coordinate_airfoil(case: BaselineCase, case_dir: Path, kernel_root: Path) -> Path | None:
    if str(case.airfoil.get("type", "")).lower() != "coordinates":
        return None
    source = _resolve_kernel_path(str(case.airfoil["path"]), kernel_root)
    destination = case_dir / source.name
    if source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)
    return Path(destination.name)


def _resolve_kernel_path(path: str, kernel_root: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return (kernel_root / candidate).resolve()


def _missing_requested_alpha(requested_alpha: Sequence[float], completed_alpha: Sequence[float]) -> list[float]:
    missing = []
    for requested in requested_alpha:
        if not any(abs(float(requested) - float(value)) <= _ALPHA_TOLERANCE for value in completed_alpha):
            missing.append(float(requested))
    return missing


def _find_point_for_requested_alpha(
    requested_alpha: float,
    points: Sequence[KernelDriverPoint],
) -> KernelDriverPoint | None:
    for point in points:
        if abs(point.alpha_deg - requested_alpha) <= _ALPHA_TOLERANCE:
            return point
    return None


def _point_nonconvergence_diagnostic(
    *,
    requested_alpha: float,
    point: KernelDriverPoint,
    options: Mapping[str, Any],
    header: Mapping[str, Any],
) -> dict[str, Any]:
    viscous = _diagnostic_bool("viscous", options, header, default=False)
    reason = "viscous_nonconvergence" if viscous else "point_not_converged"
    message = (
        "Viscous boundary-layer solve did not report convergence for the requested alpha."
        if viscous
        else "Kernel emitted a result row that was not marked converged."
    )
    diagnostic: dict[str, Any] = {
        "index": point.index,
        "requested_alpha_deg": requested_alpha,
        "alpha_deg": point.alpha_deg,
        "reason": reason,
        "message": message,
        "rms_bl": point.rms_bl,
        "cl": point.cl,
        "cd": point.cd,
        "cm": point.cm,
        "cdp": point.cdp,
        "actual_xtr_top": point.xtr_top,
        "actual_xtr_bottom": point.xtr_bottom,
        "transition_forced_top": point.transition_forced_top,
        "transition_forced_bottom": point.transition_forced_bottom,
    }
    diagnostic.update(_diagnostic_operating_context(options, header))
    return diagnostic


def _diagnostic_operating_context(
    options: Mapping[str, Any],
    header: Mapping[str, Any],
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    viscous = _diagnostic_bool("viscous", options, header, default=False)
    context["viscous"] = viscous
    _add_context_float(context, "reynolds_number", _first_present(options, "reynolds_number", header, "reynolds"))
    _add_context_float(context, "mach_number", _first_present(options, "mach_number", header, "mach"))
    _add_context_float(context, "ncrit_top", _ncrit_context_value(options, header, top=True))
    _add_context_float(context, "ncrit_bottom", _ncrit_context_value(options, header, top=False))
    _add_context_float(context, "requested_xtr_top", _first_present(options, "xtr_top", header, "xtr_top"))
    _add_context_float(context, "requested_xtr_bottom", _first_present(options, "xtr_bottom", header, "xtr_bottom"))
    _add_context_int(context, "panel_count", _first_present(options, "panel_count", header, "n_panels"))
    _add_context_int(context, "itmax", options.get("itmax"))
    if viscous and "itmax" in context:
        context["viscal_iteration_limit"] = context["itmax"] + 5
    return context


def _diagnostic_bool(
    key: str,
    options: Mapping[str, Any],
    header: Mapping[str, Any],
    *,
    default: bool,
) -> bool:
    if key in options:
        return bool(options[key])
    if key in header:
        return bool(header[key])
    return default


def _first_present(
    first_mapping: Mapping[str, Any],
    first_key: str,
    second_mapping: Mapping[str, Any],
    second_key: str,
) -> Any:
    if first_key in first_mapping:
        return first_mapping[first_key]
    return second_mapping.get(second_key)


def _ncrit_context_value(
    options: Mapping[str, Any],
    header: Mapping[str, Any],
    *,
    top: bool,
) -> Any:
    if top:
        if "ncrit_top" in options:
            return options["ncrit_top"]
        if "ncrit" in options:
            return options["ncrit"]
        return header.get("ncrit_top")
    if "ncrit_bottom" in options:
        return options["ncrit_bottom"]
    if "ncrit_top" in options:
        return options["ncrit_top"]
    if "ncrit" in options:
        return options["ncrit"]
    return header.get("ncrit_bottom")


def _add_context_float(context: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        context[key] = float(value)


def _add_context_int(context: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        context[key] = int(value)


def _fortran_bool(value: bool) -> str:
    return ".true." if value else ".false."


def _ncrit_values(options: Mapping[str, Any]) -> tuple[float, float]:
    ncrit = float(options.get("ncrit", 9.0))
    ncrit_top = float(options.get("ncrit_top", ncrit))
    ncrit_bottom = float(options.get("ncrit_bottom", ncrit_top if "ncrit_top" in options else ncrit))
    return ncrit_top, ncrit_bottom


def _parse_fortran_bool(value: str) -> bool:
    normalized = value.strip().upper()
    if normalized in {"T", ".T.", "TRUE", ".TRUE."}:
        return True
    if normalized in {"F", ".F.", "FALSE", ".FALSE."}:
        return False
    raise ValueError(f"Cannot parse Fortran logical value {value!r}.")


def _parse_kernel_float(value: str) -> float:
    normalized = value.strip().replace("D", "E").replace("d", "E")
    try:
        return float(normalized)
    except ValueError:
        match = _MISSING_EXPONENT_MARKER_RE.match(normalized)
        if match is None:
            raise
        return float(f"{match.group(1)}E{match.group(2)}")


def _parse_kernel_header_line(line: str) -> dict[str, Any]:
    payload = line[len("XK_HEADER ") :]
    matches = list(_HEADER_FIELD_RE.finditer(payload))
    header: dict[str, Any] = {}
    for index, match in enumerate(matches):
        key = match.group(1)
        value_start = match.end()
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(payload)
        raw_value = payload[value_start:value_end].strip()
        header[key] = _parse_kernel_header_value(key, raw_value)
    return header


def _parse_kernel_header_value(key: str, value: str) -> Any:
    if key in {"schema", "n_panels"}:
        return int(value)
    if key in {"viscous", "geometry_changed", "options_changed"}:
        return _parse_fortran_bool(value)
    if key in {
        "version",
        "reynolds",
        "mach",
        "ncrit_top",
        "ncrit_bottom",
        "xtr_top",
        "xtr_bottom",
    }:
        return _parse_kernel_float(value)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
