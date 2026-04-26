from __future__ import annotations

import io
import json
import math
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest


KERNEL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KERNEL_ROOT / "tools"))

from xfoil_kernel_tools.baseline import (
    BaselineCase,
    build_input_deck,
    load_cases,
    main,
    parse_xfoil_polar,
    write_reference_baselines,
)
from xfoil_kernel_tools.driver import (
    build_nonconvergence_diagnostics,
    build_case_namelist,
    compare_to_reference,
    parse_kernel_driver_output,
    parse_kernel_failure_markers,
    parse_kernel_header,
    run_kernel_case,
)
from xfoil_kernel_tools.session import KernelSession
import xfoil_kernel_tools.worker as worker_module
from xfoil_kernel_tools.worker import XFoilKernelWorker


def test_parse_xfoil_polar_file_with_transition_metadata(tmp_path: Path) -> None:
    polar_path = tmp_path / "sample.polar"
    polar_path.write_text(
        "\n".join(
            [
                "       XFOIL         Version 6.93",
                "",
                " Calculated polar for: NACA 0012",
                "",
                " 1 1 Reynolds number fixed          Mach number fixed",
                "",
                " xtrf =   0.050 (top)        0.100 (bottom)  ",
                " Mach =   0.120     Re =     1.500 e 6     Ncrit =   9.000  8.000",
                "",
                "   alpha      CL        CD       CDp       CM   Top_Xtr  Bot_Xtr  Top_Itr  Bot_Itr",
                " ------- -------- --------- --------- -------- ------- ------- ------- -------",
                "   0.000   0.0123   0.00987   0.00321  -0.0012  0.0500  0.1000  12.0000  13.0000",
                "   2.000   0.2450   0.01050   0.00350  -0.0100  0.0520  0.1010  14.0000  15.0000",
            ]
        )
        + "\n"
    )

    polar = parse_xfoil_polar(polar_path)

    assert polar.airfoil_name == "NACA 0012"
    assert polar.mach_number == 0.12
    assert polar.reynolds_number == 1_500_000.0
    assert polar.ncrit_top == 9.0
    assert polar.ncrit_bottom == 8.0
    assert polar.forced_xtr_top == 0.05
    assert polar.forced_xtr_bottom == 0.1
    assert len(polar.points) == 2
    assert polar.points[0].alpha_deg == 0.0
    assert polar.points[1].cl == 0.245
    assert polar.points[0].values["xtr_top"] == 0.05
    assert polar.points[0].values["itr_bottom"] == 13.0


def test_build_input_deck_keeps_transition_inputs_first_class(tmp_path: Path) -> None:
    case = BaselineCase.from_mapping(
        {
            "id": "deck_test",
            "description": "",
            "airfoil": {"type": "naca", "code": "0012"},
            "options": {
                "viscous": True,
                "reynolds_number": 1_000_000.0,
                "mach_number": 0.12,
                "ncrit_top": 9.0,
                "ncrit_bottom": 8.0,
                "xtr_top": 0.05,
                "xtr_bottom": 0.10,
                "itmax": 45,
                "panel_count": 160,
            },
            "alpha_deg": [-2.0, 0.0, 2.0],
        }
    )

    deck = build_input_deck(case, polar_path=tmp_path / "deck_test.polar", kernel_root=KERNEL_ROOT)

    assert "NACA 0012" in deck
    assert "PLOP\nG\n\nOPER" in deck
    assert "ITER 45" in deck
    assert "MACH 0.12" in deck
    assert "VISC 1000000" in deck
    assert "NT 9" in deck
    assert "NB 8" in deck
    assert "XTR 0.05 0.1" in deck
    assert "ASEQ -2 2 2" in deck


def test_build_input_deck_prefers_common_ncrit_scalar(tmp_path: Path) -> None:
    case = BaselineCase.from_mapping(
        {
            "id": "deck_scalar_ncrit_test",
            "description": "",
            "airfoil": {"type": "naca", "code": "0012"},
            "options": {
                "viscous": True,
                "reynolds_number": 1_000_000.0,
                "ncrit": 7.5,
                "xtr_top": 1.0,
                "xtr_bottom": 1.0,
            },
            "alpha_deg": [0.0],
        }
    )

    deck = build_input_deck(case, polar_path=tmp_path / "deck_test.polar", kernel_root=KERNEL_ROOT)

    assert "N 7.5" in deck
    assert "NT 7.5" not in deck
    assert "NB 7.5" not in deck


def test_build_input_deck_disables_graphics_before_panel_changes(tmp_path: Path) -> None:
    case = BaselineCase.from_mapping(
        {
            "id": "deck_panel_test",
            "description": "",
            "airfoil": {"type": "naca", "code": "0012"},
            "options": {
                "viscous": False,
                "panel_count": 220,
            },
            "alpha_deg": [0.0],
        }
    )

    deck = build_input_deck(case, polar_path=tmp_path / "deck_panel_test.polar", kernel_root=KERNEL_ROOT)

    assert deck.index("PLOP\nG") < deck.index("PPAR\nN 220")


def test_build_input_deck_applies_coordinate_panel_count_before_paneling(tmp_path: Path) -> None:
    coordinate_file = tmp_path / "airfoil.dat"
    coordinate_file.write_text("demo\n1 0\n0 0\n1 0\n")
    case = BaselineCase.from_mapping(
        {
            "id": "deck_coordinate_panel_test",
            "description": "",
            "airfoil": {"type": "coordinates", "path": str(coordinate_file), "panel": True},
            "options": {
                "viscous": False,
                "panel_count": 180,
            },
            "alpha_deg": [0.0],
        }
    )

    deck = build_input_deck(case, polar_path=tmp_path / "deck_coordinate_panel_test.polar")

    assert f"LOAD {coordinate_file}" in deck
    assert deck.index("PLOP\nG") < deck.index("PPAR\nN 180")
    assert deck.index("PPAR\nN 180") < deck.index("PANE")


