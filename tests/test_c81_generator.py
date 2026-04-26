from __future__ import annotations

from pathlib import Path
import json
import textwrap

import pytest

from xfoil_kernel_tools import c81_generator


class FakeWorker:
    missing_alpha_by_mach: dict[float, set[float]] = {}
    instances: list[FakeWorker] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.registered: set[str] = set()
        self.solve_count = 0
        self.__class__.instances.append(self)

    def handle(self, request):
        cmd = request["cmd"]
        if cmd == "register_airfoil":
            self.registered.add(request["airfoil_id"])
            return {"ok": True, "airfoil_id": request["airfoil_id"]}
        if cmd == "solve_alpha_sequence":
            solve_count = self.solve_count
            self.solve_count += 1
            alpha_values = [float(value) for value in request["alpha_deg"]]
            options = request.get("options", {})
            mach = float(options["mach_number"])
            reynolds = float(options["reynolds_number"])
            points = []
            for index, alpha in enumerate(alpha_values):
                converged = self._point_converges(
                    alpha,
                    alpha_values=alpha_values,
                    index=index,
                    mach=mach,
                    reynolds=reynolds,
                )
                points.append(
                    {
                        "index": index + 1,
                        "alpha_deg": alpha,
                        "cl": 0.1 * alpha + mach,
                        "cd": 0.01 + reynolds * 1.0e-9 + mach * 0.001,
                        "cm": -0.01 * alpha,
                        "converged": converged,
                    }
                )
            missing = [
                point["alpha_deg"]
                for point in points
                if not point["converged"]
            ]
            nonconvergence_diagnostics = [
                {
                    "index": point["index"],
                    "requested_alpha_deg": point["alpha_deg"],
                    "alpha_deg": point["alpha_deg"],
                    "reason": "viscous_nonconvergence",
                    "message": "Viscous boundary-layer solve did not report convergence for the requested alpha.",
                    "rms_bl": 0.01,
                    "reynolds_number": reynolds,
                    "mach_number": mach,
                }
                for point in points
                if not point["converged"]
            ]
            return {
                "ok": True,
                "complete": not missing,
                "requested_alpha_deg": alpha_values,
                "converged_alpha_deg": [
                    point["alpha_deg"] for point in points if point["converged"]
                ],
                "missing_alpha_deg": missing,
                "points": points,
                "diagnostics": {
                    "geometry_changed": solve_count == 0,
                    "options_changed": solve_count == 0,
                },
                "nonconvergence_diagnostics": nonconvergence_diagnostics,
                "failure_markers": [],
                "artifacts": {"case_id": "fake"},
            }
        raise AssertionError(f"Unexpected command {cmd!r}")

    def _point_converges(self, alpha, *, alpha_values, index, mach, reynolds) -> bool:
        return alpha not in self.missing_alpha_by_mach.get(mach, set())


class IncompleteFakeWorker(FakeWorker):
    missing_alpha_by_mach = {0.2: {0.0}}


class RequiresAboveFakeWorker(FakeWorker):
    def _point_converges(self, alpha, *, alpha_values, index, mach, reynolds) -> bool:
        if mach == 0.2 and alpha == 0.0:
            return index > 0 and alpha_values[index - 1] > alpha
        return super()._point_converges(
            alpha,
            alpha_values=alpha_values,
            index=index,
            mach=mach,
            reynolds=reynolds,
        )


class RequiresSmallStepFakeWorker(FakeWorker):
    def _point_converges(self, alpha, *, alpha_values, index, mach, reynolds) -> bool:
        if mach == 0.2 and alpha == 0.0:
            return index > 0 and abs(alpha_values[index - 1] - alpha) <= 1.0
        return super()._point_converges(
            alpha,
            alpha_values=alpha_values,
            index=index,
            mach=mach,
            reynolds=reynolds,
        )


def test_generate_c81_manifest_writes_report_and_collection(monkeypatch, tmp_path: Path) -> None:
    FakeWorker.instances = []
    written = _patch_c81_writer(monkeypatch)
    monkeypatch.setattr(c81_generator, "XFoilKernelWorker", FakeWorker)
    manifest_path = _write_manifest(tmp_path)

    report = c81_generator.generate_c81_from_manifest(manifest_path)

    assert report["ok"] is True
    assert report["tables"][0]["complete"] is True
    assert len(report["written_files"]) == 1
    assert Path(report["report_file"]).exists()
    saved_report = json.loads(Path(report["report_file"]).read_text())
    assert saved_report["ok"] is True
    assert FakeWorker.instances[-1].kwargs["use_session"] is True
    first_attempt = saved_report["tables"][0]["reynolds"][0]["machs"][0]["attempts"][0]
    assert first_attempt["diagnostics"] == {
        "geometry_changed": True,
        "options_changed": True,
    }

    write_call = written[0]
    assert write_call["airfoil_id"] == "TESTFOIL"
    assert write_call["header_format"] == "commas"
    assert write_call["output_dir"] == tmp_path / "out" / "main"
    reynolds_data = write_call["collection"][1_000_000.0]
    assert reynolds_data["cl"][0.2]["alpha"] == [-2.0, 0.0, 2.0]
    assert reynolds_data["cl"][0.2]["cl"] == pytest.approx([0.0, 0.2, 0.4])


def test_generate_c81_manifest_can_use_one_shot_worker(monkeypatch, tmp_path: Path) -> None:
    FakeWorker.instances = []
    _patch_c81_writer(monkeypatch)
    monkeypatch.setattr(c81_generator, "XFoilKernelWorker", FakeWorker)
    manifest_path = _write_manifest(tmp_path)

    report = c81_generator.generate_c81_from_manifest(
        manifest_path,
        use_session=False,
    )

    assert report["ok"] is True
    assert FakeWorker.instances[-1].kwargs["use_session"] is False


