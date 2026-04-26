from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


KERNEL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KERNEL_ROOT / "tools"))

from xfoil_kernel import AlphaSequenceResult, C81GenerationResult, PointResult  # noqa: E402
from xfoil_kernel import cli as api_cli  # noqa: E402


class FakeClient:
    instances: list[FakeClient] = []

    def __init__(self, config) -> None:
        self.config = config
        self.registered = []
        self.sequence_calls = []
        self.point_calls = []
        self.closed = False
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed = True
        return False

    def status(self):
        return {
            "ok": True,
            "protocol_version": 1,
            "implementation": "fake",
            "mode": "session" if self.config.use_session else "one_shot",
            "session_active": False,
            "registered_airfoils": [],
            "capabilities": {
                "commands": ["status", "solve_alpha_sequence"],
                "solve_options": ["viscous", "reynolds_number"],
            },
        }

    def register_airfoil(self, airfoil_id, airfoil):
        self.registered.append((airfoil_id, airfoil))

    def solve_alpha_sequence(self, airfoil_id, *, alpha_deg, options, timeout_seconds=None):
        self.sequence_calls.append(
            {
                "airfoil_id": airfoil_id,
                "alpha_deg": list(alpha_deg),
                "options": options,
                "timeout_seconds": timeout_seconds,
            }
        )
        points = [
            {
                "index": index + 1,
                "alpha_deg": float(alpha),
                "cl": 0.1 * float(alpha),
                "cd": 0.01,
                "cm": -0.01 * float(alpha),
                "converged": True,
            }
            for index, alpha in enumerate(alpha_deg)
        ]
        return AlphaSequenceResult.from_response(
            {
                "ok": True,
                "airfoil_id": airfoil_id,
                "complete": True,
                "requested_alpha_deg": list(alpha_deg),
                "converged_alpha_deg": list(alpha_deg),
                "missing_alpha_deg": [],
                "points": points,
            }
        )

    def solve_alpha(self, airfoil_id, *, alpha_deg, options, warm_start=True, timeout_seconds=None):
        self.point_calls.append(
            {
                "airfoil_id": airfoil_id,
                "alpha_deg": alpha_deg,
                "options": options,
                "warm_start": warm_start,
                "timeout_seconds": timeout_seconds,
            }
        )
        return PointResult(
            alpha_deg=float(alpha_deg),
            cl=0.1 * float(alpha_deg),
            cd=0.01,
            cm=-0.01 * float(alpha_deg),
            converged=True,
        )


class IncompleteFakeClient(FakeClient):
    def solve_alpha_sequence(self, airfoil_id, *, alpha_deg, options, timeout_seconds=None):
        return AlphaSequenceResult.from_response(
            {
                "ok": True,
                "airfoil_id": airfoil_id,
                "complete": False,
                "requested_alpha_deg": list(alpha_deg),
                "converged_alpha_deg": [float(alpha_deg[0])],
                "missing_alpha_deg": [float(alpha_deg[-1])],
                "points": [
                    {
                        "index": 1,
                        "alpha_deg": float(alpha_deg[0]),
                        "cl": 0.0,
                        "cd": 0.01,
                        "cm": 0.0,
                        "converged": True,
                    }
                ],
            }
        )


