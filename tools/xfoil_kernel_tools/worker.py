from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, IO, Mapping, Sequence
from uuid import uuid4

from .baseline import BaselineCase
from .driver import (
    DEFAULT_KERNEL_DRIVER_EXECUTABLE,
    DEFAULT_KERNEL_RUN_ROOT,
    run_kernel_case,
)
from .paths import KERNEL_ROOT
from .session import DEFAULT_KERNEL_SESSION_EXECUTABLE, KernelSession


DEFAULT_WORKER_RUNTIME_ROOT = KERNEL_ROOT / "runs" / "worker"
WORKER_PROTOCOL_VERSION = 1
WORKER_IMPLEMENTATION = "python-json-lines"
WORKER_COMMANDS = (
    "ping",
    "status",
    "register_airfoil",
    "reset_boundary_layer_state",
    "solve_alpha_sequence",
    "shutdown",
)
WORKER_SOLVE_OPTIONS = (
    "viscous",
    "reynolds_number",
    "mach_number",
    "ncrit",
    "ncrit_top",
    "ncrit_bottom",
    "xtr_top",
    "xtr_bottom",
    "itmax",
    "panel_count",
)
WORKER_SOLVE_OPTION_SET = frozenset(WORKER_SOLVE_OPTIONS)


@dataclass(frozen=True)
class RegisteredAirfoil:
    """Airfoil registered with the JSON-lines worker."""

    airfoil_id: str
    airfoil: Mapping[str, Any]