def test_generate_c81_manifest_fails_strict_when_points_are_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    written = _patch_c81_writer(monkeypatch)
    monkeypatch.setattr(c81_generator, "XFoilKernelWorker", IncompleteFakeWorker)
    manifest_path = _write_manifest(tmp_path)

    report = c81_generator.generate_c81_from_manifest(manifest_path)

    assert report["ok"] is False
    assert report["tables"][0]["complete"] is False
    assert report["tables"][0]["written_files"] == []
    assert written == []
    mach_reports = report["tables"][0]["reynolds"][0]["machs"]
    assert mach_reports[1]["missing_alpha_deg"] == [0.0]
    initial_attempt = mach_reports[1]["attempts"][0]
    assert initial_attempt["nonconvergence_diagnostics"][0]["reason"] == "viscous_nonconvergence"
    assert initial_attempt["nonconvergence_diagnostics"][0]["requested_alpha_deg"] == 0.0


def test_generate_c81_manifest_retries_missing_point_from_above(
    monkeypatch,
    tmp_path: Path,
) -> None:
    written = _patch_c81_writer(monkeypatch)
    monkeypatch.setattr(c81_generator, "XFoilKernelWorker", RequiresAboveFakeWorker)
    manifest_path = _write_manifest(tmp_path)

    report = c81_generator.generate_c81_from_manifest(manifest_path)

    assert report["ok"] is True
    mach_report = report["tables"][0]["reynolds"][0]["machs"][1]
    assert mach_report["missing_alpha_deg"] == []
    assert [attempt["label"] for attempt in mach_report["attempts"]] == ["initial"]
    assert mach_report["attempts"][0]["alpha_deg"] == [0.0, 2.0, 0.0, -2.0]
    reynolds_data = written[0]["collection"][1_000_000.0]
    assert reynolds_data["cl"][0.2]["alpha"] == [-2.0, 0.0, 2.0]


def test_generate_c81_manifest_retries_missing_point_with_smaller_step(
    monkeypatch,
    tmp_path: Path,
) -> None:
    written = _patch_c81_writer(monkeypatch)
    monkeypatch.setattr(c81_generator, "XFoilKernelWorker", RequiresSmallStepFakeWorker)
    manifest_path = _write_manifest(tmp_path)

    report = c81_generator.generate_c81_from_manifest(manifest_path)

    assert report["ok"] is True
    mach_report = report["tables"][0]["reynolds"][0]["machs"][1]
    assert mach_report["missing_alpha_deg"] == []
    assert [attempt["label"] for attempt in mach_report["attempts"]] == [
        "initial",
        "refine_below_factor_0.5",
    ]
    refined_attempt = mach_report["attempts"][-1]
    assert refined_attempt["alpha_deg"] == [-2.0, -1.0, 0.0]
    reynolds_data = written[0]["collection"][1_000_000.0]
    assert reynolds_data["cl"][0.2]["alpha"] == [-2.0, 0.0, 2.0]


def test_generate_c81_manifest_can_warm_start_from_non_target_alpha(
    monkeypatch,
    tmp_path: Path,
) -> None:
    written = _patch_c81_writer(monkeypatch)
    monkeypatch.setattr(c81_generator, "XFoilKernelWorker", FakeWorker)
    manifest_path = _write_manifest(tmp_path, alpha_spec="[-2.0, 2.0]")

    report = c81_generator.generate_c81_from_manifest(manifest_path)

    assert report["ok"] is True
    mach_report = report["tables"][0]["reynolds"][0]["machs"][1]
    assert mach_report["attempts"][0]["alpha_deg"] == [0.0, 2.0, 0.0, -2.0]
    reynolds_data = written[0]["collection"][1_000_000.0]
    assert reynolds_data["cl"][0.2]["alpha"] == [-2.0, 2.0]


def test_generate_c81_manifest_allows_incomplete_common_alpha_points(
    monkeypatch,
    tmp_path: Path,
) -> None:
    written = _patch_c81_writer(monkeypatch)
    monkeypatch.setattr(c81_generator, "XFoilKernelWorker", IncompleteFakeWorker)
    manifest_path = _write_manifest(tmp_path)

    report = c81_generator.generate_c81_from_manifest(
        manifest_path,
        allow_incomplete=True,
    )

    assert report["ok"] is True
    assert report["tables"][0]["complete"] is False
    reynolds_data = written[0]["collection"][1_000_000.0]
    assert reynolds_data["cl"][0.0]["alpha"] == [-2.0, 2.0]
    assert reynolds_data["cl"][0.2]["alpha"] == [-2.0, 2.0]


def _patch_c81_writer(monkeypatch):
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

    monkeypatch.setattr(c81_generator, "_write_c81_collection", fake_write_c81_collection)
    return written


def _write_manifest(
    directory: Path,
    *,
    alpha_spec: str = "{start: -2.0, end: 2.0, step: 2.0}",
) -> Path:
    path = directory / "manifest.yaml"
    path.write_text(
        textwrap.dedent(
            f"""
            output_root: out
            report: out/report.json
            airfoils:
              TESTFOIL:
                naca: "0012"
            defaults:
              reynolds: [1000000]
              mach: [0.0, 0.2]
              alpha: {alpha_spec}
              options:
                viscous: true
                ncrit: 9.0
                xtr_top: 1.0
                xtr_bottom: 1.0
            tables:
              - id: test_table
                airfoil: TESTFOIL
                output_dir: main
            """
        ).strip()
        + "\n"
    )
    return path
