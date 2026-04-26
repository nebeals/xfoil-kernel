from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Iterable, Mapping, Sequence

from .paths import DEFAULT_XFOIL_ROOT, KERNEL_ROOT

DEFAULT_CASES_PATH = KERNEL_ROOT / "baselines" / "cases.json"
DEFAULT_OUTPUT_ROOT = KERNEL_ROOT / "baselines" / "pristine"
DEFAULT_REFERENCE_ROOT = KERNEL_ROOT / "baselines" / "reference"
DEFAULT_XFOIL_EXECUTABLE = DEFAULT_XFOIL_ROOT / "bin" / "xfoil"


_FLOAT_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
_FLOAT_RE = re.compile(_FLOAT_PATTERN)


@dataclass(frozen=True)
class BaselineCase:
    """One pristine-XFOIL baseline case definition."""

    id: str
    description: str
    airfoil: Mapping[str, Any]
    options: Mapping[str, Any]
    alpha_deg: tuple[float, ...]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "BaselineCase":
        alpha = tuple(float(value) for value in data["alpha_deg"])
        if not alpha:
            raise ValueError(f"Baseline case {data.get('id', '<unknown>')} has no alpha_deg values.")
        return cls(
            id=str(data["id"]),
            description=str(data.get("description", "")),
            airfoil=dict(data["airfoil"]),
            options=dict(data.get("options", {})),
            alpha_deg=alpha,
        )


@dataclass(frozen=True)
class PolarPoint:
    """One parsed row from an XFOIL polar save file."""

    values: Mapping[str, float]

    @property
    def alpha_deg(self) -> float:
        return self.values["alpha_deg"]

    @property
    def cl(self) -> float:
        return self.values["cl"]

    @property
    def cd(self) -> float:
        return self.values["cd"]

    @property
    def cm(self) -> float:
        return self.values["cm"]

    def to_dict(self) -> dict[str, float]:
        return dict(self.values)


@dataclass(frozen=True)
class PolarFile:
    """Parsed XFOIL polar save file with header metadata and point rows."""

    path: Path
    airfoil_name: str | None = None
    mach_number: float | None = None
    reynolds_number: float | None = None
    ncrit_top: float | None = None
    ncrit_bottom: float | None = None
    forced_xtr_top: float | None = None
    forced_xtr_bottom: float | None = None
    columns: tuple[str, ...] = field(default_factory=tuple)
    points: tuple[PolarPoint, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "airfoil_name": self.airfoil_name,
            "mach_number": self.mach_number,
            "reynolds_number": self.reynolds_number,
            "ncrit_top": self.ncrit_top,
            "ncrit_bottom": self.ncrit_bottom,
            "forced_xtr_top": self.forced_xtr_top,
            "forced_xtr_bottom": self.forced_xtr_bottom,
            "columns": list(self.columns),
            "points": [point.to_dict() for point in self.points],
        }


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[BaselineCase]:
    """Load baseline case definitions from JSON."""

    data = json.loads(path.read_text())
    return [BaselineCase.from_mapping(case) for case in data.get("cases", [])]


def build_input_deck(
    case: BaselineCase,
    *,
    polar_path: Path,
    kernel_root: Path = KERNEL_ROOT,
    coordinate_path: Path | None = None,
) -> str:
    """Build the stdin command deck for one pristine-XFOIL run."""

    lines: list[str] = []
    airfoil = case.airfoil
    airfoil_type = str(airfoil.get("type", "")).lower()

    if airfoil_type == "naca":
        lines.append(f"NACA {airfoil['code']}")
    elif airfoil_type == "coordinates":
        load_path = coordinate_path
        if load_path is None:
            load_path = _resolve_kernel_path(str(airfoil["path"]), kernel_root)
        lines.append(f"LOAD {load_path}")
    else:
        raise ValueError(f"Unsupported airfoil type '{airfoil_type}' in case {case.id}.")

    options = dict(case.options)
    if bool(options.get("disable_graphics", True)):
        lines.extend(["PLOP", "G", ""])

    panel_count = options.get("panel_count")
    if panel_count is not None and int(panel_count) != 160:
        lines.extend(["PPAR", f"N {int(panel_count)}", "", ""])

    if airfoil_type == "coordinates" and bool(airfoil.get("panel", True)):
        lines.append("PANE")

    lines.append("OPER")

    itmax = options.get("itmax")
    if itmax is not None:
        lines.append(f"ITER {int(itmax)}")

    mach_number = float(options.get("mach_number", 0.0))
    if mach_number != 0.0:
        lines.append(f"MACH {mach_number:g}")

    if bool(options.get("viscous", False)):
        reynolds_number = float(options["reynolds_number"])
        lines.append(f"VISC {reynolds_number:.9g}")
        lines.extend(_vpar_lines(options))

    lines.append("PACC")
    lines.append(str(polar_path))
    lines.append("")

    lines.extend(_alpha_command_lines(case.alpha_deg))

    lines.append("PACC")
    lines.append("")
    lines.append("QUIT")
    lines.append("")
    return "\n".join(lines)


