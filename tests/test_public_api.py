from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest


KERNEL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KERNEL_ROOT / "tools"))

import xfoil_kernel
import xfoil_kernel.api as api_module
from xfoil_kernel import (
    AirfoilSpec,
    C81GenerationRequest,
    IncompleteSolveError,
    KernelConfig,
    RetryPolicy,
    SolveOptions,
    XfoilKernelClient,
    generate_c81,
    generate_c81_from_manifest,
)


class FakeWorker:
    instances: list[FakeWorker] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.requests = []
        self.closed = False
        FakeWorker.instances.append(self)

    def handle(self, request):
        self.requests.append(request)
        cmd = request["cmd"]
        if cmd == "register_airfoil":
            return {
                "ok": True,
                "airfoil_id": request["airfoil_id"],
            }
        if cmd == "reset_boundary_layer_state":
            return {
                "ok": True,
                "mode": "session",
                "reset_performed": True,
                "message": "XK_OK reset_boundary_layer_state",
            }
        if cmd == "status":
            return {
                "ok": True,
                "protocol_version": 1,
                "implementation": "fake",
                "mode": "session",
                "session_active": False,
                "registered_airfoils": [],
                "capabilities": {
                    "commands": ["status", "solve_alpha_sequence"],
                    "solve_options": ["viscous", "reynolds_number"],
                },
            }
        if cmd == "solve_alpha_sequence":
            alpha_values = [float(value) for value in request["alpha_deg"]]
            missing = self._missing_alphas(alpha_values)
            points = [
                self._point(alpha, index=index + 1)
                for index, alpha in enumerate(alpha_values)
            ]
            for point in points:
                if point["alpha_deg"] in missing:
                    point["converged"] = False
                    point["rms_bl"] = 0.02
            return {
                "ok": True,
                "airfoil_id": request["airfoil_id"],
                "complete": not missing,
                "requested_alpha_deg": alpha_values,
                "converged_alpha_deg": [
                    point["alpha_deg"]
                    for point in points
                    if point["converged"]
                ],
                "missing_alpha_deg": missing,
                "points": points,
                "diagnostics": {"geometry_changed": False, "options_changed": False},
                "nonconvergence_diagnostics": [
                    {
                        "index": point["index"],
                        "requested_alpha_deg": point["alpha_deg"],
                        "alpha_deg": point["alpha_deg"],
                        "reason": "viscous_nonconvergence",
                        "message": "Viscous boundary-layer solve did not report convergence for the requested alpha.",
                        "rms_bl": point["rms_bl"],
                    }
                    for point in points
                    if not point["converged"]
                ],
                "failure_markers": [],
                "artifacts": {"case_id": "fake_case"},
            }
        raise AssertionError(f"Unexpected worker command {cmd!r}.")

    def close(self):
        self.closed = True

    def _missing_alphas(self, alpha_values):
        return []

    @staticmethod
    def _point(alpha, *, index):
        return {
            "index": index,
            "alpha_deg": float(alpha),
            "cl": 0.1 * float(alpha),
            "cd": 0.01 + 0.001 * abs(float(alpha)),
            "cm": -0.02 * float(alpha),
            "cdp": 0.002,
            "converged": True,
            "rms_bl": 1.0e-5,
            "xtr_top": 0.8,
            "xtr_bottom": 0.7,
            "transition_forced_top": False,
            "transition_forced_bottom": False,
        }


class MissingAlphaFakeWorker(FakeWorker):
    def _missing_alphas(self, alpha_values):
        return [4.0] if 4.0 in alpha_values else []


