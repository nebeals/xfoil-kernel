from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from xfoil_kernel_tools import c81_generator
from xfoil_kernel_tools.c81_generator import C81GenerationError
from xfoil_kernel_tools.driver import (
    DEFAULT_KERNEL_DRIVER_EXECUTABLE,
    DEFAULT_KERNEL_RUN_ROOT,
)
from xfoil_kernel_tools.paths import KERNEL_ROOT
from xfoil_kernel_tools.session import DEFAULT_KERNEL_SESSION_EXECUTABLE
from xfoil_kernel_tools.worker import XFoilKernelWorker


try:
    __version__ = version("xfoil-kernel")
except PackageNotFoundError:
    __version__ = "0.1.0"


__all__ = [
    "__version__",
    "AirfoilRegistrationError",
    "AirfoilSpec",
    "AlphaSequenceResult",
    "C81GenerationError",
    "C81GenerationRequest",
    "C81GenerationResult",
    "IncompleteSolveError",
    "KernelConfig",
    "KernelError",
    "KernelExecutableNotFound",
    "KernelProtocolError",
    "PointResult",
    "RetryPolicy",
    "SolveOptions",
    "XfoilKernelClient",
    "generate_c81",
    "generate_c81_from_manifest",
]


class KernelError(RuntimeError):
    """Base error raised by the public XFOIL kernel API."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        response: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.response = dict(response) if response is not None else None


class KernelExecutableNotFound(KernelError):
    """Raised when a configured kernel executable is missing."""


class KernelProtocolError(KernelError):
    """Raised when the worker rejects a request or returns an error."""


class AirfoilRegistrationError(KernelProtocolError):
    """Raised when an airfoil cannot be registered with the worker."""


class IncompleteSolveError(KernelError):
    """Raised by convenience methods that require a complete solve."""


@dataclass(frozen=True)
class KernelConfig:
    """Executable and runtime configuration for one kernel client."""

    session_executable: Path | None = None
    driver_executable: Path | None = None
    runtime_root: Path | None = None
    use_session: bool = True
    timeout_seconds: float = 120.0
    kernel_root: Path | None = None

    @property
    def effective_session_executable(self) -> Path:
        return Path(self.session_executable or DEFAULT_KERNEL_SESSION_EXECUTABLE)

    @property
    def effective_driver_executable(self) -> Path:
        return Path(self.driver_executable or DEFAULT_KERNEL_DRIVER_EXECUTABLE)

    @property
    def effective_runtime_root(self) -> Path:
        return Path(self.runtime_root or DEFAULT_KERNEL_RUN_ROOT.parent / "api")

    @property
    def effective_kernel_root(self) -> Path:
        return Path(self.kernel_root or KERNEL_ROOT)

    def validate(self) -> None:
        """Validate runtime configuration before the worker is used."""

        _positive_finite_float(self.timeout_seconds, "timeout_seconds")
        _bool_value(self.use_session, "use_session")


@dataclass(frozen=True)
class AirfoilSpec:
    """NACA or coordinate airfoil specification."""

    kind: str
    code: str | None = None
    path: Path | None = None
    x: tuple[float, ...] | None = None
    y: tuple[float, ...] | None = None
    panel: bool = True

    @classmethod
    def naca(cls, code: str | int) -> AirfoilSpec:
        code_text = str(code).strip()
        if not code_text:
            raise ValueError("NACA airfoils require a non-empty code.")
        if not code_text.isdigit():
            raise ValueError("NACA airfoil code must contain only digits.")
        if int(code_text) <= 0:
            raise ValueError("NACA airfoil code must be positive.")
        return cls(kind="naca", code=code_text)

    @classmethod
    def coordinates_file(cls, path: str | Path, *, panel: bool = True) -> AirfoilSpec:
        _bool_value(panel, "panel")
        path_value = Path(path)
        if not str(path_value):
            raise ValueError("Coordinate airfoil path must not be empty.")
        return cls(kind="coordinates", path=Path(path), panel=panel)

    @classmethod
    def coordinates(
        cls,
        *,
        x: Sequence[float],
        y: Sequence[float],
        panel: bool = True,
    ) -> AirfoilSpec:
        _bool_value(panel, "panel")
        x_values = _finite_float_sequence(x, "x")
        y_values = _finite_float_sequence(y, "y")
        if len(x_values) != len(y_values):
            raise ValueError("Airfoil coordinate x and y arrays must have the same length.")
        if len(x_values) < 3:
            raise ValueError("At least three airfoil coordinate points are required.")
        return cls(
            kind="coordinates",
            x=x_values,
            y=y_values,
            panel=panel,
        )

    def to_worker_airfoil(self) -> dict[str, Any]:
        if self.kind == "naca":
            if self.code is None:
                raise ValueError("NACA airfoils require a code.")
            return {"type": "naca", "code": self.code}
        if self.kind == "coordinates":
            payload: dict[str, Any] = {"type": "coordinates", "panel": bool(self.panel)}
            if self.path is not None:
                payload["path"] = str(self.path)
                return payload
            if self.x is not None and self.y is not None:
                payload["coordinates"] = {
                    "x": list(self.x),
                    "y": list(self.y),
                }
                return payload
            raise ValueError("Coordinate airfoils require a file path or coordinate arrays.")
        raise ValueError(f"Unsupported airfoil kind {self.kind!r}.")

    def to_manifest_spec(self) -> dict[str, Any]:
        if self.kind == "naca":
            if self.code is None:
                raise ValueError("NACA airfoils require a code.")
            return {"naca": self.code}
        if self.kind == "coordinates":
            payload: dict[str, Any] = {"type": "coordinates", "panel": bool(self.panel)}
            if self.path is not None:
                payload["path"] = str(self.path)
                return payload
            if self.x is not None and self.y is not None:
                payload["coordinates"] = {
                    "x": list(self.x),
                    "y": list(self.y),
                }
                return payload
            raise ValueError("Coordinate airfoils require a file path or coordinate arrays.")
        raise ValueError(f"Unsupported airfoil kind {self.kind!r}.")

    def to_dict(self) -> dict[str, Any]:
        return self.to_manifest_spec()


@dataclass(frozen=True)
class SolveOptions:
    """Operating-point options for an alpha solve."""

    viscous: bool = True
    reynolds_number: float | None = None
    mach_number: float = 0.0
    ncrit: float | None = 9.0
    ncrit_top: float | None = None
    ncrit_bottom: float | None = None
    xtr_top: float = 1.0
    xtr_bottom: float = 1.0
    itmax: int = 50
    panel_count: int = 160

    def to_worker_options(self) -> dict[str, Any]:
        _bool_value(self.viscous, "viscous")
        mach_number = _nonnegative_finite_float(self.mach_number, "mach_number")
        xtr_top = _unit_interval_float(self.xtr_top, "xtr_top")
        xtr_bottom = _unit_interval_float(self.xtr_bottom, "xtr_bottom")
        itmax = _positive_int(self.itmax, "itmax")
        panel_count = _positive_int(self.panel_count, "panel_count")
        if panel_count <= 1:
            raise ValueError("panel_count must be greater than one.")

        options: dict[str, Any] = {
            "viscous": self.viscous,
            "mach_number": mach_number,
            "xtr_top": xtr_top,
            "xtr_bottom": xtr_bottom,
            "itmax": itmax,
            "panel_count": panel_count,
        }
        if self.reynolds_number is not None:
            options["reynolds_number"] = _positive_finite_float(
                self.reynolds_number,
                "reynolds_number",
            )
        if self.ncrit is not None:
            options["ncrit"] = _positive_finite_float(self.ncrit, "ncrit")
        if self.ncrit_top is not None:
            options["ncrit_top"] = _positive_finite_float(self.ncrit_top, "ncrit_top")
        if self.ncrit_bottom is not None:
            options["ncrit_bottom"] = _positive_finite_float(self.ncrit_bottom, "ncrit_bottom")
        return options

    def to_dict(self) -> dict[str, Any]:
        return self.to_worker_options()


@dataclass(frozen=True)
class PointResult:
    """One alpha-point result returned by the kernel."""

    alpha_deg: float
    cl: float
    cd: float
    cm: float
    cdp: float | None = None
    converged: bool = True
    rms_bl: float | None = None
    xtr_top: float | None = None
    xtr_bottom: float | None = None
    transition_forced_top: bool | None = None
    transition_forced_bottom: bool | None = None
    index: int | None = None

    @classmethod
    def from_mapping(cls, point: Mapping[str, Any]) -> PointResult:
        return cls(
            index=_optional_int(point.get("index")),
            alpha_deg=float(point["alpha_deg"]),
            cl=float(point["cl"]),
            cd=float(point["cd"]),
            cm=float(point["cm"]),
            cdp=_optional_float(point.get("cdp")),
            converged=bool(point.get("converged", True)),
            rms_bl=_optional_float(point.get("rms_bl")),
            xtr_top=_optional_float(point.get("xtr_top")),
            xtr_bottom=_optional_float(point.get("xtr_bottom")),
            transition_forced_top=_optional_bool(point.get("transition_forced_top")),
            transition_forced_bottom=_optional_bool(point.get("transition_forced_bottom")),
        )

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


@dataclass(frozen=True)
class AlphaSequenceResult:
    """Result from one requested alpha sequence."""

    ok: bool
    complete: bool
    airfoil_id: str | None
    requested_alpha_deg: tuple[float, ...]
    converged_alpha_deg: tuple[float, ...]
    missing_alpha_deg: tuple[float, ...]
    points: tuple[PointResult, ...]
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    nonconvergence_diagnostics: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    failure_markers: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    artifacts: Mapping[str, Any] = field(default_factory=dict)
    raw_response: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_response(cls, response: Mapping[str, Any]) -> AlphaSequenceResult:
        return cls(
            ok=bool(response.get("ok", False)),
            complete=bool(response.get("complete", False)),
            airfoil_id=str(response["airfoil_id"]) if "airfoil_id" in response else None,
            requested_alpha_deg=tuple(float(value) for value in response.get("requested_alpha_deg", [])),
            converged_alpha_deg=tuple(float(value) for value in response.get("converged_alpha_deg", [])),
            missing_alpha_deg=tuple(float(value) for value in response.get("missing_alpha_deg", [])),
            points=tuple(PointResult.from_mapping(point) for point in response.get("points", [])),
            diagnostics=dict(response.get("diagnostics", {})),
            nonconvergence_diagnostics=tuple(
                dict(item) for item in response.get("nonconvergence_diagnostics", [])
            ),
            failure_markers=tuple(dict(item) for item in response.get("failure_markers", [])),
            artifacts=dict(response.get("artifacts", {})),
            raw_response=dict(response),
        )

    def require_complete(self) -> AlphaSequenceResult:
        if not self.complete:
            raise IncompleteSolveError(
                f"XFOIL did not converge at all requested alpha points: {list(self.missing_alpha_deg)}",
                code="incomplete_solve",
                response=self.raw_response,
            )
        return self

    def point_at(
        self,
        alpha_deg: float,
        *,
        require_converged: bool = True,
        tolerance: float = 1.0e-6,
    ) -> PointResult:
        for point in self.points:
            if abs(point.alpha_deg - float(alpha_deg)) <= tolerance:
                if require_converged and not point.converged:
                    raise IncompleteSolveError(
                        f"XFOIL point at alpha={alpha_deg:g} deg did not converge.",
                        code="point_not_converged",
                        response=self.raw_response,
                    )
                return point
        raise IncompleteSolveError(
            f"No kernel result was returned for alpha={alpha_deg:g} deg.",
            code="missing_point",
            response=self.raw_response,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "complete": self.complete,
            "airfoil_id": self.airfoil_id,
            "requested_alpha_deg": list(self.requested_alpha_deg),
            "converged_alpha_deg": list(self.converged_alpha_deg),
            "missing_alpha_deg": list(self.missing_alpha_deg),
            "points": [point.to_dict() for point in self.points],
            "diagnostics": dict(self.diagnostics),
            "nonconvergence_diagnostics": [
                dict(item) for item in self.nonconvergence_diagnostics
            ],
            "failure_markers": [dict(item) for item in self.failure_markers],
            "artifacts": dict(self.artifacts),
        }


@dataclass(frozen=True)
class RetryPolicy:
    """Retry policy for offline C81 generation and advanced sequence solves."""

    enabled: bool = True
    initial_sequence: str = "warm_start"
    warm_start_alpha_deg: float | None = 0.0
    reverse_sequence: bool = True
    single_points: bool = False
    refinement_factors: tuple[float, ...] = (0.5, 0.25)
    step_sizes_deg: tuple[float, ...] = ()
    approach_from: tuple[str, ...] = ("below", "above")

    @classmethod
    def default(cls) -> RetryPolicy:
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "initial_sequence": self.initial_sequence,
            "warm_start_alpha_deg": self.warm_start_alpha_deg,
            "reverse_sequence": bool(self.reverse_sequence),
            "single_points": bool(self.single_points),
            "refinement_factors": list(self.refinement_factors),
            "step_sizes_deg": list(self.step_sizes_deg),
            "approach_from": list(self.approach_from),
        }


@dataclass(frozen=True)
class C81GenerationRequest:
    """Typed request for offline C81 generation."""

    output_root: Path
    report_file: Path | None = None
    allow_incomplete: bool = False
    airfoils: Mapping[str, AirfoilSpec] = field(default_factory=dict)
    tables: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    defaults: Mapping[str, Any] = field(default_factory=dict)
    base_dir: Path | None = None

    def to_manifest_dict(self) -> dict[str, Any]:
        output_root = Path(self.output_root)
        manifest: dict[str, Any] = {
            "output_root": str(output_root),
            "allow_incomplete": bool(self.allow_incomplete),
            "airfoils": {
                airfoil_id: airfoil.to_manifest_spec()
                for airfoil_id, airfoil in self.airfoils.items()
            },
            "defaults": _plain_data(self.defaults),
            "tables": [
                _normalize_c81_table_spec(table)
                for table in self.tables
            ],
        }
        if self.report_file is not None:
            manifest["report"] = str(self.report_file)
        return manifest


@dataclass(frozen=True)
class C81GenerationResult:
    """Report returned by offline C81 generation."""

    ok: bool
    report_file: Path | None
    output_root: Path | None
    allow_incomplete: bool
    tables: tuple[Mapping[str, Any], ...]
    written_files: tuple[Path, ...]
    raw_report: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, report: Mapping[str, Any]) -> C81GenerationResult:
        return cls(
            ok=bool(report.get("ok", False)),
            report_file=Path(str(report["report_file"])) if report.get("report_file") else None,
            output_root=Path(str(report["output_root"])) if report.get("output_root") else None,
            allow_incomplete=bool(report.get("allow_incomplete", False)),
            tables=tuple(dict(table) for table in report.get("tables", [])),
            written_files=tuple(Path(str(path)) for path in report.get("written_files", [])),
            raw_report=dict(report),
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw_report)


class XfoilKernelClient:
    """Public client wrapper around one persistent XFOIL kernel worker."""

    def __init__(self, config: KernelConfig | None = None) -> None:
        self.config = config or KernelConfig()
        self.config.validate()
        self._worker = XFoilKernelWorker(
            driver_executable=self.config.effective_driver_executable,
            session_executable=self.config.effective_session_executable,
            use_session=self.config.use_session,
            runtime_root=self.config.effective_runtime_root,
            kernel_root=self.config.effective_kernel_root,
        )
        self._closed = False

    def __enter__(self) -> XfoilKernelClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def register_airfoil(self, airfoil_id: str, airfoil: AirfoilSpec) -> None:
        if not isinstance(airfoil, AirfoilSpec):
            raise ValueError("airfoil must be an AirfoilSpec.")
        airfoil_id_text = str(airfoil_id).strip()
        if not airfoil_id_text:
            raise ValueError("airfoil_id must not be empty.")
        response = self._worker.handle(
            {
                "cmd": "register_airfoil",
                "airfoil_id": airfoil_id_text,
                "airfoil": airfoil.to_worker_airfoil(),
            }
        )
        if not response.get("ok", False):
            _raise_response_error(response, error_type=AirfoilRegistrationError)

    def solve_alpha_sequence(
        self,
        airfoil_id: str,
        *,
        alpha_deg: Sequence[float],
        options: SolveOptions,
        timeout_seconds: float | None = None,
    ) -> AlphaSequenceResult:
        if not isinstance(options, SolveOptions):
            raise ValueError("options must be a SolveOptions instance.")
        alpha_sequence = _finite_float_sequence(alpha_deg, "alpha_deg")
        if not alpha_sequence:
            raise ValueError("alpha_deg must not be empty.")
        if options.viscous and options.reynolds_number is None:
            raise ValueError("reynolds_number is required when viscous=True.")
        timeout = _positive_finite_float(
            timeout_seconds if timeout_seconds is not None else self.config.timeout_seconds,
            "timeout_seconds",
        )
        response = self._worker.handle(
            {
                "cmd": "solve_alpha_sequence",
                "airfoil_id": str(airfoil_id),
                "options": options.to_worker_options(),
                "alpha_deg": list(alpha_sequence),
                "timeout_seconds": timeout,
            }
        )
        if not response.get("ok", False):
            _raise_response_error(response)
        return AlphaSequenceResult.from_response(response)

    def solve_alpha(
        self,
        airfoil_id: str,
        *,
        alpha_deg: float,
        options: SolveOptions,
        warm_start: bool | Sequence[float] = True,
        timeout_seconds: float | None = None,
    ) -> PointResult:
        alpha = _finite_float(alpha_deg, "alpha_deg")
        alpha_sequence = _single_alpha_sequence(
            alpha,
            options=options,
            warm_start=warm_start,
        )
        result = self.solve_alpha_sequence(
            airfoil_id,
            alpha_deg=alpha_sequence,
            options=options,
            timeout_seconds=timeout_seconds,
        )
        return result.point_at(alpha, require_converged=True)

    def reset_boundary_layer_state(self, *, timeout_seconds: float | None = None) -> Mapping[str, Any]:
        """Reset XFOIL's persistent boundary-layer state when a session is active."""

        response = self._worker.handle(
            {
                "cmd": "reset_boundary_layer_state",
                "timeout_seconds": _positive_finite_float(
                    timeout_seconds if timeout_seconds is not None else self.config.timeout_seconds,
                    "timeout_seconds",
                ),
            }
        )
        if not response.get("ok", False):
            _raise_response_error(response)
        return response

    def status(self) -> Mapping[str, Any]:
        """Return worker protocol status and capability metadata."""

        response = self._worker.handle({"cmd": "status"})
        if not response.get("ok", False):
            _raise_response_error(response)
        return response

    def close(self) -> None:
        if self._closed:
            return
        close = getattr(self._worker, "close", None)
        if callable(close):
            close()
        self._closed = True