def test_cli_reports_missing_xfoil_without_traceback(tmp_path: Path, capsys) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        """
{
  "schema_version": 1,
  "cases": [
    {
      "id": "missing_xfoil",
      "airfoil": {"type": "naca", "code": "0012"},
      "options": {"viscous": false},
      "alpha_deg": [0.0]
    }
  ]
}
"""
    )

    return_code = main(
        [
            "--cases",
            str(cases_path),
            "--output-root",
            str(tmp_path / "out"),
            "--xfoil-executable",
            str(tmp_path / "does-not-exist"),
        ]
    )

    captured = capsys.readouterr()
    assert return_code == 2
    assert "XFOIL executable not found" in captured.out
    assert "Traceback" not in captured.out


def test_write_reference_baselines_omits_machine_paths(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        """
{
  "schema_version": 1,
  "cases": [
    {
      "id": "case_a",
      "description": "reference write test",
      "airfoil": {"type": "naca", "code": "0012"},
      "options": {"viscous": false},
      "alpha_deg": [0.0]
    }
  ]
}
"""
    )
    summary_dir = tmp_path / "out" / "case_a"
    summary_dir.mkdir(parents=True)
    (summary_dir / "summary.json").write_text(
        """
{
  "case_id": "case_a",
  "ok": true,
  "complete": true,
  "requested_alpha_deg": [0.0],
  "completed_alpha_deg": [0.0],
  "missing_alpha_deg": [],
  "polar": {
    "path": "/absolute/machine/path/case_a.polar",
    "airfoil_name": "NACA 0012",
    "columns": ["alpha_deg", "cl", "cd", "cm"],
    "points": [{"alpha_deg": 0.0, "cl": 0.0, "cd": 0.0, "cm": 0.0}]
  }
}
"""
    )

    written = write_reference_baselines(
        cases_path=cases_path,
        output_root=tmp_path / "out",
        reference_root=tmp_path / "reference",
    )

    reference_text = written[0].read_text()
    assert "/absolute/machine/path" not in reference_text
    assert '"case_id": "case_a"' in reference_text


def test_build_case_namelist_keeps_transition_inputs_first_class() -> None:
    case = BaselineCase.from_mapping(
        {
            "id": "driver_deck_test",
            "description": "",
            "airfoil": {"type": "naca", "code": "0012"},
            "options": {
                "viscous": True,
                "reynolds_number": 1_000_000.0,
                "mach_number": 0.12,
                "ncrit_top": 9.0,
                "ncrit_bottom": 8.0,
                "xtr_top": 0.05,
                "xtr_bottom": 0.10,
                "itmax": 45,
                "panel_count": 160,
            },
            "alpha_deg": [-2.0, 0.0, 2.0],
        }
    )

    namelist = build_case_namelist(case)

    assert "&xkcase" in namelist
    assert "airfoil_type = 'naca'" in namelist
    assert "naca_code = 12" in namelist
    assert "viscous = .true." in namelist
    assert "ncrit_top = 9" in namelist
    assert "ncrit_bottom = 8" in namelist
    assert "xtr_top = 0.05" in namelist
    assert "xtr_bottom = 0.1" in namelist
    assert "alpha_deg = -2, 0, 2" in namelist


def test_build_case_namelist_accepts_common_ncrit_scalar() -> None:
    case = BaselineCase.from_mapping(
        {
            "id": "driver_scalar_ncrit_test",
            "description": "",
            "airfoil": {"type": "naca", "code": "0012"},
            "options": {
                "viscous": True,
                "reynolds_number": 1_000_000.0,
                "ncrit": 7.5,
            },
            "alpha_deg": [0.0],
        }
    )

    namelist = build_case_namelist(case)

    assert "ncrit_top = 7.5" in namelist
    assert "ncrit_bottom = 7.5" in namelist


def test_parse_kernel_driver_output_ignores_xfoil_chatter() -> None:
    points = parse_kernel_driver_output(
        "\n".join(
            [
                " Buffer airfoil set using  257 points",
                "XK_POINT     1      -2.000000  -1.2340000000000000E-01   6.7000000000000002E-03  -1.4900000000000000E-02   7.6000000000000003E-04 T   3.10000000E-05   8.83800000E-01   1.80800000E-01 F T",
                "XK_END",
            ]
        )
    )

    assert len(points) == 1
    assert points[0].alpha_deg == -2.0
    assert points[0].converged is True
    assert points[0].transition_forced_top is False
    assert points[0].transition_forced_bottom is True


def test_parse_kernel_driver_output_accepts_legacy_fortran_exponent_format() -> None:
    points = parse_kernel_driver_output(
        "XK_POINT     1      -8.000000  -5.8281542066448799E-01   "
        "8.3824529799490000-205   1.2263315702756664E-01   "
        "1.2738793733668305E-01 F   1.41822857D+06   "
        "8.14302418E-01   1.68708980E-02 F F\n"
    )

    assert len(points) == 1
    assert points[0].cd == pytest.approx(8.382452979949e-205)
    assert points[0].rms_bl == pytest.approx(1.41822857e6)
    assert points[0].converged is False