def test_public_import_surface_exposes_documented_names() -> None:
    assert xfoil_kernel.AirfoilSpec is AirfoilSpec
    assert xfoil_kernel.XfoilKernelClient is XfoilKernelClient
    assert xfoil_kernel.__version__ == api_module.__version__
    assert hasattr(xfoil_kernel, "generate_c81")
    assert set(xfoil_kernel.__all__) == {
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
    }
    assert xfoil_kernel.__all__ == api_module.__all__


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: AirfoilSpec.naca(""), "non-empty"),
        (lambda: AirfoilSpec.naca("12A"), "digits"),
        (lambda: AirfoilSpec.naca("0000"), "positive"),
        (
            lambda: AirfoilSpec.coordinates(x=[1.0, 0.0], y=[0.0, 0.0]),
            "At least three",
        ),
        (
            lambda: AirfoilSpec.coordinates(x=[1.0, math.nan, 0.0], y=[0.0, 0.0, 0.0]),
            "x\\[1\\]",
        ),
        (
            lambda: AirfoilSpec.coordinates(x=[1.0, 0.5, 0.0], y=[0.0, 0.0]),
            "same length",
        ),
        (
            lambda: AirfoilSpec.coordinates_file("airfoil.dat", panel="yes"),
            "panel",
        ),
    ],
)
def test_airfoil_spec_validates_public_contract(factory, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (SolveOptions(viscous="yes"), "viscous"),
        (SolveOptions(reynolds_number=0.0), "reynolds_number"),
        (SolveOptions(mach_number=-0.1), "mach_number"),
        (SolveOptions(mach_number=math.inf), "mach_number"),
        (SolveOptions(ncrit=0.0), "ncrit"),
        (SolveOptions(ncrit_top=math.nan), "ncrit_top"),
        (SolveOptions(xtr_top=-0.1), "xtr_top"),
        (SolveOptions(xtr_bottom=1.1), "xtr_bottom"),
        (SolveOptions(itmax=0), "itmax"),
        (SolveOptions(panel_count=1), "panel_count"),
        (SolveOptions(panel_count=1.5), "panel_count"),
    ],
)
def test_solve_options_validates_worker_contract(options: SolveOptions, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        options.to_worker_options()


def test_solve_options_can_omit_reynolds_for_c81_grid() -> None:
    assert SolveOptions(viscous=True, reynolds_number=None).to_worker_options()["viscous"] is True


def test_client_registers_airfoil_and_solves_alpha_sequence(monkeypatch, tmp_path: Path) -> None:
    FakeWorker.instances = []
    monkeypatch.setattr(api_module, "XFoilKernelWorker", FakeWorker)

    config = KernelConfig(runtime_root=tmp_path / "api", use_session=False)
    with XfoilKernelClient(config) as client:
        client.register_airfoil("naca0012", AirfoilSpec.naca("0012"))
        result = client.solve_alpha_sequence(
            "naca0012",
            alpha_deg=[-2.0, 0.0, 2.0],
            options=SolveOptions(viscous=True, reynolds_number=1_000_000.0),
        )

    worker = FakeWorker.instances[0]
    assert worker.closed is True
    assert worker.kwargs["use_session"] is False
    assert worker.requests[0]["airfoil"] == {"type": "naca", "code": "0012"}
    assert worker.requests[1]["options"]["reynolds_number"] == 1_000_000.0
    assert result.complete is True
    assert result.diagnostics == {"geometry_changed": False, "options_changed": False}
    assert result.nonconvergence_diagnostics == ()
    assert result.failure_markers == ()
    assert result.point_at(2.0).cl == pytest.approx(0.2)
    assert result.to_dict()["diagnostics"] == {"geometry_changed": False, "options_changed": False}
    assert result.to_dict()["nonconvergence_diagnostics"] == []
    assert result.to_dict()["artifacts"]["case_id"] == "fake_case"


def test_solve_alpha_uses_warm_start_sequence_by_default(monkeypatch, tmp_path: Path) -> None:
    FakeWorker.instances = []
    monkeypatch.setattr(api_module, "XFoilKernelWorker", FakeWorker)

    client = XfoilKernelClient(KernelConfig(runtime_root=tmp_path / "api", use_session=False))
    try:
        client.register_airfoil("naca0012", AirfoilSpec.naca("0012"))
        point = client.solve_alpha(
            "naca0012",
            alpha_deg=4.0,
            options=SolveOptions(viscous=True, reynolds_number=1_000_000.0),
        )
    finally:
        client.close()

    assert point.alpha_deg == 4.0
    assert FakeWorker.instances[0].requests[-1]["alpha_deg"] == [0.0, 4.0]


def test_incomplete_solve_stays_in_result_until_required(monkeypatch, tmp_path: Path) -> None:
    FakeWorker.instances = []
    monkeypatch.setattr(api_module, "XFoilKernelWorker", MissingAlphaFakeWorker)

    client = XfoilKernelClient(KernelConfig(runtime_root=tmp_path / "api", use_session=False))
    try:
        client.register_airfoil("naca0012", AirfoilSpec.naca("0012"))
        result = client.solve_alpha_sequence(
            "naca0012",
            alpha_deg=[0.0, 4.0],
            options=SolveOptions(viscous=True, reynolds_number=1_000_000.0),
        )
    finally:
        client.close()

    assert result.complete is False
    assert result.missing_alpha_deg == (4.0,)
    assert result.nonconvergence_diagnostics == (
        {
            "index": 2,
            "requested_alpha_deg": 4.0,
            "alpha_deg": 4.0,
            "reason": "viscous_nonconvergence",
            "message": "Viscous boundary-layer solve did not report convergence for the requested alpha.",
            "rms_bl": 0.02,
        },
    )
    with pytest.raises(IncompleteSolveError):
        result.require_complete()
    with pytest.raises(IncompleteSolveError):
        result.point_at(4.0)


def test_online_viscous_solve_requires_reynolds_number(monkeypatch, tmp_path: Path) -> None:
    FakeWorker.instances = []
    monkeypatch.setattr(api_module, "XFoilKernelWorker", FakeWorker)

    client = XfoilKernelClient(KernelConfig(runtime_root=tmp_path / "api", use_session=False))
    try:
        client.register_airfoil("naca0012", AirfoilSpec.naca("0012"))
        with pytest.raises(ValueError, match="reynolds_number"):
            client.solve_alpha_sequence(
                "naca0012",
                alpha_deg=[0.0],
                options=SolveOptions(viscous=True),
            )
    finally:
        client.close()

    solve_requests = [
        request
        for request in FakeWorker.instances[0].requests
        if request["cmd"] == "solve_alpha_sequence"
    ]
    assert solve_requests == []


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (
            lambda client: client.register_airfoil("", AirfoilSpec.naca("0012")),
            "airfoil_id",
        ),
        (
            lambda client: client.solve_alpha_sequence(
                "naca0012",
                alpha_deg=[0.0, math.nan],
                options=SolveOptions(viscous=False),
            ),
            "alpha_deg\\[1\\]",
        ),
        (
            lambda client: client.solve_alpha_sequence(
                "naca0012",
                alpha_deg=[0.0],
                options=SolveOptions(viscous=False),
                timeout_seconds=0.0,
            ),
            "timeout_seconds",
        ),
        (
            lambda client: client.solve_alpha(
                "naca0012",
                alpha_deg=math.inf,
                options=SolveOptions(viscous=False),
            ),
            "alpha_deg",
        ),
        (
            lambda client: client.solve_alpha(
                "naca0012",
                alpha_deg=2.0,
                options=SolveOptions(viscous=False),
                warm_start=[0.0, math.nan, 2.0],
            ),
            "warm_start\\[1\\]",
        ),
        (
            lambda client: client.reset_boundary_layer_state(timeout_seconds=-1.0),
            "timeout_seconds",
        ),
    ],
)
def test_client_validates_public_contract_before_worker(
    monkeypatch,
    tmp_path: Path,
    call,
    message: str,
) -> None:
    FakeWorker.instances = []
    monkeypatch.setattr(api_module, "XFoilKernelWorker", FakeWorker)

    client = XfoilKernelClient(KernelConfig(runtime_root=tmp_path / "api", use_session=False))
    try:
        client.register_airfoil("naca0012", AirfoilSpec.naca("0012"))
        initial_request_count = len(FakeWorker.instances[0].requests)
        with pytest.raises(ValueError, match=message):
            call(client)
    finally:
        client.close()

    assert len(FakeWorker.instances[0].requests) == initial_request_count


