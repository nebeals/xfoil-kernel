from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import py_compile
import shutil
import sys


KERNEL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KERNEL_ROOT / "tools"))

from xfoil_kernel_tools import c81_generator  # noqa: E402


class ExampleFakeWorker:
    instances: list[ExampleFakeWorker] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.closed = False
        self.__class__.instances.append(self)

    def handle(self, request):
        if request["cmd"] == "register_airfoil":
            return {"ok": True, "airfoil_id": request["airfoil_id"]}
        if request["cmd"] == "solve_alpha_sequence":
            alpha_values = [float(alpha) for alpha in request["alpha_deg"]]
            points = [
                {
                    "index": index + 1,
                    "alpha_deg": alpha,
                    "cl": 0.1 * alpha,
                    "cd": 0.01,
                    "cm": -0.01 * alpha,
                    "converged": True,
                }
                for index, alpha in enumerate(alpha_values)
            ]
            return {
                "ok": True,
                "complete": True,
                "requested_alpha_deg": alpha_values,
                "converged_alpha_deg": alpha_values,
                "missing_alpha_deg": [],
                "points": points,
                "diagnostics": {},
                "nonconvergence_diagnostics": [],
                "failure_markers": [],
                "artifacts": {},
            }
        raise AssertionError(f"Unexpected command {request['cmd']!r}.")

    def close(self) -> None:
        self.closed = True


def test_example_python_files_compile() -> None:
    for path in (KERNEL_ROOT / "examples").glob("*.py"):
        py_compile.compile(path, doraise=True)


def test_solve_alpha_sequence_example_uses_public_api(monkeypatch, capsys, tmp_path: Path) -> None:
    module = _load_example_module("solve_alpha_sequence")

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def register_airfoil(self, airfoil_id, airfoil):
            self.airfoil_id = airfoil_id
            self.airfoil = airfoil

        def solve_alpha_sequence(self, airfoil_id, *, alpha_deg, options):
            from xfoil_kernel import AlphaSequenceResult

            assert airfoil_id == self.airfoil_id
            assert options.reynolds_number == 500_000.0
            return AlphaSequenceResult.from_response(
                {
                    "ok": True,
                    "airfoil_id": airfoil_id,
                    "complete": True,
                    "requested_alpha_deg": list(alpha_deg),
                    "converged_alpha_deg": list(alpha_deg),
                    "missing_alpha_deg": [],
                    "points": [
                        {
                            "index": 1,
                            "alpha_deg": 0.0,
                            "cl": 0.0,
                            "cd": 0.01,
                            "cm": 0.0,
                            "converged": True,
                        }
                    ],
                }
            )

    monkeypatch.setattr(module, "XfoilKernelClient", FakeClient)

    code = module.main(
        [
            "--naca",
            "0012",
            "--alpha",
            "0",
            "--reynolds",
            "500000",
            "--runtime-root",
            str(tmp_path / "worker"),
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "complete: True" in output
    assert "alpha_deg" in output


def test_bundled_c81_manifest_runs_with_fake_worker(monkeypatch, tmp_path: Path) -> None:
    ExampleFakeWorker.instances = []
    monkeypatch.setattr(c81_generator, "XFoilKernelWorker", ExampleFakeWorker)
    monkeypatch.setattr(c81_generator, "_write_c81_collection", _fake_write_c81_collection)

    examples_dir = tmp_path / "examples"
    examples_dir.mkdir()
    manifest_path = examples_dir / "c81_naca0012.yaml"
    shutil.copyfile(KERNEL_ROOT / "examples" / "c81_naca0012.yaml", manifest_path)

    report = c81_generator.generate_c81_from_manifest(manifest_path)

    assert report["ok"] is True
    assert report["written_files"]
    assert Path(report["report_file"]).is_file()
    saved_report = json.loads(Path(report["report_file"]).read_text())
    assert saved_report["tables"][0]["airfoil"] == "NACA0012"
    assert ExampleFakeWorker.instances[-1].kwargs["use_session"] is True
    assert ExampleFakeWorker.instances[-1].closed is True


def _load_example_module(name: str):
    path = KERNEL_ROOT / "examples" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_write_c81_collection(*, airfoil_id, collection, output_dir, header_format):
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for reynolds in collection:
        path = output_dir / f"{airfoil_id}_Re_{int(reynolds)}.c81"
        path.write_text(f"{airfoil_id} {header_format}\n")
        written.append(str(path))
    return written