def test_status_cli_prints_json(monkeypatch, capsys, tmp_path: Path) -> None:
    FakeClient.instances = []
    monkeypatch.setattr(api_cli, "XfoilKernelClient", FakeClient)

    code = api_cli.main(
        [
            "status",
            "--json",
            "--one-shot",
            "--runtime-root",
            str(tmp_path / "worker"),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["mode"] == "one_shot"
    assert FakeClient.instances[0].config.use_session is False
    assert FakeClient.instances[0].closed is True


def test_cli_prints_version(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        api_cli.main(["--version"])

    output = capsys.readouterr().out
    assert exc.value.code == 0
    assert "xfoil-kernel-api" in output


def test_solve_alpha_sequence_cli_uses_public_client(monkeypatch, capsys, tmp_path: Path) -> None:
    FakeClient.instances = []
    monkeypatch.setattr(api_cli, "XfoilKernelClient", FakeClient)

    code = api_cli.main(
        [
            "solve-alpha-sequence",
            "--naca",
            "0012",
            "--alpha",
            "-2",
            "0",
            "2",
            "--reynolds",
            "1000000",
            "--mach",
            "0.1",
            "--panel-count",
            "180",
            "--runtime-root",
            str(tmp_path / "worker"),
        ]
    )

    output = capsys.readouterr().out
    client = FakeClient.instances[0]
    assert code == 0
    assert "complete: True" in output
    assert client.registered[0][0] == "NACA0012"
    assert client.sequence_calls[0]["alpha_deg"] == [-2.0, 0.0, 2.0]
    assert client.sequence_calls[0]["options"].reynolds_number == 1_000_000.0
    assert client.sequence_calls[0]["options"].mach_number == 0.1
    assert client.sequence_calls[0]["options"].panel_count == 180


def test_solve_alpha_sequence_cli_returns_two_for_incomplete(monkeypatch, capsys) -> None:
    monkeypatch.setattr(api_cli, "XfoilKernelClient", IncompleteFakeClient)

    code = api_cli.main(
        [
            "solve-alpha-sequence",
            "--naca",
            "0012",
            "--alpha",
            "0",
            "4",
            "--reynolds",
            "1000000",
        ]
    )

    output = capsys.readouterr().out
    assert code == 2
    assert "complete: False" in output
    assert "missing_alpha_deg: 4" in output


def test_solve_alpha_cli_accepts_explicit_warm_start(monkeypatch, capsys) -> None:
    FakeClient.instances = []
    monkeypatch.setattr(api_cli, "XfoilKernelClient", FakeClient)

    code = api_cli.main(
        [
            "solve-alpha",
            "--naca",
            "0012",
            "--alpha",
            "4",
            "--warm-start-alpha",
            "0",
            "2",
            "4",
            "--reynolds",
            "1000000",
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "alpha_deg" in output
    assert FakeClient.instances[0].point_calls[0]["warm_start"] == [0.0, 2.0, 4.0]


def test_generate_c81_cli_uses_public_manifest_api(monkeypatch, capsys, tmp_path: Path) -> None:
    calls = []

    def fake_generate_c81_from_manifest(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return C81GenerationResult.from_mapping(
            {
                "ok": True,
                "report_file": str(tmp_path / "out" / "report.json"),
                "output_root": str(tmp_path / "out"),
                "allow_incomplete": True,
                "tables": [
                    {
                        "id": "demo",
                        "ok": True,
                        "complete": False,
                        "written_files": [str(tmp_path / "out" / "demo.c81")],
                    }
                ],
                "written_files": [str(tmp_path / "out" / "demo.c81")],
            }
        )

    monkeypatch.setattr(api_cli, "generate_c81_from_manifest", fake_generate_c81_from_manifest)
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("tables: []\n")

    code = api_cli.main(
        [
            "generate-c81",
            str(manifest),
            "--one-shot",
            "--allow-incomplete",
            "--runtime-root",
            str(tmp_path / "worker"),
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "demo: ok, incomplete" in output
    assert calls[0]["args"] == (manifest,)
    assert calls[0]["kwargs"]["use_session"] is False
    assert calls[0]["kwargs"]["allow_incomplete"] is True
    assert calls[0]["kwargs"]["runtime_root"] == tmp_path / "worker"


def test_generate_c81_cli_returns_one_for_incomplete_report(monkeypatch, capsys, tmp_path: Path) -> None:
    def fake_generate_c81_from_manifest(*args, **kwargs):
        return C81GenerationResult.from_mapping(
            {
                "ok": False,
                "report_file": str(tmp_path / "out" / "report.json"),
                "output_root": str(tmp_path / "out"),
                "allow_incomplete": False,
                "tables": [{"id": "demo", "ok": False, "complete": False}],
                "written_files": [],
            }
        )

    monkeypatch.setattr(api_cli, "generate_c81_from_manifest", fake_generate_c81_from_manifest)

    code = api_cli.main(["generate-c81", str(tmp_path / "manifest.yaml"), "--json"])

    output = json.loads(capsys.readouterr().out)
    assert code == 1
    assert output["ok"] is False
