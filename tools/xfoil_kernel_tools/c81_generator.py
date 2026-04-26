from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import yaml

from .driver import DEFAULT_KERNEL_DRIVER_EXECUTABLE
from .paths import KERNEL_ROOT
from .session import DEFAULT_KERNEL_SESSION_EXECUTABLE
from .worker import XFoilKernelWorker


DEFAULT_C81_RUNTIME_ROOT = KERNEL_ROOT / "runs" / "c81-generation"
DEFAULT_RETRY_OPTIONS = {
    "enabled": True,
    "initial_sequence": "warm_start",
    "warm_start_alpha_deg": 0.0,
    "reverse_sequence": True,
    "single_points": False,
    "refinement_factors": [0.5, 0.25],
    "step_sizes_deg": [],
    "approach_from": ["below", "above"],
}


class C81GenerationError(RuntimeError):
    """Raised when an offline C81 generation request is invalid or incomplete."""


def generate_c81_from_manifest(
    manifest_path: str | Path,
    *,
    driver_executable: str | Path | None = None,
    session_executable: str | Path | None = None,
    use_session: bool | None = None,
    runtime_root: str | Path | None = None,
    kernel_root: str | Path = KERNEL_ROOT,
    allow_incomplete: bool | None = None,
) -> dict[str, Any]:
    """Generate C81 files from a YAML manifest and return a report dictionary."""

    manifest_path = Path(manifest_path).expanduser().resolve()
    manifest = _load_manifest(manifest_path)
    base_dir = manifest_path.parent
    kernel_root = Path(kernel_root).expanduser().resolve()

    output_root = _resolve_path(manifest.get("output_root", "."), base_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    report_path = _resolve_path(
        manifest.get("report", output_root / "c81_generation_report.json"),
        base_dir,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_allow_incomplete = bool(manifest.get("allow_incomplete", False))
    effective_allow_incomplete = (
        manifest_allow_incomplete if allow_incomplete is None else bool(allow_incomplete)
    )

    worker_config = dict(manifest.get("worker", {}))
    driver_path = (
        Path(driver_executable).expanduser().resolve()
        if driver_executable is not None
        else _resolve_path(
            worker_config.get("driver_executable", DEFAULT_KERNEL_DRIVER_EXECUTABLE),
            base_dir,
        )
    )
    session_path = (
        Path(session_executable).expanduser().resolve()
        if session_executable is not None
        else _resolve_path(
            worker_config.get("session_executable", DEFAULT_KERNEL_SESSION_EXECUTABLE),
            base_dir,
        )
    )
    worker_use_session = bool(worker_config.get("use_session", True)) if use_session is None else bool(use_session)
    runtime_path = (
        Path(runtime_root).expanduser().resolve()
        if runtime_root is not None
        else _resolve_path(
            worker_config.get("runtime_root", DEFAULT_C81_RUNTIME_ROOT),
            base_dir,
        )
    )
    worker = XFoilKernelWorker(
        driver_executable=driver_path,
        session_executable=session_path,
        use_session=worker_use_session,
        runtime_root=runtime_path,
        kernel_root=kernel_root,
    )

    defaults = dict(manifest.get("defaults", {}))
    airfoils = dict(manifest.get("airfoils", {}))
    tables = manifest.get("tables")
    if not isinstance(tables, list) or not tables:
        raise C81GenerationError("C81 manifest requires a non-empty 'tables' list.")

    report: dict[str, Any] = {
        "ok": True,
        "manifest": str(manifest_path),
        "report_file": str(report_path),
        "output_root": str(output_root),
        "allow_incomplete": effective_allow_incomplete,
        "tables": [],
        "written_files": [],
    }

    try:
        registered_airfoils: set[str] = set()
        for table_spec in tables:
            table_report = _generate_one_table(
                worker,
                table_spec=_expect_mapping(table_spec, "tables[]"),
                defaults=defaults,
                airfoils=airfoils,
                registered_airfoils=registered_airfoils,
                base_dir=base_dir,
                output_root=output_root,
                allow_incomplete=effective_allow_incomplete,
            )
            report["tables"].append(table_report)
            report["written_files"].extend(table_report.get("written_files", []))
            if not table_report.get("ok", False):
                report["ok"] = False

        report_path.write_text(json.dumps(report, indent=2) + "\n")
        return report
    finally:
        close = getattr(worker, "close", None)
        if callable(close):
            close()


def _generate_one_table(
    worker: XFoilKernelWorker,
    *,
    table_spec: Mapping[str, Any],
    defaults: Mapping[str, Any],
    airfoils: Mapping[str, Any],
    registered_airfoils: set[str],
    base_dir: Path,
    output_root: Path,
    allow_incomplete: bool,
) -> dict[str, Any]:
    airfoil_id = str(table_spec["airfoil"])
    table_id = str(table_spec.get("id", _safe_id(airfoil_id)))
    c81_airfoil_id = str(
        table_spec.get("c81_airfoil_id", table_spec.get("output_airfoil_id", airfoil_id))
    )
    output_dir = _resolve_output_dir(table_spec.get("output_dir", "."), output_root)
    output_dir.mkdir(parents=True, exist_ok=True)

    reynolds_values = _coerce_float_list(
        table_spec.get("reynolds", table_spec.get("reynolds_numbers", defaults.get("reynolds"))),
        "reynolds",
    )
    mach_values = _coerce_float_list(
        table_spec.get("mach", table_spec.get("machs", defaults.get("mach", defaults.get("machs")))),
        "mach",
    )
    alpha_values = _parse_alpha_spec(table_spec.get("alpha", defaults.get("alpha")))
    options = _merge_dicts(defaults.get("options", {}), table_spec.get("options", {}))
    retry_options = _normalize_retry_options(
        _merge_retry_specs(
            defaults.get("retry", defaults.get("retries")),
            table_spec.get("retry", table_spec.get("retries")),
        )
    )
    header_format = str(table_spec.get("header_format", defaults.get("header_format", "commas")))
    timeout_seconds = float(table_spec.get("timeout_seconds", defaults.get("timeout_seconds", 120.0)))

    _ensure_airfoil_registered(
        worker,
        airfoil_id=airfoil_id,
        airfoil_specs=airfoils,
        registered_airfoils=registered_airfoils,
        base_dir=base_dir,
    )

    collection: dict[float, dict[str, dict[float, dict[str, list[float]]]]] = {}
    table_report: dict[str, Any] = {
        "id": table_id,
        "airfoil": airfoil_id,
        "c81_airfoil_id": c81_airfoil_id,
        "output_dir": str(output_dir),
        "ok": True,
        "complete": True,
        "reynolds": [],
        "written_files": [],
    }

    for reynolds in reynolds_values:
        reynolds_report, reynolds_data = _solve_reynolds_table(
            worker,
            airfoil_id=airfoil_id,
            reynolds=reynolds,
            mach_values=mach_values,
            alpha_values=alpha_values,
            options=options,
            retry_options=retry_options,
            timeout_seconds=timeout_seconds,
        )
        table_report["reynolds"].append(reynolds_report)
        if not reynolds_report["complete"]:
            table_report["complete"] = False
            if not allow_incomplete:
                table_report["ok"] = False
        if reynolds_data is not None:
            collection[float(reynolds)] = reynolds_data

    if not collection:
        table_report["ok"] = False
        table_report["error"] = "No complete Reynolds tables were available to write."
        return table_report

    if table_report["complete"] or allow_incomplete:
        try:
            written_files = _write_c81_collection(
                airfoil_id=c81_airfoil_id,
                collection=collection,
                output_dir=output_dir,
                header_format=header_format,
            )
        except Exception as exc:
            table_report["ok"] = False
            table_report["error"] = str(exc)
        else:
            table_report["written_files"] = written_files
    else:
        table_report["error"] = (
            "At least one requested XFOIL point did not converge; no C81 files "
            "were written for this table. Set allow_incomplete: true to write "
            "tables from converged common alpha points."
        )
    return table_report


def _solve_reynolds_table(
    worker: XFoilKernelWorker,
    *,
    airfoil_id: str,
    reynolds: float,
    mach_values: Sequence[float],
    alpha_values: Sequence[float],
    options: Mapping[str, Any],
    retry_options: Mapping[str, Any],
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, dict[float, dict[str, list[float]]]] | None]:
    reynolds_report: dict[str, Any] = {
        "reynolds": float(reynolds),
        "complete": True,
        "machs": [],
    }
    by_mach: dict[float, list[Mapping[str, Any]]] = {}

    for mach in mach_values:
        mach_report, target_points = _solve_mach_table_with_retries(
            worker,
            airfoil_id=airfoil_id,
            reynolds=reynolds,
            mach=mach,
            alpha_values=alpha_values,
            options=options,
            retry_options=retry_options,
            timeout_seconds=timeout_seconds,
        )
        if not mach_report["complete"]:
            reynolds_report["complete"] = False
        reynolds_report["machs"].append(mach_report)
        if target_points:
            by_mach[float(mach)] = target_points

    if not by_mach:
        return reynolds_report, None

    common_alpha = _common_converged_alpha(by_mach)
    if not common_alpha:
        reynolds_report["complete"] = False
        reynolds_report["error"] = "No common converged alpha points across Mach values."
        return reynolds_report, None

    reynolds_data = {
        "cl": {},
        "cd": {},
        "cm": {},
    }
    for mach, points in by_mach.items():
        point_by_alpha = {round(float(point["alpha_deg"]), 8): point for point in points}
        for coefficient_name in ("cl", "cd", "cm"):
            values = [
                float(point_by_alpha[round(alpha, 8)][coefficient_name])
                for alpha in common_alpha
            ]
            reynolds_data[coefficient_name][float(mach)] = {
                "alpha": list(common_alpha),
                coefficient_name: values,
            }
    return reynolds_report, reynolds_data


def _solve_mach_table_with_retries(
    worker: XFoilKernelWorker,
    *,
    airfoil_id: str,
    reynolds: float,
    mach: float,
    alpha_values: Sequence[float],
    options: Mapping[str, Any],
    retry_options: Mapping[str, Any],
    timeout_seconds: float,
) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    target_alphas = [float(alpha) for alpha in alpha_values]
    target_by_key = {_alpha_key(alpha): alpha for alpha in target_alphas}
    points_by_key: dict[float, Mapping[str, Any]] = {}
    attempts: list[dict[str, Any]] = []
    attempted_sequences: set[tuple[float, ...]] = set()

    solve_options = dict(options)
    solve_options["reynolds_number"] = float(reynolds)
    solve_options["mach_number"] = float(mach)

    def run_attempt(label: str, sequence: Sequence[float], *, force: bool = False) -> None:
        if not sequence:
            return
        alpha_sequence = [float(value) for value in sequence]
        sequence_key = tuple(_alpha_key(value) for value in alpha_sequence)
        if sequence_key in attempted_sequences and not force:
            return
        attempted_sequences.add(sequence_key)
        response = worker.handle(
            {
                "cmd": "solve_alpha_sequence",
                "airfoil_id": airfoil_id,
                "options": solve_options,
                "alpha_deg": alpha_sequence,
                "timeout_seconds": float(timeout_seconds),
            }
        )
        collected = _collect_converged_target_points(
            response.get("points", []),
            target_by_key=target_by_key,
            points_by_key=points_by_key,
        )
        attempts.append(
            {
                "label": label,
                "alpha_deg": alpha_sequence,
                "ok": bool(response.get("ok", False)),
                "worker_complete": bool(response.get("complete", False)) if response.get("ok") else False,
                "worker_missing_alpha_deg": response.get("missing_alpha_deg", alpha_sequence),
                "converged_target_alpha_deg": collected,
                "missing_target_alpha_deg_after_attempt": _missing_target_alphas(
                    target_alphas,
                    points_by_key,
                ),
                "diagnostics": response.get("diagnostics", {}),
                "nonconvergence_diagnostics": response.get("nonconvergence_diagnostics", []),
                "failure_markers": response.get("failure_markers", []),
                "artifacts": response.get("artifacts", {}),
                **({"error": response.get("error", response)} if not response.get("ok") else {}),
            }
        )

    initial_sequence = _initial_alpha_sequence(target_alphas, retry_options)
    run_attempt("initial", initial_sequence)
    missing = _missing_target_alphas(target_alphas, points_by_key)

    if missing and retry_options.get("enabled", True):
        pre_reverse_missing = _pre_reverse_refinement_targets(missing, retry_options)
        if pre_reverse_missing:
            missing = _run_local_refinement_attempts(
                pre_reverse_missing,
                target_alphas=target_alphas,
                points_by_key=points_by_key,
                retry_options=retry_options,
                run_attempt=run_attempt,
            )

        if missing and retry_options.get("reverse_sequence", True):
            run_attempt("reverse", list(reversed(initial_sequence)))
            missing = _missing_target_alphas(target_alphas, points_by_key)
            missing = _run_local_refinement_attempts(
                missing,
                target_alphas=target_alphas,
                points_by_key=points_by_key,
                retry_options=retry_options,
                run_attempt=run_attempt,
                label_prefix="post_reverse_",
                force_attempt=True,
            )

        if retry_options.get("single_points", False):
            for target in list(missing):
                run_attempt("single_alpha", [target])
                missing = _missing_target_alphas(target_alphas, points_by_key)

    final_missing = _missing_target_alphas(target_alphas, points_by_key)
    target_points = [
        points_by_key[_alpha_key(alpha)]
        for alpha in target_alphas
        if _alpha_key(alpha) in points_by_key
    ]
    mach_report: dict[str, Any] = {
        "mach": float(mach),
        "complete": not final_missing,
        "requested_alpha_deg": target_alphas,
        "converged_alpha_deg": [
            float(point["alpha_deg"])
            for point in target_points
        ],
        "missing_alpha_deg": final_missing,
        "attempts": attempts,
    }
    if final_missing:
        mach_report["error"] = (
            "Missing requested alpha point(s) after retry attempts."
        )
    return mach_report, target_points


def _pre_reverse_refinement_targets(
    missing: Sequence[float],
    retry_options: Mapping[str, Any],
) -> list[float]:
    if retry_options.get("initial_sequence", "warm_start") != "warm_start":
        return list(missing)
    warm_start_alpha = retry_options.get("warm_start_alpha_deg")
    if warm_start_alpha is None:
        return list(missing)
    anchor = float(warm_start_alpha)
    return [float(alpha) for alpha in missing if float(alpha) >= anchor]


def _run_local_refinement_attempts(
    missing: Sequence[float],
    *,
    target_alphas: Sequence[float],
    points_by_key: Mapping[float, Mapping[str, Any]],
    retry_options: Mapping[str, Any],
    run_attempt,
    label_prefix: str = "",
    force_attempt: bool = False,
) -> list[float]:
    current_missing = list(missing)
    for factor in retry_options.get("refinement_factors", []):
        if not current_missing:
            break
        for target in list(current_missing):
            for direction in retry_options.get("approach_from", ["below", "above"]):
                anchor = _nearest_converged_anchor(
                    target_alphas,
                    target,
                    direction,
                    points_by_key,
                )
                if anchor is None:
                    continue
                sequence = _refined_sequence(anchor, target, float(factor))
                run_attempt(
                    f"{label_prefix}refine_{direction}_factor_{float(factor):g}",
                    sequence,
                    force=force_attempt,
                )
                current_missing = _missing_target_alphas(target_alphas, points_by_key)
                if target not in current_missing:
                    break

    for step_size in retry_options.get("step_sizes_deg", []):
        if not current_missing:
            break
        for target in list(current_missing):
            for direction in retry_options.get("approach_from", ["below", "above"]):
                anchor = _nearest_converged_anchor(
                    target_alphas,
                    target,
                    direction,
                    points_by_key,
                )
                if anchor is None:
                    continue
                sequence = _sequence_with_max_step(anchor, target, float(step_size))
                run_attempt(
                    f"{label_prefix}refine_{direction}_step_{float(step_size):g}",
                    sequence,
                    force=force_attempt,
                )
                current_missing = _missing_target_alphas(target_alphas, points_by_key)
                if target not in current_missing:
                    break
    return current_missing


def _ensure_airfoil_registered(
    worker: XFoilKernelWorker,
    *,
    airfoil_id: str,
    airfoil_specs: Mapping[str, Any],
    registered_airfoils: set[str],
    base_dir: Path,
) -> None:
    if airfoil_id in registered_airfoils:
        return

    spec = airfoil_specs.get(airfoil_id)
    payload = _airfoil_registration_payload(airfoil_id, spec, base_dir=base_dir)
    response = worker.handle({"cmd": "register_airfoil", "airfoil_id": airfoil_id, **payload})
    if not response.get("ok"):
        raise C81GenerationError(
            f"Failed to register airfoil {airfoil_id!r}: {response.get('error', response)}"
        )
    registered_airfoils.add(airfoil_id)


def _airfoil_registration_payload(
    airfoil_id: str,
    spec: Any,
    *,
    base_dir: Path,
) -> dict[str, Any]:
    if spec is None:
        naca_code = _parse_naca_code(airfoil_id)
        if naca_code is not None:
            return {"naca": naca_code}
        raise C81GenerationError(
            f"No airfoil spec for {airfoil_id!r}. Provide airfoils.{airfoil_id} "
            "or use a NACA-style airfoil id."
        )

    if isinstance(spec, str):
        naca_code = _parse_naca_code(spec)
        if naca_code is not None:
            return {"naca": naca_code}
        return {"airfoil": {"type": "coordinates", "path": str(_resolve_path(spec, base_dir))}}

    mapping = dict(_expect_mapping(spec, f"airfoils.{airfoil_id}"))
    if "naca" in mapping:
        return {"naca": str(mapping["naca"])}
    airfoil_type = str(mapping.get("type", "")).lower()
    if airfoil_type == "naca":
        return {"airfoil": {"type": "naca", "code": str(mapping["code"])}}
    coordinate_path = mapping.get("path", mapping.get("file", mapping.get("coordinates")))
    if airfoil_type == "coordinates" or coordinate_path is not None:
        if isinstance(coordinate_path, str):
            return {
                "airfoil": {
                    "type": "coordinates",
                    "path": str(_resolve_path(coordinate_path, base_dir)),
                    "panel": bool(mapping.get("panel", True)),
                }
            }
        if isinstance(coordinate_path, Mapping):
            return {
                "airfoil": {
                    "type": "coordinates",
                    "coordinates": deepcopy(coordinate_path),
                    "panel": bool(mapping.get("panel", True)),
                }
            }
    raise C81GenerationError(f"Unsupported airfoil spec for {airfoil_id!r}: {spec!r}")


def _write_c81_collection(
    *,
    airfoil_id: str,
    collection: Mapping[float, Mapping[str, Mapping]],
    output_dir: Path,
    header_format: str,
) -> list[str]:
    try:
        from c81_utils.from_dict import generate_c81
    except ImportError as exc:
        raise C81GenerationError(
            "Generating C81 files requires the optional 'c81_utils' package. "
            "Install or add c81_utils to PYTHONPATH before running this command."
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    return [
        str(Path(path).resolve())
        for path in generate_c81(
            airfoil_id,
            dict(collection),
            output_dir,
            header_format=header_format,
        )
    ]


def _load_manifest(path: Path) -> dict[str, Any]:
    with open(path, "r") as stream:
        manifest = yaml.safe_load(stream)
    if not isinstance(manifest, dict):
        raise C81GenerationError(f"C81 manifest '{path}' must contain a YAML mapping.")
    return manifest


def _parse_alpha_spec(spec: Any) -> list[float]:
    if spec is None:
        raise C81GenerationError("C81 table spec requires alpha values.")
    if isinstance(spec, list):
        return _coerce_float_list(spec, "alpha")
    mapping = dict(_expect_mapping(spec, "alpha"))
    if "values" in mapping:
        return _coerce_float_list(mapping["values"], "alpha.values")
    try:
        start = float(mapping["start"])
        end = float(mapping.get("end", mapping.get("stop")))
        step = float(mapping["step"])
    except KeyError as exc:
        raise C81GenerationError(
            "alpha mapping must contain either 'values' or start/end/step."
        ) from exc
    return _make_alpha_sequence(start, end, step)


def _make_alpha_sequence(start: float, end: float, step: float) -> list[float]:
    if step == 0.0:
        raise C81GenerationError("alpha.step must be nonzero.")
    if end < start and step > 0.0:
        step = -step
    if end > start and step < 0.0:
        step = -step
    n_points = int((end - start) / step + 0.5) + 1
    return [float(start + step * index) for index in range(max(n_points, 1))]


def _coerce_float_list(value: Any, name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or not value:
        raise C81GenerationError(f"{name} must be a non-empty list.")
    return [float(item) for item in value]


def _merge_retry_specs(default_spec: Any, table_spec: Any) -> Any:
    if table_spec is None:
        return default_spec
    if isinstance(default_spec, Mapping) and isinstance(table_spec, Mapping):
        return _merge_dicts(default_spec, table_spec)
    return table_spec


def _normalize_retry_options(spec: Any) -> dict[str, Any]:
    options = dict(DEFAULT_RETRY_OPTIONS)
    if spec is None:
        pass
    elif isinstance(spec, bool):
        options["enabled"] = spec
    elif isinstance(spec, Mapping):
        options.update(dict(spec))
    else:
        raise C81GenerationError("retry options must be a mapping or boolean.")

    options["enabled"] = bool(options.get("enabled", True))
    initial_sequence = str(options.get("initial_sequence", "warm_start")).lower()
    if initial_sequence not in {"warm_start", "as_requested"}:
        raise C81GenerationError(
            "retry.initial_sequence must be 'warm_start' or 'as_requested'."
        )
    options["initial_sequence"] = initial_sequence
    warm_start_alpha = options.get("warm_start_alpha_deg", options.get("warm_start_alpha"))
    options["warm_start_alpha_deg"] = (
        None if warm_start_alpha is None else float(warm_start_alpha)
    )
    options["reverse_sequence"] = bool(options.get("reverse_sequence", True))
    options["single_points"] = bool(options.get("single_points", False))

    factors = [float(value) for value in options.get("refinement_factors", [])]
    if any(value <= 0.0 or value > 1.0 for value in factors):
        raise C81GenerationError("retry.refinement_factors must be in the interval (0, 1].")
    options["refinement_factors"] = factors

    step_sizes = [float(value) for value in options.get("step_sizes_deg", [])]
    if any(value <= 0.0 for value in step_sizes):
        raise C81GenerationError("retry.step_sizes_deg values must be positive.")
    options["step_sizes_deg"] = step_sizes

    approach_from = [str(value).lower() for value in options.get("approach_from", [])]
    if not approach_from:
        approach_from = ["below", "above"]
    invalid = sorted(set(approach_from) - {"below", "above"})
    if invalid:
        raise C81GenerationError(
            "retry.approach_from may only contain 'below' and/or 'above'."
        )
    options["approach_from"] = approach_from
    return options


def _initial_alpha_sequence(
    target_alphas: Sequence[float],
    retry_options: Mapping[str, Any],
) -> list[float]:
    if retry_options.get("initial_sequence", "warm_start") == "as_requested":
        return [float(alpha) for alpha in target_alphas]
    warm_start_alpha = retry_options.get("warm_start_alpha_deg")
    if warm_start_alpha is None:
        return [float(alpha) for alpha in target_alphas]
    return _warm_start_sequence(target_alphas, float(warm_start_alpha))


def _warm_start_sequence(target_alphas: Sequence[float], warm_start_alpha: float) -> list[float]:
    target_by_key = {_alpha_key(alpha): float(alpha) for alpha in target_alphas}
    unique_targets = sorted(target_by_key.values())
    warm_key = _alpha_key(warm_start_alpha)
    anchor = target_by_key.get(warm_key, float(warm_start_alpha))

    below = sorted((alpha for alpha in unique_targets if alpha < anchor), reverse=True)
    above = sorted(alpha for alpha in unique_targets if alpha > anchor)

    sequence = [anchor]
    sequence.extend(above)
    if below and above:
        sequence.append(anchor)
    sequence.extend(below)
    return sequence


def _collect_converged_target_points(
    points: Sequence[Mapping[str, Any]],
    *,
    target_by_key: Mapping[float, float],
    points_by_key: dict[float, Mapping[str, Any]],
) -> list[float]:
    collected: list[float] = []
    for point in points:
        if not point.get("converged", False):
            continue
        try:
            key = _alpha_key(float(point["alpha_deg"]))
        except (KeyError, TypeError, ValueError):
            continue
        target_alpha = target_by_key.get(key)
        if target_alpha is None or key in points_by_key:
            continue
        target_point = dict(point)
        target_point["alpha_deg"] = target_alpha
        points_by_key[key] = target_point
        collected.append(float(target_alpha))
    return collected


def _missing_target_alphas(
    target_alphas: Sequence[float],
    points_by_key: Mapping[float, Mapping[str, Any]],
) -> list[float]:
    return [
        float(alpha)
        for alpha in target_alphas
        if _alpha_key(alpha) not in points_by_key
    ]


def _alpha_key(alpha: float) -> float:
    return round(float(alpha), 8)


def _nearest_converged_anchor(
    target_alphas: Sequence[float],
    target: float,
    direction: str,
    points_by_key: Mapping[float, Mapping[str, Any]],
) -> float | None:
    target = float(target)
    converged_keys = set(points_by_key)
    if direction == "below":
        candidates = [
            float(alpha)
            for alpha in target_alphas
            if float(alpha) < target and _alpha_key(alpha) in converged_keys
        ]
        return max(candidates, default=None)
    if direction == "above":
        candidates = [
            float(alpha)
            for alpha in target_alphas
            if float(alpha) > target and _alpha_key(alpha) in converged_keys
        ]
        return min(candidates, default=None)
    raise C81GenerationError(f"Unsupported retry approach direction {direction!r}.")


def _refined_sequence(anchor: float, target: float, factor: float) -> list[float]:
    distance = float(target) - float(anchor)
    if distance == 0.0:
        return [float(target)]
    step = abs(distance) * float(factor)
    return _sequence_with_max_step(anchor, target, step)


def _sequence_with_max_step(anchor: float, target: float, step_size: float) -> list[float]:
    anchor = float(anchor)
    target = float(target)
    step_size = abs(float(step_size))
    if step_size == 0.0:
        raise C81GenerationError("retry step size must be nonzero.")
    distance = target - anchor
    if distance == 0.0:
        return [target]
    n_intervals = max(1, int(abs(distance) / step_size + 0.999999999))
    return [
        anchor + distance * index / n_intervals
        for index in range(n_intervals + 1)
    ]


def _common_converged_alpha(by_mach: Mapping[float, Sequence[Mapping[str, Any]]]) -> list[float]:
    alpha_sets = [
        {round(float(point["alpha_deg"]), 8) for point in points}
        for points in by_mach.values()
    ]
    if not alpha_sets:
        return []
    return sorted(set.intersection(*alpha_sets))


def _resolve_path(path: str | Path, base_dir: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate.resolve()


def _resolve_output_dir(path: str | Path, output_root: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = output_root / candidate
    return candidate.resolve()


def _merge_dicts(base: Any, override: Any) -> dict[str, Any]:
    result = dict(base or {})
    result.update(dict(override or {}))
    return result


def _expect_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise C81GenerationError(f"{name} must be a mapping.")
    return value


def _parse_naca_code(value: str) -> str | None:
    compact = str(value).strip().replace(" ", "").replace("-", "").upper()
    match = re.fullmatch(r"(?:NACA)?([0-9]{4,5})", compact)
    if match:
        return match.group(1)
    return None


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_") or "airfoil"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate C81 tables with the XFOIL kernel.")
    parser.add_argument("manifest", type=Path, help="YAML C81 generation manifest.")
    parser.add_argument("--driver-executable", type=Path, default=None)
    parser.add_argument("--session-executable", type=Path, default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--use-session", dest="use_session", action="store_true", default=None)
    mode.add_argument("--one-shot", dest="use_session", action="store_false")
    parser.add_argument("--runtime-root", type=Path, default=None)
    parser.add_argument("--kernel-root", type=Path, default=KERNEL_ROOT)
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Write tables from common converged alpha points even when some requested points fail.",
    )
    args = parser.parse_args(argv)

    try:
        report = generate_c81_from_manifest(
            args.manifest,
            driver_executable=args.driver_executable,
            session_executable=args.session_executable,
            use_session=args.use_session,
            runtime_root=args.runtime_root,
            kernel_root=args.kernel_root,
            allow_incomplete=(True if args.allow_incomplete else None),
        )
    except C81GenerationError as exc:
        print(str(exc))
        return 2

    print(f"report: {report['report_file']}")
    for table in report["tables"]:
        status = "ok" if table.get("ok") else "failed"
        completeness = "complete" if table.get("complete") else "incomplete"
        print(f"{table['id']}: {status}, {completeness}")
        for path in table.get("written_files", []):
            print(f"  wrote {path}")
    return 0 if report.get("ok") else 1