def parse_xfoil_polar(path: Path) -> PolarFile:
    """Parse an XFOIL polar save file written by PACC/PWRT."""

    airfoil_name: str | None = None
    mach_number: float | None = None
    reynolds_number: float | None = None
    ncrit_top: float | None = None
    ncrit_bottom: float | None = None
    forced_xtr_top: float | None = None
    forced_xtr_bottom: float | None = None
    columns: tuple[str, ...] = ()
    points: list[PolarPoint] = []
    in_data = False

    for raw_line in path.read_text(errors="replace").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        if "Calculated polar for:" in line:
            airfoil_name = line.split("Calculated polar for:", 1)[1].strip()
            continue

        xtr_match = re.search(
            rf"xtrf\s*=\s*({_FLOAT_PATTERN})\s*\(top\)\s*({_FLOAT_PATTERN})\s*\(bottom\)",
            line,
            flags=re.IGNORECASE,
        )
        if xtr_match:
            forced_xtr_top = float(xtr_match.group(1))
            forced_xtr_bottom = float(xtr_match.group(2))
            continue

        if "Mach" in line and "Re" in line:
            mach_match = re.search(rf"Mach\s*=\s*({_FLOAT_PATTERN})", line)
            re_match = re.search(rf"Re\s*=\s*({_FLOAT_PATTERN})\s*e\s*6", line, flags=re.IGNORECASE)
            ncrit_match = re.search(rf"Ncrit\s*=\s*(.*)$", line)
            if mach_match:
                mach_number = float(mach_match.group(1))
            if re_match:
                reynolds_number = float(re_match.group(1)) * 1.0e6
            if ncrit_match:
                ncrit_values = _parse_floats(ncrit_match.group(1))
                if ncrit_values:
                    ncrit_top = ncrit_values[0]
                    ncrit_bottom = ncrit_values[1] if len(ncrit_values) > 1 else ncrit_values[0]
            continue

        if not in_data and _looks_like_column_line(stripped):
            columns = tuple(_normalize_column_name(token) for token in stripped.split())
            continue

        if columns and set(stripped) <= {"-", " "}:
            in_data = True
            continue

        if in_data:
            numbers = _parse_floats(stripped)
            if not numbers:
                continue
            if len(numbers) < len(columns):
                raise ValueError(
                    f"Polar data row in {path} has {len(numbers)} values but {len(columns)} columns: {line!r}"
                )
            values = {
                column: float(numbers[index])
                for index, column in enumerate(columns)
            }
            points.append(PolarPoint(values=values))

    if not columns:
        raise ValueError(f"Could not find XFOIL polar column labels in {path}.")

    return PolarFile(
        path=path,
        airfoil_name=airfoil_name,
        mach_number=mach_number,
        reynolds_number=reynolds_number,
        ncrit_top=ncrit_top,
        ncrit_bottom=ncrit_bottom,
        forced_xtr_top=forced_xtr_top,
        forced_xtr_bottom=forced_xtr_bottom,
        columns=columns,
        points=tuple(points),
    )