def test_build_nonconvergence_diagnostics_for_unconverged_point() -> None:
    points = parse_kernel_driver_output(
        "XK_POINT     1      -8.000000  -5.8281542066448799E-01   "
        "8.3824529799490000-205   1.2263315702756664E-01   "
        "1.2738793733668305E-01 F   1.41822857D+06   "
        "8.14302418E-01   1.68708980E-02 F F\n"
    )

    diagnostics = build_nonconvergence_diagnostics(
        [-8.0],
        points,
        options={
            "viscous": True,
            "reynolds_number": 1_000_000.0,
            "mach_number": 0.2,
            "ncrit": 9.0,
            "xtr_top": 1.0,
            "xtr_bottom": 1.0,
            "itmax": 80,
            "panel_count": 180,
        },
        header={"viscous": True, "n_panels": 180},
        failure_markers=[{"code": "viscous_nonconvergence", "message": "VISCAL:  Convergence failed"}],
    )

    assert diagnostics == [
        {
            "index": 1,
            "requested_alpha_deg": -8.0,
            "alpha_deg": -8.0,
            "reason": "viscous_nonconvergence",
            "message": "Viscous boundary-layer solve did not report convergence for the requested alpha.",
            "rms_bl": pytest.approx(1.41822857e6),
            "cl": pytest.approx(-0.582815420664488),
            "cd": pytest.approx(8.382452979949e-205),
            "cm": pytest.approx(0.12263315702756664),
            "cdp": pytest.approx(0.12738793733668305),
            "actual_xtr_top": pytest.approx(0.814302418),
            "actual_xtr_bottom": pytest.approx(0.016870898),
            "transition_forced_top": False,
            "transition_forced_bottom": False,
            "viscous": True,
            "reynolds_number": pytest.approx(1_000_000.0),
            "mach_number": pytest.approx(0.2),
            "ncrit_top": pytest.approx(9.0),
            "ncrit_bottom": pytest.approx(9.0),
            "requested_xtr_top": pytest.approx(1.0),
            "requested_xtr_bottom": pytest.approx(1.0),
            "panel_count": 180,
            "itmax": 80,
            "viscal_iteration_limit": 85,
            "failure_markers": [
                {"code": "viscous_nonconvergence", "message": "VISCAL:  Convergence failed"}
            ],
        }
    ]


def test_build_nonconvergence_diagnostics_for_missing_result_row() -> None:
    diagnostics = build_nonconvergence_diagnostics(
        [0.0],
        [],
        options={"viscous": False, "mach_number": 0.0},
    )

    assert diagnostics == [
        {
            "index": 1,
            "requested_alpha_deg": 0.0,
            "reason": "no_result_row",
            "message": "Kernel did not emit a result row for the requested alpha.",
        }
    ]


def test_parse_kernel_failure_markers() -> None:
    markers = parse_kernel_failure_markers(
        "\n".join(
            [
                "Solving BL system ...",
                "VISCAL:  Convergence failed",
                "Paneling convergence failed.  Continuing anyway...",
            ]
        )
    )

    assert markers == [
        {"code": "viscous_nonconvergence", "message": "VISCAL:  Convergence failed"},
        {
            "code": "paneling_convergence_failed",
            "message": "Paneling convergence failed.  Continuing anyway...",
        },
    ]


def test_parse_kernel_header_with_session_reuse_diagnostics() -> None:
    header = parse_kernel_header(
        "XK_HEADER schema=1 version=  6.99 n_panels=  180"
        " viscous=T reynolds=  1.00000000E+06 mach=  2.00000000E-01"
        " ncrit_top=  9.00000000E+00 ncrit_bottom=  8.00000000E+00"
        " xtr_top=  5.00000000E-02 xtr_bottom=  1.00000000E+00"
        " geometry_changed=F options_changed=T\n"
    )

    assert header == {
        "schema": 1,
        "version": pytest.approx(6.99),
        "n_panels": 180,
        "viscous": True,
        "reynolds": pytest.approx(1_000_000.0),
        "mach": pytest.approx(0.2),
        "ncrit_top": pytest.approx(9.0),
        "ncrit_bottom": pytest.approx(8.0),
        "xtr_top": pytest.approx(0.05),
        "xtr_bottom": pytest.approx(1.0),
        "geometry_changed": False,
        "options_changed": True,
    }


def test_kernel_driver_matches_pristine_references_when_built(tmp_path: Path) -> None:
    driver_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_driver"
    if not driver_executable.exists():
        pytest.skip("direct-call kernel driver executable is not built")

    for case in load_cases(KERNEL_ROOT / "baselines" / "cases.json"):
        summary = run_kernel_case(
            case,
            driver_executable=driver_executable,
            run_root=tmp_path / "runs",
            kernel_root=KERNEL_ROOT,
            timeout_seconds=120.0,
        )
        reference_path = KERNEL_ROOT / "baselines" / "reference" / f"{case.id}.json"
        reference = json.loads(reference_path.read_text())
        differences = compare_to_reference(summary, reference)

        assert summary["missing_alpha_deg"] == reference["missing_alpha_deg"]
        assert len(differences) == len(reference["polar"]["points"])
        assert max(abs(row["d_cl"]) for row in differences) <= 7.0e-5
        assert max(abs(row["d_cd"]) for row in differences) <= 7.0e-6
        assert max(abs(row["d_cm"]) for row in differences) <= 7.0e-5
        xtr_differences = [
            max(abs(row["d_xtr_top"]), abs(row["d_xtr_bottom"]))
            for row in differences
            if "d_xtr_top" in row and "d_xtr_bottom" in row
        ]
        if xtr_differences:
            assert max(xtr_differences) <= 1.0e-4