def generate_c81_from_manifest(
    manifest_path: str | Path,
    *,
    driver_executable: str | Path | None = None,
    session_executable: str | Path | None = None,
    use_session: bool | None = None,
    runtime_root: str | Path | None = None,
    kernel_root: str | Path = KERNEL_ROOT,
    allow_incomplete: bool | None = None,
) -> C81GenerationResult:
    """Generate C81 files from a YAML manifest and return a typed report."""

    report = c81_generator.generate_c81_from_manifest(
        manifest_path,
        driver_executable=driver_executable,
        session_executable=session_executable,
        use_session=use_session,
        runtime_root=runtime_root,
        kernel_root=kernel_root,
        allow_incomplete=allow_incomplete,
    )
    return C81GenerationResult.from_mapping(report)


def generate_c81(
    client: XfoilKernelClient,
    request: C81GenerationRequest,
) -> C81GenerationResult:
    """Generate C81 files from a typed request using an existing client."""

    manifest = request.to_manifest_dict()
    base_dir = Path(request.base_dir or Path.cwd()).expanduser().resolve()
    output_root = _resolve_path(manifest.get("output_root", request.output_root), base_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = _resolve_path(
        manifest.get("report", output_root / "c81_generation_report.json"),
        base_dir,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "ok": True,
        "manifest": None,
        "report_file": str(report_path),
        "output_root": str(output_root),
        "allow_incomplete": bool(request.allow_incomplete),
        "tables": [],
        "written_files": [],
    }
    registered_airfoils: set[str] = set()
    defaults = dict(manifest.get("defaults", {}))
    airfoils = dict(manifest.get("airfoils", {}))
    tables = manifest.get("tables", [])
    if not tables:
        raise C81GenerationError("C81GenerationRequest requires at least one table.")

    for table in tables:
        table_report = c81_generator._generate_one_table(  # noqa: SLF001
            client._worker,
            table_spec=dict(table),
            defaults=defaults,
            airfoils=airfoils,
            registered_airfoils=registered_airfoils,
            base_dir=base_dir,
            output_root=output_root,
            allow_incomplete=bool(request.allow_incomplete),
        )
        report["tables"].append(table_report)
        report["written_files"].extend(table_report.get("written_files", []))
        if not table_report.get("ok", False):
            report["ok"] = False

    report_path.write_text(json.dumps(report, indent=2) + "\n")
    return C81GenerationResult.from_mapping(report)


def _raise_response_error(
    response: Mapping[str, Any],
    *,
    error_type: type[KernelProtocolError] = KernelProtocolError,
) -> None:
    error = response.get("error", {})
    if not isinstance(error, Mapping):
        raise error_type("Kernel worker returned an unknown error.", response=response)
    code = str(error.get("code", "kernel_error"))
    message = str(error.get("message", "Kernel worker returned an error."))
    if code == "driver_not_found":
        raise KernelExecutableNotFound(message, code=code, response=response)
    raise error_type(message, code=code, response=response)


def _single_alpha_sequence(
    alpha_deg: float,
    *,
    options: SolveOptions,
    warm_start: bool | Sequence[float],
) -> list[float]:
    if isinstance(warm_start, bool):
        if warm_start and options.viscous and abs(alpha_deg) > 1.0e-9:
            return [0.0, float(alpha_deg)]
        return [float(alpha_deg)]

    sequence = list(_finite_float_sequence(warm_start, "warm_start"))
    if not sequence:
        raise ValueError("warm_start sequence must not be empty.")
    if not any(abs(value - alpha_deg) <= 1.0e-6 for value in sequence):
        raise ValueError("warm_start sequence must include the requested alpha.")
    return sequence


def _normalize_c81_table_spec(table: Mapping[str, Any]) -> dict[str, Any]:
    normalized = {
        key: _plain_data(value)
        for key, value in table.items()
        if key != "alpha_deg"
    }
    if "alpha_deg" in table and "alpha" not in normalized:
        normalized["alpha"] = _plain_data(table["alpha_deg"])
    return normalized


def _plain_data(value: Any) -> Any:
    if isinstance(value, SolveOptions):
        return value.to_dict()
    if isinstance(value, RetryPolicy):
        return value.to_dict()
    if isinstance(value, AirfoilSpec):
        return value.to_manifest_spec()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_data(item) for item in value]
    if isinstance(value, list):
        return [_plain_data(item) for item in value]
    return value


def _resolve_path(path: Any, base_dir: Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (base_dir / candidate).resolve()


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


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


def _finite_float_sequence(value: Any, field_name: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be a sequence of finite numbers.")
    return tuple(
        _finite_float(item, f"{field_name}[{index}]")
        for index, item in enumerate(value)
    )


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