def run_case(
    case: BaselineCase,
    *,
    xfoil_executable: Path,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    kernel_root: Path = KERNEL_ROOT,
    timeout_seconds: float = 120.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run or prepare one pristine-XFOIL baseline case."""

    case_dir = output_root / case.id
    case_dir.mkdir(parents=True, exist_ok=True)
    polar_path = case_dir / f"{case.id}.polar"
    polar_command_path = Path(f"{case.id}.polar")
    input_path = case_dir / "input.xfoil"
    transcript_path = case_dir / "transcript.txt"
    summary_path = case_dir / "summary.json"

    coordinate_command_path = _prepare_coordinate_airfoil(case, case_dir, kernel_root)
    input_deck = build_input_deck(
        case,
        polar_path=polar_command_path,
        kernel_root=kernel_root,
        coordinate_path=coordinate_command_path,
    )
    input_path.write_text(input_deck)

    summary: dict[str, Any] = {
        "case_id": case.id,
        "description": case.description,
        "input_file": str(input_path),
        "polar_file": str(polar_path),
        "transcript_file": str(transcript_path),
        "dry_run": dry_run,
    }

    if dry_run:
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        return summary

    xfoil_executable = xfoil_executable.resolve()
    if not xfoil_executable.exists():
        raise FileNotFoundError(
            f"XFOIL executable not found at {xfoil_executable}. "
            "Build pristine XFOIL first or pass --dry-run."
        )

    if polar_path.exists():
        polar_path.unlink()

    completed = subprocess.run(
        [str(xfoil_executable)],
        input=input_deck,
        text=True,
        cwd=case_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds,
        check=False,
    )
    transcript_path.write_text(completed.stdout)
    summary["returncode"] = completed.returncode

    if completed.returncode != 0:
        summary["ok"] = False
        summary["error"] = f"XFOIL exited with return code {completed.returncode}."
    elif not polar_path.exists():
        summary["ok"] = False
        summary["error"] = "XFOIL completed but did not write the expected polar file."
    else:
        polar = parse_xfoil_polar(polar_path)
        completed_alpha = [point.alpha_deg for point in polar.points]
        missing_alpha = _missing_requested_alpha(case.alpha_deg, completed_alpha)
        summary["ok"] = True
        summary["complete"] = not missing_alpha
        summary["requested_alpha_deg"] = list(case.alpha_deg)
        summary["completed_alpha_deg"] = completed_alpha
        summary["missing_alpha_deg"] = missing_alpha
        summary["polar"] = polar.to_dict()

    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def write_reference_baselines(
    *,
    cases_path: Path = DEFAULT_CASES_PATH,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    reference_root: Path = DEFAULT_REFERENCE_ROOT,
) -> list[Path]:
    """Write compact tracked reference JSON files from generated summaries."""

    cases_by_id = {case.id: case for case in load_cases(cases_path)}
    cases_file = _display_path(cases_path, relative_to=KERNEL_ROOT)
    reference_root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for summary_path in sorted(output_root.glob("*/summary.json")):
        summary = json.loads(summary_path.read_text())
        case_id = str(summary["case_id"])
        if not summary.get("ok"):
            raise ValueError(f"Cannot promote failed baseline {case_id}: {summary.get('error')}")
        case = cases_by_id[case_id]
        polar = dict(summary["polar"])
        polar.pop("path", None)

        reference = {
            "schema_version": 1,
            "case_id": case_id,
            "description": case.description,
            "airfoil": dict(case.airfoil),
            "options": dict(case.options),
            "requested_alpha_deg": summary.get("requested_alpha_deg", list(case.alpha_deg)),
            "completed_alpha_deg": summary.get("completed_alpha_deg", []),
            "missing_alpha_deg": summary.get("missing_alpha_deg", []),
            "complete": bool(summary.get("complete", False)),
            "source": {
                "tool": "pristine XFOIL",
                "cases_file": cases_file,
            },
            "polar": polar,
        }

        output_path = reference_root / f"{case_id}.json"
        output_path.write_text(json.dumps(reference, indent=2) + "\n")
        written.append(output_path)

    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run pristine-XFOIL baseline cases.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--kernel-root", type=Path, default=KERNEL_ROOT)
    parser.add_argument("--xfoil-executable", type=Path, default=DEFAULT_XFOIL_EXECUTABLE)
    parser.add_argument("--case", action="append", dest="case_ids", help="Case id to run. May be repeated.")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--dry-run", action="store_true", help="Write input decks without launching XFOIL.")
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
            summary = run_case(
                case,
                xfoil_executable=args.xfoil_executable,
                output_root=args.output_root,
                kernel_root=args.kernel_root,
                timeout_seconds=args.timeout,
                dry_run=args.dry_run,
            )
        except FileNotFoundError as exc:
            print(f"{case.id}: failed")
            print(str(exc))
            return 2
        status = "prepared" if args.dry_run else ("ok" if summary.get("ok") else "failed")
        print(f"{case.id}: {status}")
    return 0


def _vpar_lines(options: Mapping[str, Any]) -> list[str]:
    lines = ["VPAR"]
    ncrit_top, ncrit_bottom = _ncrit_values(options)
    if ncrit_top == ncrit_bottom:
        lines.append(f"N {ncrit_top:g}")
    else:
        lines.append(f"NT {ncrit_top:g}")
        lines.append(f"NB {ncrit_bottom:g}")

    xtr_top = float(options.get("xtr_top", 1.0))
    xtr_bottom = float(options.get("xtr_bottom", 1.0))
    lines.append(f"XTR {xtr_top:g} {xtr_bottom:g}")
    lines.append("")
    return lines


def _ncrit_values(options: Mapping[str, Any]) -> tuple[float, float]:
    ncrit = float(options.get("ncrit", 9.0))
    ncrit_top = float(options.get("ncrit_top", ncrit))
    ncrit_bottom = float(options.get("ncrit_bottom", ncrit_top if "ncrit_top" in options else ncrit))
    return ncrit_top, ncrit_bottom


def _alpha_command_lines(alpha_deg: Sequence[float]) -> list[str]:
    if len(alpha_deg) >= 2 and _is_regular_sequence(alpha_deg):
        step = alpha_deg[1] - alpha_deg[0]
        return [f"ASEQ {alpha_deg[0]:g} {alpha_deg[-1]:g} {step:g}"]
    return [f"ALFA {alpha:g}" for alpha in alpha_deg]


def _is_regular_sequence(values: Sequence[float], *, tolerance: float = 1.0e-9) -> bool:
    if len(values) < 2:
        return False
    step = values[1] - values[0]
    return all(abs((values[index] - values[index - 1]) - step) <= tolerance for index in range(2, len(values)))


def _resolve_kernel_path(path: str, kernel_root: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return (kernel_root / candidate).resolve()


def _display_path(path: Path, *, relative_to: Path) -> str:
    try:
        return str(path.resolve().relative_to(relative_to.resolve()))
    except ValueError:
        return str(path)


def _prepare_coordinate_airfoil(
    case: BaselineCase,
    case_dir: Path,
    kernel_root: Path,
) -> Path | None:
    if str(case.airfoil.get("type", "")).lower() != "coordinates":
        return None

    source = _resolve_kernel_path(str(case.airfoil["path"]), kernel_root)
    destination = case_dir / source.name
    if source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)
    return Path(destination.name)


def _looks_like_column_line(line: str) -> bool:
    tokens = line.split()
    return "alpha" in tokens and "CL" in tokens and any(token in tokens for token in ("CD", "CDp", "CM"))


def _normalize_column_name(name: str) -> str:
    mapping = {
        "alpha": "alpha_deg",
        "CL": "cl",
        "CD": "cd",
        "CDp": "cdp",
        "CM": "cm",
        "Mach": "mach_number",
        "Re": "reynolds_number",
        "Top_Xtr": "xtr_top",
        "Bot_Xtr": "xtr_bottom",
        "Top_Itr": "itr_top",
        "Bot_Itr": "itr_bottom",
        "Top_Ncrit": "ncrit_top",
        "Bot_Ncrit": "ncrit_bottom",
        "Top_Xtrip": "forced_xtr_top",
        "Bot_Xtrip": "forced_xtr_bottom",
        "Cpmin": "cp_min",
        "Chinge": "c_hinge",
    }
    return mapping.get(name, name.strip().lower())


def _parse_floats(text: str) -> list[float]:
    return [float(match.group(0)) for match in _FLOAT_RE.finditer(text)]


def _missing_requested_alpha(
    requested_alpha: Iterable[float],
    completed_alpha: Iterable[float],
    *,
    tolerance: float = 1.0e-6,
) -> list[float]:
    completed = list(completed_alpha)
    missing = []
    for requested in requested_alpha:
        if not any(abs(float(requested) - float(value)) <= tolerance for value in completed):
            missing.append(float(requested))
    return missing


if __name__ == "__main__":
    raise SystemExit(main())