class XFoilKernelWorker:
    """Persistent JSON-lines worker fronting the direct-call XFOIL driver."""

    def __init__(
        self,
        *,
        driver_executable: Path = DEFAULT_KERNEL_DRIVER_EXECUTABLE,
        session_executable: Path = DEFAULT_KERNEL_SESSION_EXECUTABLE,
        use_session: bool = True,
        runtime_root: Path = DEFAULT_WORKER_RUNTIME_ROOT,
        kernel_root: Path = KERNEL_ROOT,
    ) -> None:
        self.driver_executable = driver_executable
        self.session_executable = session_executable
        self.use_session = use_session
        self.runtime_root = runtime_root
        self.kernel_root = kernel_root
        self.registry: dict[str, RegisteredAirfoil] = {}
        self._solve_count = 0
        self._session: KernelSession | None = None

    def handle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Handle one protocol request and return one protocol response."""

        request_id = request.get("request_id")
        try:
            cmd = str(request["cmd"])
            if cmd == "ping":
                return self._response(request_id, ok=True)
            if cmd == "status":
                return self._status(request_id)
            if cmd == "register_airfoil":
                return self._register_airfoil(request)
            if cmd == "reset_boundary_layer_state":
                return self._reset_boundary_layer_state(request)
            if cmd == "solve_alpha_sequence":
                return self._solve_alpha_sequence(request)
            if cmd == "shutdown":
                self.close()
                return self._response(request_id, ok=True)
            return self._error(request_id, "unknown_command", f"Unknown worker command {cmd!r}.")
        except KeyError as exc:
            return self._error(request_id, "missing_field", f"Missing required field {exc.args[0]!r}.")
        except (TypeError, ValueError) as exc:
            return self._error(request_id, "invalid_request", str(exc))
        except Exception as exc:
            return self._error(
                request_id,
                "internal_error",
                f"{type(exc).__name__}: {exc}",
            )

    def serve(self, input_stream: IO[str], output_stream: IO[str]) -> None:
        """Serve newline-delimited JSON requests until EOF or shutdown."""

        for raw_line in input_stream:
            line = raw_line.strip()
            if not line:
                continue
            request: Mapping[str, Any] | None = None
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                response = self._error(None, "invalid_json", str(exc))
            else:
                if not isinstance(request, Mapping):
                    response = self._error(None, "invalid_request", "Request must be a JSON object.")
                else:
                    response = self.handle(request)
            output_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
            output_stream.flush()
            if request is not None and request.get("cmd") == "shutdown":
                break

    def _register_airfoil(self, request: Mapping[str, Any]) -> dict[str, Any]:
        airfoil_id = str(request["airfoil_id"])
        airfoil = _airfoil_from_request(request, runtime_root=self.runtime_root, airfoil_id=airfoil_id)
        self.registry[airfoil_id] = RegisteredAirfoil(airfoil_id=airfoil_id, airfoil=airfoil)
        response = self._response(request.get("request_id"), ok=True)
        response["airfoil_id"] = airfoil_id
        if airfoil["type"] == "coordinates":
            response["geometry_path"] = str(airfoil["path"])
        return response

    def _solve_alpha_sequence(self, request: Mapping[str, Any]) -> dict[str, Any]:
        airfoil_id = str(request["airfoil_id"])
        registered = self.registry.get(airfoil_id)
        if registered is None:
            return self._error(
                request.get("request_id"),
                "unknown_airfoil",
                f"Airfoil {airfoil_id!r} has not been registered.",
            )

        alpha_deg = _validated_alpha_sequence(request["alpha_deg"])
        if not alpha_deg:
            return self._error(request.get("request_id"), "empty_alpha_sequence", "alpha_deg must not be empty.")
        options = _validated_solve_options(request.get("options", {}))
        timeout_seconds = _positive_finite_float(
            request.get("timeout_seconds", 120.0),
            "timeout_seconds",
        )

        self._solve_count += 1
        case = BaselineCase(
            id=f"{_safe_id(airfoil_id)}_{self._solve_count:06d}_{uuid4().hex[:8]}",
            description=f"worker solve for {airfoil_id}",
            airfoil=registered.airfoil,
            options=options,
            alpha_deg=alpha_deg,
        )
        try:
            if self.use_session:
                summary = self._kernel_session().solve_case(
                    case,
                    run_root=self.runtime_root / "session-cases",
                    kernel_root=self.kernel_root,
                    timeout_seconds=timeout_seconds,
                )
            else:
                summary = run_kernel_case(
                    case,
                    driver_executable=self.driver_executable,
                    run_root=self.runtime_root / "cases",
                    kernel_root=self.kernel_root,
                    timeout_seconds=timeout_seconds,
                )
        except FileNotFoundError as exc:
            return self._error(request.get("request_id"), "driver_not_found", str(exc))
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            if self.use_session:
                self._discard_session()
            return self._error(
                request.get("request_id"),
                "driver_timeout",
                str(exc),
                details={"case_id": case.id, "mode": self._mode()},
            )
        except (BrokenPipeError, OSError, RuntimeError) as exc:
            if self.use_session:
                self._discard_session()
            return self._error(
                request.get("request_id"),
                "driver_failed",
                str(exc),
                details={"case_id": case.id, "mode": self._mode()},
            )
        except ValueError as exc:
            return self._error(
                request.get("request_id"),
                "driver_output_parse_failed",
                str(exc),
                details={"case_id": case.id, "mode": self._mode()},
            )

        if not summary.get("ok"):
            return self._error(
                request.get("request_id"),
                "driver_failed",
                str(summary.get("error", "Kernel driver failed.")),
                details={"case_id": case.id, "returncode": summary.get("returncode")},
            )

        response = self._response(request.get("request_id"), ok=True)
        response.update(
            {
                "airfoil_id": airfoil_id,
                "complete": bool(summary.get("complete")),
                "requested_alpha_deg": summary["requested_alpha_deg"],
                "converged_alpha_deg": summary["converged_alpha_deg"],
                "missing_alpha_deg": summary["missing_alpha_deg"],
                "points": summary["points"],
                "diagnostics": dict(summary.get("diagnostics", {})),
                "nonconvergence_diagnostics": list(summary.get("nonconvergence_diagnostics", [])),
                "failure_markers": list(summary.get("failure_markers", [])),
                "artifacts": {
                    "case_id": case.id,
                    "input_file": summary["input_file"],
                    "transcript_file": summary["transcript_file"],
                },
            }
        )
        return response

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def _reset_boundary_layer_state(self, request: Mapping[str, Any]) -> dict[str, Any]:
        response = self._response(request.get("request_id"), ok=True)
        response["mode"] = self._mode()
        if not self.use_session:
            response["reset_performed"] = False
            response["reason"] = "one_shot_mode_has_no_persistent_boundary_layer_state"
            return response
        if self._session is None:
            response["reset_performed"] = False
            response["reason"] = "no_active_session"
            return response
        try:
            message = self._session.reset_boundary_layer_state(
                timeout_seconds=float(request.get("timeout_seconds", 10.0)),
            )
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            self._discard_session()
            return self._error(
                request.get("request_id"),
                "driver_timeout",
                str(exc),
                details={"mode": self._mode(), "command": "reset_boundary_layer_state"},
            )
        except (BrokenPipeError, OSError, RuntimeError) as exc:
            self._discard_session()
            return self._error(
                request.get("request_id"),
                "driver_failed",
                str(exc),
                details={"mode": self._mode(), "command": "reset_boundary_layer_state"},
            )
        response["reset_performed"] = True
        response["message"] = message
        return response

    def _status(self, request_id: Any) -> dict[str, Any]:
        response = self._response(request_id, ok=True)
        response.update(
            {
                "protocol_version": WORKER_PROTOCOL_VERSION,
                "implementation": WORKER_IMPLEMENTATION,
                "mode": self._mode(),
                "session_active": self._session is not None,
                "registered_airfoils": sorted(self.registry),
                "runtime_root": str(self.runtime_root),
                "driver_executable": str(self.driver_executable),
                "session_executable": str(self.session_executable),
                "capabilities": {
                    "commands": list(WORKER_COMMANDS),
                    "airfoil_types": ["naca", "coordinates"],
                    "sequence_types": ["alpha"],
                    "solve_options": list(WORKER_SOLVE_OPTIONS),
                    "persistent_session": bool(self.use_session),
                    "cl_sequence": False,
                },
            }
        )
        return response

    def _kernel_session(self) -> KernelSession:
        if self._session is None:
            self._session = KernelSession(
                session_executable=self.session_executable,
                runtime_root=self.runtime_root / "session",
            )
        return self._session

    def _discard_session(self) -> None:
        if self._session is None:
            return
        try:
            self._session.close(kill=True)
        finally:
            self._session = None

    def _mode(self) -> str:
        return "session" if self.use_session else "one_shot"

    @staticmethod
    def _response(request_id: Any, *, ok: bool) -> dict[str, Any]:
        response = {"ok": ok}
        if request_id is not None:
            response["request_id"] = request_id
        return response

    def _error(
        self,
        request_id: Any,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._response(request_id, ok=False)
        error: dict[str, Any] = {"code": code, "message": message}
        if details:
            error.update(details)
        response["error"] = error
        return response


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the XFOIL JSON-lines worker.")
    parser.add_argument("--driver-executable", type=Path, default=DEFAULT_KERNEL_DRIVER_EXECUTABLE)
    parser.add_argument("--session-executable", type=Path, default=DEFAULT_KERNEL_SESSION_EXECUTABLE)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--use-session", dest="use_session", action="store_true", default=True)
    mode.add_argument("--one-shot", dest="use_session", action="store_false")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_WORKER_RUNTIME_ROOT)
    parser.add_argument("--kernel-root", type=Path, default=KERNEL_ROOT)
    args = parser.parse_args(argv)

    worker = XFoilKernelWorker(
        driver_executable=args.driver_executable,
        session_executable=args.session_executable,
        use_session=args.use_session,
        runtime_root=args.runtime_root,
        kernel_root=args.kernel_root,
    )
    try:
        worker.serve(sys.stdin, sys.stdout)
    finally:
        worker.close()
    return 0


def _airfoil_from_request(
    request: Mapping[str, Any],
    *,
    runtime_root: Path,
    airfoil_id: str,
) -> Mapping[str, Any]:
    if "airfoil" in request:
        airfoil = dict(_expect_mapping(request["airfoil"], "airfoil"))
        airfoil_type = str(airfoil.get("type", "")).lower()
        if airfoil_type == "naca":
            return {"type": "naca", "code": str(airfoil["code"])}
        if airfoil_type == "coordinates":
            if "path" in airfoil:
                return {
                    "type": "coordinates",
                    "path": str(Path(str(airfoil["path"])).expanduser().resolve()),
                    "panel": bool(airfoil.get("panel", True)),
                }
            if "coordinates" in airfoil:
                return _coordinate_airfoil_from_arrays(
                    _expect_mapping(airfoil["coordinates"], "airfoil.coordinates"),
                    runtime_root=runtime_root,
                    airfoil_id=airfoil_id,
                    panel=bool(airfoil.get("panel", True)),
                )
        raise ValueError(f"Unsupported airfoil type {airfoil_type!r}.")

    if "naca" in request:
        return {"type": "naca", "code": str(request["naca"])}

    if "coordinates" in request:
        return _coordinate_airfoil_from_arrays(
            _expect_mapping(request["coordinates"], "coordinates"),
            runtime_root=runtime_root,
            airfoil_id=airfoil_id,
            panel=bool(request.get("panel", True)),
        )

    raise ValueError("register_airfoil requires naca, coordinates, or airfoil.")


def _coordinate_airfoil_from_arrays(
    coordinates: Mapping[str, Any],
    *,
    runtime_root: Path,
    airfoil_id: str,
    panel: bool,
) -> Mapping[str, Any]:
    x_values = [_finite_float(value, f"coordinates.x[{index}]") for index, value in enumerate(coordinates["x"])]
    y_values = [_finite_float(value, f"coordinates.y[{index}]") for index, value in enumerate(coordinates["y"])]
    if len(x_values) != len(y_values):
        raise ValueError("Coordinate arrays x and y must have the same length.")
    if len(x_values) < 3:
        raise ValueError("At least three coordinate points are required.")

    airfoil_dir = runtime_root / "airfoils"
    airfoil_dir.mkdir(parents=True, exist_ok=True)
    path = airfoil_dir / f"{_safe_id(airfoil_id)}_{uuid4().hex[:8]}.dat"
    lines = [airfoil_id]
    lines.extend(f"{x:.12g} {y:.12g}" for x, y in zip(x_values, y_values, strict=True))
    path.write_text("\n".join(lines) + "\n")
    return {"type": "coordinates", "path": str(path.resolve()), "panel": panel}


def _expect_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object.")
    return value


def _validated_alpha_sequence(value: Any) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("alpha_deg must be a JSON array.")
    return tuple(
        _finite_float(alpha, f"alpha_deg[{index}]")
        for index, alpha in enumerate(value)
    )


def _validated_solve_options(value: Any) -> dict[str, Any]:
    options = dict(_expect_mapping(value, "options"))
    unknown = sorted(set(options) - WORKER_SOLVE_OPTION_SET)
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"Unsupported solve option(s): {joined}.")

    validated: dict[str, Any] = {}
    for key, raw_value in options.items():
        if key == "viscous":
            validated[key] = _bool_value(raw_value, f"options.{key}")
        elif key == "reynolds_number":
            validated[key] = _positive_finite_float(raw_value, f"options.{key}")
        elif key == "mach_number":
            validated[key] = _nonnegative_finite_float(raw_value, f"options.{key}")
        elif key in {"ncrit", "ncrit_top", "ncrit_bottom"}:
            validated[key] = _positive_finite_float(raw_value, f"options.{key}")
        elif key in {"xtr_top", "xtr_bottom"}:
            validated[key] = _unit_interval_float(raw_value, f"options.{key}")
        elif key == "itmax":
            validated[key] = _positive_int(raw_value, f"options.{key}")
        elif key == "panel_count":
            panel_count = _positive_int(raw_value, f"options.{key}")
            if panel_count <= 1:
                raise ValueError("options.panel_count must be greater than one.")
            validated[key] = panel_count

    if bool(validated.get("viscous", False)) and "reynolds_number" not in validated:
        raise ValueError("options.reynolds_number is required when options.viscous is true.")
    return validated


def _bool_value(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be true or false.")
    return value


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer.")
    if isinstance(value, int):
        integer = value
    elif isinstance(value, float) and value.is_integer():
        integer = int(value)
    else:
        raise ValueError(f"{field_name} must be an integer.")
    if integer <= 0:
        raise ValueError(f"{field_name} must be positive.")
    return integer


def _finite_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite number.")
    return number


def _positive_finite_float(value: Any, field_name: str) -> float:
    number = _finite_float(value, field_name)
    if number <= 0.0:
        raise ValueError(f"{field_name} must be positive.")
    return number


def _nonnegative_finite_float(value: Any, field_name: str) -> float:
    number = _finite_float(value, field_name)
    if number < 0.0:
        raise ValueError(f"{field_name} must be non-negative.")
    return number


def _unit_interval_float(value: Any, field_name: str) -> float:
    number = _finite_float(value, field_name)
    if number < 0.0 or number > 1.0:
        raise ValueError(f"{field_name} must be between 0.0 and 1.0.")
    return number


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe or "airfoil"


if __name__ == "__main__":
    raise SystemExit(main())