def test_kernel_config_validates_timeout_and_mode() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        XfoilKernelClient(KernelConfig(timeout_seconds=math.nan))
    with pytest.raises(ValueError, match="use_session"):
        XfoilKernelClient(KernelConfig(use_session="yes"))


def test_client_can_reset_boundary_layer_state(monkeypatch, tmp_path: Path) -> None:
    FakeWorker.instances = []
    monkeypatch.setattr(api_module, "XFoilKernelWorker", FakeWorker)

    client = XfoilKernelClient(KernelConfig(runtime_root=tmp_path / "api"))
    try:
        response = client.reset_boundary_layer_state(timeout_seconds=2.0)
    finally:
        client.close()

    assert response["reset_performed"] is True
    assert FakeWorker.instances[0].requests[0] == {
        "cmd": "reset_boundary_layer_state",
        "timeout_seconds": 2.0,
    }


def test_client_can_query_status(monkeypatch, tmp_path: Path) -> None:
    FakeWorker.instances = []
    monkeypatch.setattr(api_module, "XFoilKernelWorker", FakeWorker)

    client = XfoilKernelClient(KernelConfig(runtime_root=tmp_path / "api"))
    try:
        status = client.status()
    finally:
        client.close()

    assert status["protocol_version"] == 1
    assert status["capabilities"]["commands"] == ["status", "solve_alpha_sequence"]
    assert FakeWorker.instances[0].requests[0] == {"cmd": "status"}