def test_kernel_driver_binary_omits_non_kernel_symbols_when_built() -> None:
    driver_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_driver"
    if not driver_executable.exists():
        pytest.skip("direct-call kernel driver executable is not built")
    nm_executable = shutil.which("nm")
    if nm_executable is None:
        pytest.skip("nm is not available")

    completed = subprocess.run(
        [nm_executable, "-g", str(driver_executable)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    symbols = {_normalize_symbol(line.split()[-1]) for line in completed.stdout.splitlines() if line.split()}

    assert {"specal_", "viscal_", "abcopy_"}.issubset(symbols)
    forbidden = {
        "oper_",
        "gdes_",
        "mdes_",
        "qdes_",
        "plot_",
        "plopen_",
        "plinitialize_",
        "colorspectrumhues_",
        "askc_",
        "askr_",
        "mhinge_",
        "blplot_",
        "polplt_",
    }
    assert symbols.isdisjoint(forbidden)


def test_persistent_kernel_session_reuses_state_when_built(tmp_path: Path) -> None:
    session_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_session"
    if not session_executable.exists():
        pytest.skip("persistent kernel session executable is not built")

    case_a = BaselineCase.from_mapping(
        {
            "id": "session_a",
            "airfoil": {"type": "naca", "code": "0012"},
            "options": {"viscous": False, "mach_number": 0.0, "panel_count": 160},
            "alpha_deg": [0.0],
        }
    )
    case_b = BaselineCase.from_mapping(
        {
            "id": "session_b",
            "airfoil": {"type": "naca", "code": "0012"},
            "options": {"viscous": False, "mach_number": 0.0, "panel_count": 160},
            "alpha_deg": [2.0],
        }
    )
    case_panel_change = BaselineCase.from_mapping(
        {
            "id": "session_panel_change",
            "airfoil": {"type": "naca", "code": "0012"},
            "options": {"viscous": False, "mach_number": 0.0, "panel_count": 180},
            "alpha_deg": [2.0],
        }
    )
    case_options_change = BaselineCase.from_mapping(
        {
            "id": "session_options_change",
            "airfoil": {"type": "naca", "code": "0012"},
            "options": {"viscous": False, "mach_number": 0.2, "panel_count": 180},
            "alpha_deg": [2.0],
        }
    )
    case_reynolds_change = BaselineCase.from_mapping(
        {
            "id": "session_reynolds_change",
            "airfoil": {"type": "naca", "code": "0012"},
            "options": {
                "viscous": False,
                "reynolds_number": 1_000_000.0,
                "mach_number": 0.2,
                "panel_count": 180,
            },
            "alpha_deg": [2.0],
        }
    )
    case_transition_change = BaselineCase.from_mapping(
        {
            "id": "session_transition_change",
            "airfoil": {"type": "naca", "code": "0012"},
            "options": {
                "viscous": False,
                "reynolds_number": 1_000_000.0,
                "mach_number": 0.2,
                "ncrit": 8.0,
                "xtr_top": 0.8,
                "xtr_bottom": 0.7,
                "panel_count": 180,
            },
            "alpha_deg": [2.0],
        }
    )
    case_airfoil_change = BaselineCase.from_mapping(
        {
            "id": "session_airfoil_change",
            "airfoil": {"type": "naca", "code": "2412"},
            "options": {
                "viscous": False,
                "reynolds_number": 1_000_000.0,
                "mach_number": 0.2,
                "ncrit": 8.0,
                "xtr_top": 0.8,
                "xtr_bottom": 0.7,
                "panel_count": 180,
            },
            "alpha_deg": [2.0],
        }
    )
    case_after_reset = BaselineCase.from_mapping(
        {
            "id": "session_after_reset",
            "airfoil": {"type": "naca", "code": "2412"},
            "options": {
                "viscous": False,
                "reynolds_number": 1_000_000.0,
                "mach_number": 0.2,
                "ncrit": 8.0,
                "xtr_top": 0.8,
                "xtr_bottom": 0.7,
                "panel_count": 180,
            },
            "alpha_deg": [4.0],
        }
    )

    with KernelSession(session_executable=session_executable, runtime_root=tmp_path / "session") as session:
        assert session.ping() == "XK_OK ping"
        first = session.solve_case(case_a, run_root=tmp_path / "runs", kernel_root=KERNEL_ROOT)
        assert session.reset_boundary_layer_state() == "XK_OK reset_boundary_layer_state"
        second = session.solve_case(case_b, run_root=tmp_path / "runs", kernel_root=KERNEL_ROOT)
        panel_change = session.solve_case(
            case_panel_change,
            run_root=tmp_path / "runs",
            kernel_root=KERNEL_ROOT,
        )
        options_change = session.solve_case(
            case_options_change,
            run_root=tmp_path / "runs",
            kernel_root=KERNEL_ROOT,
        )
        reynolds_change = session.solve_case(
            case_reynolds_change,
            run_root=tmp_path / "runs",
            kernel_root=KERNEL_ROOT,
        )
        transition_change = session.solve_case(
            case_transition_change,
            run_root=tmp_path / "runs",
            kernel_root=KERNEL_ROOT,
        )
        airfoil_change = session.solve_case(
            case_airfoil_change,
            run_root=tmp_path / "runs",
            kernel_root=KERNEL_ROOT,
        )
        assert session.reset_boundary_layer_state() == "XK_OK reset_boundary_layer_state"
        after_reset = session.solve_case(
            case_after_reset,
            run_root=tmp_path / "runs",
            kernel_root=KERNEL_ROOT,
        )

    assert first["ok"] is True
    assert second["ok"] is True
    assert panel_change["ok"] is True
    assert options_change["ok"] is True
    assert reynolds_change["ok"] is True
    assert transition_change["ok"] is True
    assert airfoil_change["ok"] is True
    assert after_reset["ok"] is True

    assert first["diagnostics"]["geometry_changed"] is True
    assert first["diagnostics"]["options_changed"] is True
    assert second["diagnostics"]["geometry_changed"] is False
    assert second["diagnostics"]["options_changed"] is False
    assert panel_change["diagnostics"]["geometry_changed"] is True
    assert panel_change["diagnostics"]["options_changed"] is True
    assert options_change["diagnostics"]["geometry_changed"] is False
    assert options_change["diagnostics"]["options_changed"] is True
    assert options_change["diagnostics"]["mach"] == pytest.approx(0.2)
    assert reynolds_change["diagnostics"]["geometry_changed"] is False
    assert reynolds_change["diagnostics"]["options_changed"] is True
    assert reynolds_change["diagnostics"]["reynolds"] == pytest.approx(1_000_000.0)
    assert transition_change["diagnostics"]["geometry_changed"] is False
    assert transition_change["diagnostics"]["options_changed"] is True
    assert transition_change["diagnostics"]["ncrit_top"] == pytest.approx(8.0)
    assert transition_change["diagnostics"]["xtr_top"] == pytest.approx(0.8)
    assert airfoil_change["diagnostics"]["geometry_changed"] is True
    assert airfoil_change["diagnostics"]["options_changed"] is True
    assert after_reset["diagnostics"]["geometry_changed"] is False
    assert after_reset["diagnostics"]["options_changed"] is False


def test_persistent_kernel_session_rebuilds_coordinate_geometry_when_built(tmp_path: Path) -> None:
    session_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_session"
    if not session_executable.exists():
        pytest.skip("persistent kernel session executable is not built")

    coordinate_a = KERNEL_ROOT / "data" / "airfoils" / "stress" / "naca2412.dat"
    coordinate_b = KERNEL_ROOT / "data" / "airfoils" / "stress" / "naca0024.dat"
    case_coord_a = BaselineCase.from_mapping(
        {
            "id": "session_coord_a",
            "airfoil": {"type": "coordinates", "path": str(coordinate_a), "panel": True},
            "options": {"viscous": False, "mach_number": 0.0, "panel_count": 160},
            "alpha_deg": [0.0],
        }
    )
    case_coord_a_compatible = BaselineCase.from_mapping(
        {
            "id": "session_coord_a_compatible",
            "airfoil": {"type": "coordinates", "path": str(coordinate_a), "panel": True},
            "options": {"viscous": False, "mach_number": 0.0, "panel_count": 160},
            "alpha_deg": [2.0],
        }
    )
    case_coord_b = BaselineCase.from_mapping(
        {
            "id": "session_coord_b",
            "airfoil": {"type": "coordinates", "path": str(coordinate_b), "panel": True},
            "options": {"viscous": False, "mach_number": 0.0, "panel_count": 160},
            "alpha_deg": [0.0],
        }
    )

    with KernelSession(session_executable=session_executable, runtime_root=tmp_path / "session") as session:
        first = session.solve_case(case_coord_a, run_root=tmp_path / "runs", kernel_root=KERNEL_ROOT)
        compatible = session.solve_case(
            case_coord_a_compatible,
            run_root=tmp_path / "runs",
            kernel_root=KERNEL_ROOT,
        )
        geometry_change = session.solve_case(case_coord_b, run_root=tmp_path / "runs", kernel_root=KERNEL_ROOT)

    assert first["ok"] is True
    assert compatible["ok"] is True
    assert geometry_change["ok"] is True
    assert first["diagnostics"]["geometry_changed"] is True
    assert first["diagnostics"]["options_changed"] is True
    assert compatible["diagnostics"]["geometry_changed"] is False
    assert compatible["diagnostics"]["options_changed"] is False
    assert geometry_change["diagnostics"]["geometry_changed"] is True
    assert geometry_change["diagnostics"]["options_changed"] is True


def test_worker_registers_naca_and_solves_with_runner(monkeypatch, tmp_path: Path) -> None:
    captured_cases: list[BaselineCase] = []

    def fake_run_kernel_case(case: BaselineCase, **kwargs):
        captured_cases.append(case)
        return {
            "ok": True,
            "complete": True,
            "requested_alpha_deg": list(case.alpha_deg),
            "converged_alpha_deg": list(case.alpha_deg),
            "missing_alpha_deg": [],
            "diagnostics": {"geometry_changed": True, "options_changed": True},
            "input_file": str(tmp_path / "input.nml"),
            "transcript_file": str(tmp_path / "transcript.txt"),
            "points": [
                {
                    "index": 1,
                    "alpha_deg": case.alpha_deg[0],
                    "cl": 0.1,
                    "cd": 0.01,
                    "cm": -0.02,
                    "cdp": 0.001,
                    "converged": True,
                    "rms_bl": 1.0e-5,
                    "xtr_top": 0.5,
                    "xtr_bottom": 0.6,
                    "transition_forced_top": False,
                    "transition_forced_bottom": False,
                }
            ],
        }

    monkeypatch.setattr(worker_module, "run_kernel_case", fake_run_kernel_case)
    worker = XFoilKernelWorker(
        use_session=False,
        runtime_root=tmp_path / "worker",
        kernel_root=KERNEL_ROOT,
    )

    register_response = worker.handle(
        {"request_id": "r1", "cmd": "register_airfoil", "airfoil_id": "naca0012", "naca": "0012"}
    )
    solve_response = worker.handle(
        {
            "request_id": "r2",
            "cmd": "solve_alpha_sequence",
            "airfoil_id": "naca0012",
            "options": {"viscous": False, "mach_number": 0.0},
            "alpha_deg": [0.0],
        }
    )

    assert register_response == {"ok": True, "request_id": "r1", "airfoil_id": "naca0012"}
    assert solve_response["ok"] is True
    assert solve_response["complete"] is True
    assert solve_response["diagnostics"] == {"geometry_changed": True, "options_changed": True}
    assert solve_response["points"][0]["cl"] == 0.1
    assert captured_cases[0].airfoil == {"type": "naca", "code": "0012"}
    assert captured_cases[0].options == {"viscous": False, "mach_number": 0.0}


@pytest.mark.parametrize(
    ("request_patch", "message"),
    [
        ({"alpha_deg": [math.nan]}, "alpha_deg[0]"),
        ({"alpha_deg": "0"}, "alpha_deg must be a JSON array"),
        ({"options": {"viscous": True}}, "reynolds_number"),
        ({"options": {"viscous": True, "reynolds_number": 0.0}}, "reynolds_number"),
        ({"options": {"viscous": False, "itmax": 0}}, "itmax"),
        ({"options": {"viscous": False, "panel_count": 1}}, "panel_count"),
        ({"options": {"viscous": False, "xtr_top": -0.1}}, "xtr_top"),
        ({"options": {"viscous": False, "xtr_bottom": 1.1}}, "xtr_bottom"),
        ({"options": {"viscous": False, "mach_number": math.inf}}, "mach_number"),
        ({"options": {"viscous": False, "surprise": 1}}, "Unsupported solve option"),
    ],
)
def test_worker_rejects_invalid_solve_requests_before_runner(
    monkeypatch,
    tmp_path: Path,
    request_patch: dict,
    message: str,
) -> None:
    def fake_run_kernel_case(*args, **kwargs):
        raise AssertionError("runner should not be called for invalid worker requests")

    monkeypatch.setattr(worker_module, "run_kernel_case", fake_run_kernel_case)
    worker = XFoilKernelWorker(
        use_session=False,
        runtime_root=tmp_path / "worker",
        kernel_root=KERNEL_ROOT,
    )
    worker.handle({"cmd": "register_airfoil", "airfoil_id": "naca0012", "naca": "0012"})
    request = {
        "request_id": "solve1",
        "cmd": "solve_alpha_sequence",
        "airfoil_id": "naca0012",
        "options": {"viscous": False},
        "alpha_deg": [0.0],
    }
    request.update(request_patch)

    response = worker.handle(request)

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"
    assert message in response["error"]["message"]


def test_worker_registers_coordinate_arrays_to_dat_file(tmp_path: Path) -> None:
    worker = XFoilKernelWorker(runtime_root=tmp_path / "worker", kernel_root=KERNEL_ROOT)

    response = worker.handle(
        {
            "request_id": "r1",
            "cmd": "register_airfoil",
            "airfoil_id": "toy foil",
            "coordinates": {
                "x": [1.0, 0.5, 0.0, 0.5, 1.0],
                "y": [0.0, 0.05, 0.0, -0.05, 0.0],
            },
        }
    )

    geometry_path = Path(response["geometry_path"])
    assert response["ok"] is True
    assert geometry_path.exists()
    assert geometry_path.read_text().splitlines()[0] == "toy foil"
    assert worker.registry["toy foil"].airfoil["type"] == "coordinates"


def test_worker_json_lines_loop_handles_ping_and_shutdown(tmp_path: Path) -> None:
    worker = XFoilKernelWorker(runtime_root=tmp_path / "worker", kernel_root=KERNEL_ROOT)
    input_stream = io.StringIO(
        "\n".join(
            [
                '{"request_id":"r1","cmd":"ping"}',
                '{"request_id":"r2","cmd":"shutdown"}',
            ]
        )
        + "\n"
    )
    output_stream = io.StringIO()

    worker.serve(input_stream, output_stream)

    responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]
    assert responses == [
        {"ok": True, "request_id": "r1"},
        {"ok": True, "request_id": "r2"},
    ]


def test_worker_script_speaks_json_lines_protocol_when_built(tmp_path: Path) -> None:
    session_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_session"
    if not session_executable.exists():
        pytest.skip("persistent kernel session executable is not built")

    script = KERNEL_ROOT / "scripts" / "xfoil_worker.py"
    process = _JsonLineWorkerProcess(
        [
            sys.executable,
            str(script),
            "--session-executable",
            str(session_executable),
            "--runtime-root",
            str(tmp_path / "worker-process"),
        ]
    )
    try:
        status = process.request({"request_id": "s1", "cmd": "status"})
        registered = process.request(
            {
                "request_id": "r1",
                "cmd": "register_airfoil",
                "airfoil_id": "naca0012",
                "naca": "0012",
            }
        )
        solved = process.request(
            {
                "request_id": "a1",
                "cmd": "solve_alpha_sequence",
                "airfoil_id": "naca0012",
                "options": {"viscous": False, "mach_number": 0.0, "panel_count": 160},
                "alpha_deg": [0.0, 2.0],
                "timeout_seconds": 120.0,
            },
            timeout_seconds=120.0,
        )
        reset = process.request({"request_id": "b1", "cmd": "reset_boundary_layer_state"})
        shutdown = process.request({"request_id": "q1", "cmd": "shutdown"})
        returncode = process.wait(timeout_seconds=10.0)
    finally:
        process.close()

    assert status["ok"] is True
    assert status["request_id"] == "s1"
    assert status["mode"] == "session"
    assert status["session_active"] is False
    assert registered == {"ok": True, "request_id": "r1", "airfoil_id": "naca0012"}
    assert solved["ok"] is True
    assert solved["request_id"] == "a1"
    assert solved["complete"] is True
    assert solved["diagnostics"]["geometry_changed"] is True
    assert solved["diagnostics"]["options_changed"] is True
    assert reset == {
        "ok": True,
        "request_id": "b1",
        "mode": "session",
        "reset_performed": True,
        "message": "XK_OK reset_boundary_layer_state",
    }
    assert shutdown == {"ok": True, "request_id": "q1"}
    assert returncode == 0


def test_worker_status_reports_protocol_and_capabilities(tmp_path: Path) -> None:
    worker = XFoilKernelWorker(runtime_root=tmp_path / "worker", kernel_root=KERNEL_ROOT)
    worker.handle({"cmd": "register_airfoil", "airfoil_id": "naca0012", "naca": "0012"})

    response = worker.handle({"request_id": "status1", "cmd": "status"})

    assert response["ok"] is True
    assert response["request_id"] == "status1"
    assert response["protocol_version"] == 1
    assert response["implementation"] == "python-json-lines"
    assert response["mode"] == "session"
    assert response["session_active"] is False
    assert response["registered_airfoils"] == ["naca0012"]
    assert "solve_alpha_sequence" in response["capabilities"]["commands"]
    assert "reset_boundary_layer_state" in response["capabilities"]["commands"]
    assert response["capabilities"]["sequence_types"] == ["alpha"]
    assert response["capabilities"]["cl_sequence"] is False


def test_worker_reset_boundary_layer_state_is_noop_in_one_shot_mode(tmp_path: Path) -> None:
    worker = XFoilKernelWorker(
        use_session=False,
        runtime_root=tmp_path / "worker",
        kernel_root=KERNEL_ROOT,
    )

    response = worker.handle({"request_id": "reset1", "cmd": "reset_boundary_layer_state"})

    assert response == {
        "ok": True,
        "request_id": "reset1",
        "mode": "one_shot",
        "reset_performed": False,
        "reason": "one_shot_mode_has_no_persistent_boundary_layer_state",
    }


def test_worker_reset_boundary_layer_state_uses_active_session(tmp_path: Path) -> None:
    class FakeSession:
        timeout_seconds = None
        closed = False

        def reset_boundary_layer_state(self, *, timeout_seconds):
            self.timeout_seconds = timeout_seconds
            return "XK_OK reset_boundary_layer_state"

        def close(self, *, kill=False):
            self.closed = True

    fake_session = FakeSession()
    worker = XFoilKernelWorker(runtime_root=tmp_path / "worker", kernel_root=KERNEL_ROOT)
    worker._session = fake_session

    response = worker.handle(
        {
            "request_id": "reset1",
            "cmd": "reset_boundary_layer_state",
            "timeout_seconds": 3.0,
        }
    )

    assert response == {
        "ok": True,
        "request_id": "reset1",
        "mode": "session",
        "reset_performed": True,
        "message": "XK_OK reset_boundary_layer_state",
    }
    assert fake_session.timeout_seconds == 3.0


def test_worker_reset_boundary_layer_state_reports_no_active_session(tmp_path: Path) -> None:
    worker = XFoilKernelWorker(runtime_root=tmp_path / "worker", kernel_root=KERNEL_ROOT)

    response = worker.handle({"request_id": "reset1", "cmd": "reset_boundary_layer_state"})

    assert response == {
        "ok": True,
        "request_id": "reset1",
        "mode": "session",
        "reset_performed": False,
        "reason": "no_active_session",
    }


def test_worker_returns_structured_timeout_error(monkeypatch, tmp_path: Path) -> None:
    def fake_run_kernel_case(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["xfoil_kernel_driver"], timeout=0.01)

    monkeypatch.setattr(worker_module, "run_kernel_case", fake_run_kernel_case)
    worker = XFoilKernelWorker(
        use_session=False,
        runtime_root=tmp_path / "worker",
        kernel_root=KERNEL_ROOT,
    )
    worker.handle({"cmd": "register_airfoil", "airfoil_id": "naca0012", "naca": "0012"})

    response = worker.handle(
        {
            "request_id": "solve1",
            "cmd": "solve_alpha_sequence",
            "airfoil_id": "naca0012",
            "options": {"viscous": False},
            "alpha_deg": [0.0],
        }
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "driver_timeout"
    assert response["error"]["mode"] == "one_shot"
    assert response["error"]["case_id"].startswith("naca0012_")


def test_worker_returns_structured_parse_error(monkeypatch, tmp_path: Path) -> None:
    def fake_run_kernel_case(*args, **kwargs):
        raise ValueError("Unexpected XK_POINT row")

    monkeypatch.setattr(worker_module, "run_kernel_case", fake_run_kernel_case)
    worker = XFoilKernelWorker(
        use_session=False,
        runtime_root=tmp_path / "worker",
        kernel_root=KERNEL_ROOT,
    )
    worker.handle({"cmd": "register_airfoil", "airfoil_id": "naca0012", "naca": "0012"})

    response = worker.handle(
        {
            "request_id": "solve1",
            "cmd": "solve_alpha_sequence",
            "airfoil_id": "naca0012",
            "options": {"viscous": False},
            "alpha_deg": [0.0],
        }
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "driver_output_parse_failed"
    assert response["error"]["mode"] == "one_shot"
    assert "Unexpected XK_POINT" in response["error"]["message"]


def test_worker_solves_with_real_driver_when_built(tmp_path: Path) -> None:
    driver_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_driver"
    if not driver_executable.exists():
        pytest.skip("direct-call kernel driver executable is not built")

    worker = XFoilKernelWorker(
        driver_executable=driver_executable,
        use_session=False,
        runtime_root=tmp_path / "worker",
        kernel_root=KERNEL_ROOT,
    )
    worker.handle({"cmd": "register_airfoil", "airfoil_id": "naca0012", "naca": "0012"})
    response = worker.handle(
        {
            "request_id": "solve1",
            "cmd": "solve_alpha_sequence",
            "airfoil_id": "naca0012",
            "options": {"viscous": False, "mach_number": 0.0, "panel_count": 160},
            "alpha_deg": [-2.0, 0.0, 2.0],
        }
    )

    assert response["ok"] is True
    assert response["complete"] is True
    assert response["missing_alpha_deg"] == []
    assert len(response["points"]) == 3


def test_worker_solves_with_persistent_session_when_built(tmp_path: Path) -> None:
    session_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_session"
    if not session_executable.exists():
        pytest.skip("persistent kernel session executable is not built")

    worker = XFoilKernelWorker(
        session_executable=session_executable,
        use_session=True,
        runtime_root=tmp_path / "worker",
        kernel_root=KERNEL_ROOT,
    )
    try:
        worker.handle({"cmd": "register_airfoil", "airfoil_id": "naca0012", "naca": "0012"})
        worker.handle({"cmd": "register_airfoil", "airfoil_id": "naca2412", "naca": "2412"})
        first = worker.handle(
            {
                "request_id": "solve1",
                "cmd": "solve_alpha_sequence",
                "airfoil_id": "naca0012",
                "options": {"viscous": False, "mach_number": 0.0, "panel_count": 160},
                "alpha_deg": [-2.0, 0.0, 2.0],
            }
        )
        airfoil_change = worker.handle(
            {
                "request_id": "solve2",
                "cmd": "solve_alpha_sequence",
                "airfoil_id": "naca2412",
                "options": {"viscous": False, "mach_number": 0.0, "panel_count": 160},
                "alpha_deg": [0.0],
            }
        )
        compatible = worker.handle(
            {
                "request_id": "solve3",
                "cmd": "solve_alpha_sequence",
                "airfoil_id": "naca2412",
                "options": {"viscous": False, "mach_number": 0.0, "panel_count": 160},
                "alpha_deg": [2.0],
            }
        )
    finally:
        worker.close()

    assert first["ok"] is True
    assert first["complete"] is True
    assert first["missing_alpha_deg"] == []
    assert len(first["points"]) == 3
    assert first["diagnostics"]["geometry_changed"] is True
    assert airfoil_change["ok"] is True
    assert airfoil_change["diagnostics"]["geometry_changed"] is True
    assert airfoil_change["diagnostics"]["options_changed"] is True
    assert compatible["ok"] is True
    assert compatible["diagnostics"]["geometry_changed"] is False
    assert compatible["diagnostics"]["options_changed"] is False


def test_persistent_worker_options_affect_viscous_solve_when_built(tmp_path: Path) -> None:
    session_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_session"
    if not session_executable.exists():
        pytest.skip("persistent kernel session executable is not built")

    worker = XFoilKernelWorker(
        session_executable=session_executable,
        use_session=True,
        runtime_root=tmp_path / "worker",
        kernel_root=KERNEL_ROOT,
    )
    try:
        worker.handle({"cmd": "register_airfoil", "airfoil_id": "naca0012", "naca": "0012"})
        base_options = {
            "viscous": True,
            "reynolds_number": 1_000_000.0,
            "mach_number": 0.0,
            "itmax": 120,
            "panel_count": 180,
        }
        ncrit9 = _solve_single_worker_point(
            worker,
            options={**base_options, "ncrit": 9.0, "xtr_top": 1.0, "xtr_bottom": 1.0},
        )
        ncrit3 = _solve_single_worker_point(
            worker,
            options={**base_options, "ncrit": 3.0, "xtr_top": 1.0, "xtr_bottom": 1.0},
        )
        forced = _solve_single_worker_point(
            worker,
            options={**base_options, "ncrit": 9.0, "xtr_top": 0.05, "xtr_bottom": 0.05},
        )
        inviscid = _solve_single_worker_point(
            worker,
            options={"viscous": False, "mach_number": 0.0, "panel_count": 180, "xtr_top": 0.2, "xtr_bottom": 0.3},
        )
        invalid = worker.handle(
            {
                "cmd": "solve_alpha_sequence",
                "airfoil_id": "naca0012",
                "options": {**base_options, "itmax": 0},
                "alpha_deg": [4.0],
            }
        )
    finally:
        worker.close()

    assert ncrit3["xtr_top"] < ncrit9["xtr_top"]
    assert ncrit3["cd"] != pytest.approx(ncrit9["cd"])
    assert forced["xtr_top"] == pytest.approx(0.05)
    assert forced["xtr_bottom"] == pytest.approx(0.05)
    assert forced["transition_forced_top"] is True
    assert forced["transition_forced_bottom"] is True
    assert inviscid["converged"] is True
    assert inviscid["cd"] == pytest.approx(0.0)
    assert inviscid["xtr_top"] == pytest.approx(0.2)
    assert inviscid["xtr_bottom"] == pytest.approx(0.3)
    assert invalid["ok"] is False
    assert invalid["error"]["code"] == "invalid_request"
    assert "itmax must be positive" in invalid["error"]["message"]


class _JsonLineWorkerProcess:
    def __init__(self, args: list[str]) -> None:
        self.process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if self.process.stdin is None or self.process.stdout is None:
            raise AssertionError("Could not open worker process pipes.")
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

    def request(self, payload: dict, *, timeout_seconds: float = 20.0) -> dict:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        line = self._readline(timeout_seconds)
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"Worker emitted a non-JSON protocol line: {line!r}") from exc
        if not isinstance(response, dict):
            raise AssertionError(f"Worker response must be a JSON object: {response!r}")
        return response

    def wait(self, *, timeout_seconds: float) -> int:
        return self.process.wait(timeout=timeout_seconds)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5.0)

    def _readline(self, timeout_seconds: float) -> str:
        try:
            line = self._lines.get(timeout=timeout_seconds)
        except queue.Empty as exc:
            raise AssertionError("Timed out waiting for worker protocol output.") from exc
        if line is None:
            raise AssertionError(f"Worker exited with return code {self.process.poll()}.")
        return line

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._lines.put(line.rstrip("\n"))
        self._lines.put(None)


def _solve_single_worker_point(worker: XFoilKernelWorker, *, options: dict) -> dict:
    response = worker.handle(
        {
            "cmd": "solve_alpha_sequence",
            "airfoil_id": "naca0012",
            "options": options,
            "alpha_deg": [4.0],
            "timeout_seconds": 120.0,
        }
    )
    assert response["ok"] is True
    assert response["complete"] is True
    assert len(response["points"]) == 1
    return response["points"][0]


def _normalize_symbol(symbol: str) -> str:
    return symbol.lower().lstrip("_")