def test_manifest_c81_wrapper_returns_typed_result(monkeypatch, tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"

    def fake_generate_c81_from_manifest(*args, **kwargs):
        return {
            "ok": True,
            "report_file": str(report_path),
            "output_root": str(tmp_path / "out"),
            "allow_incomplete": False,
            "tables": [],
            "written_files": [str(tmp_path / "out" / "NACA0012.c81")],
        }

    monkeypatch.setattr(
        api_module.c81_generator,
        "generate_c81_from_manifest",
        fake_generate_c81_from_manifest,
    )

    result = generate_c81_from_manifest(tmp_path / "manifest.yaml", use_session=True)

    assert result.ok is True
    assert result.report_file == report_path
    assert result.written_files == (tmp_path / "out" / "NACA0012.c81",)


def test_typed_c81_generation_uses_existing_client(monkeypatch, tmp_path: Path) -> None:
    FakeWorker.instances = []
    monkeypatch.setattr(api_module, "XFoilKernelWorker", FakeWorker)
    written = []

    def fake_write_c81_collection(*, airfoil_id, collection, output_dir, header_format):
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{airfoil_id}_Re_1000000.c81"
        path.write_text("fake c81\n")
        written.append(
            {
                "airfoil_id": airfoil_id,
                "collection": collection,
                "output_dir": output_dir,
                "header_format": header_format,
            }
        )
        return [str(path)]

    monkeypatch.setattr(
        api_module.c81_generator,
        "_write_c81_collection",
        fake_write_c81_collection,
    )

    request = C81GenerationRequest(
        output_root=tmp_path / "out",
        report_file=tmp_path / "out" / "report.json",
        airfoils={"NACA0012": AirfoilSpec.naca("0012")},
        tables=[
            {
                "id": "demo",
                "airfoil": "NACA0012",
                "c81_airfoil_id": "NACA0012",
                "output_dir": "tables",
                "reynolds": [1_000_000.0],
                "mach": [0.0],
                "alpha_deg": [-2.0, 0.0, 2.0],
                "options": SolveOptions(
                    viscous=True,
                    panel_count=180,
                ),
                "retry": RetryPolicy(
                    enabled=False,
                    initial_sequence="as_requested",
                    refinement_factors=(),
                ),
            }
        ],
    )

    with XfoilKernelClient(KernelConfig(runtime_root=tmp_path / "api", use_session=False)) as client:
        result = generate_c81(client, request)

    assert result.ok is True
    assert result.report_file == tmp_path / "out" / "report.json"
    assert result.written_files == (tmp_path / "out" / "tables" / "NACA0012_Re_1000000.c81",)
    assert written[0]["airfoil_id"] == "NACA0012"
    assert written[0]["collection"][1_000_000.0]["cl"][0.0]["alpha"] == [-2.0, 0.0, 2.0]
    solve_request = [
        request
        for request in FakeWorker.instances[0].requests
        if request["cmd"] == "solve_alpha_sequence"
    ][0]
    assert solve_request["options"]["reynolds_number"] == 1_000_000.0
