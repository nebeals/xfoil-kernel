from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import pytest


KERNEL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KERNEL_ROOT / "tools"))

from xfoil_kernel_tools import build as build_tools  # noqa: E402
from xfoil_kernel_tools.baseline import BaselineCase  # noqa: E402
from xfoil_kernel_tools.driver import run_kernel_case  # noqa: E402
from xfoil_kernel_tools.session import KernelSession  # noqa: E402


def test_refresh_extracted_sources_is_deterministic(tmp_path: Path) -> None:
    refreshed_root = tmp_path / "kernel"

    written = build_tools.refresh_extracted_kernel_sources(kernel_source_root=refreshed_root)

    expected_names = set(build_tools.EXTRACTED_KERNEL_SOURCE_NAMES)
    expected_names.update(build_tools.EXTRACTED_KERNEL_INCLUDE_NAMES)
    assert {path.name for path in written} == expected_names

    for source_name in build_tools.EXTRACTED_KERNEL_SOURCE_NAMES:
        tracked = build_tools.KERNEL_SOURCE_ROOT / source_name
        refreshed = refreshed_root / source_name
        assert refreshed.read_text(errors="replace") == tracked.read_text(errors="replace")

    for include_name in build_tools.EXTRACTED_KERNEL_INCLUDE_NAMES:
        tracked = build_tools.KERNEL_SOURCE_ROOT / include_name
        refreshed = refreshed_root / include_name
        assert refreshed.read_text(errors="replace") == tracked.read_text(errors="replace")


@pytest.mark.parametrize(
    "case",
    [
        BaselineCase.from_mapping(
            {
                "id": "equiv_naca0012_inviscid",
                "airfoil": {"type": "naca", "code": "0012"},
                "options": {"viscous": False, "mach_number": 0.0, "panel_count": 160},
                "alpha_deg": [-2.0, 0.0, 2.0],
            }
        ),
        BaselineCase.from_mapping(
            {
                "id": "equiv_naca2412_viscous",
                "airfoil": {"type": "naca", "code": "2412"},
                "options": {
                    "viscous": True,
                    "reynolds_number": 1_000_000.0,
                    "mach_number": 0.0,
                    "ncrit": 9.0,
                    "xtr_top": 1.0,
                    "xtr_bottom": 1.0,
                    "panel_count": 160,
                    "itmax": 80,
                },
                "alpha_deg": [0.0, 2.0, 4.0],
            }
        ),
    ],
)
def test_one_shot_and_persistent_session_are_numerically_equivalent_when_built(
    tmp_path: Path,
    case: BaselineCase,
) -> None:
    driver_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_driver"
    session_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_session"
    if not driver_executable.exists() or not session_executable.exists():
        pytest.skip("kernel driver/session executables are not built")

    one_shot = run_kernel_case(
        case,
        driver_executable=driver_executable,
        run_root=tmp_path / "one-shot",
        kernel_root=KERNEL_ROOT,
        timeout_seconds=120.0,
    )
    with KernelSession(session_executable=session_executable, runtime_root=tmp_path / "session-process") as session:
        persistent = session.solve_case(
            case,
            run_root=tmp_path / "persistent",
            kernel_root=KERNEL_ROOT,
            timeout_seconds=120.0,
        )

    _assert_equivalent_kernel_summaries(one_shot, persistent)
    assert persistent["diagnostics"]["geometry_changed"] is True
    assert persistent["diagnostics"]["options_changed"] is True


def test_session_option_changes_match_one_shot_driver_when_built(tmp_path: Path) -> None:
    driver_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_driver"
    session_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_session"
    if not driver_executable.exists() or not session_executable.exists():
        pytest.skip("kernel driver/session executables are not built")

    option_cases = [
        (
            "option_forced_transition",
            {
                "viscous": True,
                "reynolds_number": 1_000_000.0,
                "mach_number": 0.0,
                "ncrit": 9.0,
                "xtr_top": 0.1,
                "xtr_bottom": 0.1,
                "panel_count": 160,
                "itmax": 80,
            },
        ),
        (
            "option_asymmetric_transition",
            {
                "viscous": True,
                "reynolds_number": 1_000_000.0,
                "mach_number": 0.0,
                "ncrit": 9.0,
                "xtr_top": 0.05,
                "xtr_bottom": 0.8,
                "panel_count": 160,
                "itmax": 80,
            },
        ),
        (
            "option_low_ncrit",
            {
                "viscous": True,
                "reynolds_number": 1_000_000.0,
                "mach_number": 0.0,
                "ncrit": 3.0,
                "xtr_top": 1.0,
                "xtr_bottom": 1.0,
                "panel_count": 160,
                "itmax": 80,
            },
        ),
        (
            "option_lower_reynolds",
            {
                "viscous": True,
                "reynolds_number": 500_000.0,
                "mach_number": 0.0,
                "ncrit": 9.0,
                "xtr_top": 1.0,
                "xtr_bottom": 1.0,
                "panel_count": 160,
                "itmax": 80,
            },
        ),
        (
            "option_nonzero_mach",
            {
                "viscous": True,
                "reynolds_number": 1_000_000.0,
                "mach_number": 0.2,
                "ncrit": 9.0,
                "xtr_top": 1.0,
                "xtr_bottom": 1.0,
                "panel_count": 160,
                "itmax": 80,
            },
        ),
        (
            "option_panel_count",
            {
                "viscous": True,
                "reynolds_number": 1_000_000.0,
                "mach_number": 0.0,
                "ncrit": 9.0,
                "xtr_top": 1.0,
                "xtr_bottom": 1.0,
                "panel_count": 220,
                "itmax": 80,
            },
        ),
    ]
    cases = [
        BaselineCase.from_mapping(
            {
                "id": case_id,
                "airfoil": {"type": "naca", "code": "0012"},
                "options": options,
                "alpha_deg": [4.0],
            }
        )
        for case_id, options in option_cases
    ]

    with KernelSession(session_executable=session_executable, runtime_root=tmp_path / "option-session-process") as session:
        for case in cases:
            one_shot = run_kernel_case(
                case,
                driver_executable=driver_executable,
                run_root=tmp_path / "option-one-shot",
                kernel_root=KERNEL_ROOT,
                timeout_seconds=120.0,
            )
            persistent = session.solve_case(
                case,
                run_root=tmp_path / "option-session",
                kernel_root=KERNEL_ROOT,
                timeout_seconds=120.0,
            )

            assert persistent["diagnostics"]["options_changed"] is True
            _assert_equivalent_kernel_summaries(
                one_shot,
                persistent,
                coefficient_tolerance=2.0e-6,
                transition_tolerance=1.0e-5,
            )


def test_stress_coordinate_airfoils_panel_and_solve_inviscid_when_built(tmp_path: Path) -> None:
    driver_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_driver"
    if not driver_executable.exists():
        pytest.skip("direct-call kernel driver executable is not built")

    stress_files = sorted((KERNEL_ROOT / "data" / "airfoils" / "stress").glob("*.dat"))
    assert stress_files
    for airfoil_file in stress_files:
        case = BaselineCase.from_mapping(
            {
                "id": f"stress_{airfoil_file.stem.replace('-', '_')}",
                "airfoil": {
                    "type": "coordinates",
                    "path": str(airfoil_file.relative_to(KERNEL_ROOT)),
                    "panel": True,
                },
                "options": {"viscous": False, "mach_number": 0.0, "panel_count": 160},
                "alpha_deg": [0.0],
            }
        )

        summary = run_kernel_case(
            case,
            driver_executable=driver_executable,
            run_root=tmp_path / "stress-runs",
            kernel_root=KERNEL_ROOT,
            timeout_seconds=120.0,
        )

        assert summary["ok"] is True
        assert summary["complete"] is True
        assert summary["missing_alpha_deg"] == []
        assert len(summary["points"]) == 1
        point = summary["points"][0]
        assert point["converged"] is True
        for key in ("cl", "cd", "cm", "cdp", "xtr_top", "xtr_bottom"):
            assert math.isfinite(float(point[key]))


def test_viscous_sequence_direction_characterization_when_built(tmp_path: Path) -> None:
    driver_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_driver"
    if not driver_executable.exists():
        pytest.skip("direct-call kernel driver executable is not built")

    options = _naca2412_viscous_options()
    ascending_case = BaselineCase.from_mapping(
        {
            "id": "warm_start_direction_ascending",
            "airfoil": {"type": "naca", "code": "2412"},
            "options": options,
            "alpha_deg": [-4.0, 0.0, 4.0, 8.0],
        }
    )
    descending_case = BaselineCase.from_mapping(
        {
            "id": "warm_start_direction_descending",
            "airfoil": {"type": "naca", "code": "2412"},
            "options": options,
            "alpha_deg": [8.0, 4.0, 0.0, -4.0],
        }
    )

    ascending = run_kernel_case(
        ascending_case,
        driver_executable=driver_executable,
        run_root=tmp_path / "direction-runs",
        kernel_root=KERNEL_ROOT,
        timeout_seconds=120.0,
    )
    descending = run_kernel_case(
        descending_case,
        driver_executable=driver_executable,
        run_root=tmp_path / "direction-runs",
        kernel_root=KERNEL_ROOT,
        timeout_seconds=120.0,
    )

    assert [point["alpha_deg"] for point in ascending["points"]] == [-4.0, 0.0, 4.0, 8.0]
    assert [point["alpha_deg"] for point in descending["points"]] == [8.0, 4.0, 0.0, -4.0]
    _assert_matching_common_points(
        ascending,
        descending,
        coefficient_tolerance=7.0e-5,
        transition_tolerance=1.0e-4,
    )


def test_boundary_layer_reset_matches_isolated_viscous_point_when_built(tmp_path: Path) -> None:
    driver_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_driver"
    session_executable = KERNEL_ROOT / "build" / "kernel-driver" / "bin" / "xfoil_kernel_session"
    if not driver_executable.exists() or not session_executable.exists():
        pytest.skip("kernel driver/session executables are not built")

    options = _naca2412_viscous_options()
    seed_case = BaselineCase.from_mapping(
        {
            "id": "reset_seed_sequence",
            "airfoil": {"type": "naca", "code": "2412"},
            "options": options,
            "alpha_deg": [0.0, 2.0, 4.0],
        }
    )
    single_case = BaselineCase.from_mapping(
        {
            "id": "reset_single_alpha",
            "airfoil": {"type": "naca", "code": "2412"},
            "options": options,
            "alpha_deg": [6.0],
        }
    )

    isolated = run_kernel_case(
        single_case,
        driver_executable=driver_executable,
        run_root=tmp_path / "reset-isolated",
        kernel_root=KERNEL_ROOT,
        timeout_seconds=120.0,
    )
    with KernelSession(session_executable=session_executable, runtime_root=tmp_path / "reset-session-process") as session:
        seed = session.solve_case(
            seed_case,
            run_root=tmp_path / "reset-session",
            kernel_root=KERNEL_ROOT,
            timeout_seconds=120.0,
        )
        warm_continuation = session.solve_case(
            single_case,
            run_root=tmp_path / "reset-session",
            kernel_root=KERNEL_ROOT,
            timeout_seconds=120.0,
        )
        assert session.reset_boundary_layer_state() == "XK_OK reset_boundary_layer_state"
        reset_single = session.solve_case(
            single_case,
            run_root=tmp_path / "reset-session",
            kernel_root=KERNEL_ROOT,
            timeout_seconds=120.0,
        )

    assert seed["complete"] is True
    assert warm_continuation["complete"] is True
    assert reset_single["complete"] is True
    assert warm_continuation["diagnostics"]["geometry_changed"] is False
    assert warm_continuation["diagnostics"]["options_changed"] is False
    assert reset_single["diagnostics"]["geometry_changed"] is False
    assert reset_single["diagnostics"]["options_changed"] is False
    _assert_equivalent_kernel_summaries(
        isolated,
        reset_single,
        coefficient_tolerance=1.0e-8,
        transition_tolerance=1.0e-6,
    )
    _assert_equivalent_kernel_summaries(
        warm_continuation,
        reset_single,
        coefficient_tolerance=1.0e-6,
        transition_tolerance=1.0e-5,
    )


def _assert_equivalent_kernel_summaries(
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    coefficient_tolerance: float = 1.0e-8,
    transition_tolerance: float = 1.0e-6,
) -> None:
    assert first["ok"] is True
    assert second["ok"] is True
    assert first["complete"] == second["complete"]
    assert first["missing_alpha_deg"] == second["missing_alpha_deg"]
    assert len(first["points"]) == len(second["points"])

    for first_point, second_point in zip(first["points"], second["points"], strict=True):
        assert first_point["index"] == second_point["index"]
        assert first_point["alpha_deg"] == pytest.approx(second_point["alpha_deg"])
        assert first_point["converged"] == second_point["converged"]
        assert first_point["transition_forced_top"] == second_point["transition_forced_top"]
        assert first_point["transition_forced_bottom"] == second_point["transition_forced_bottom"]
        for key in ("cl", "cd", "cm", "cdp", "rms_bl"):
            assert first_point[key] == pytest.approx(second_point[key], abs=coefficient_tolerance)
        for key in ("xtr_top", "xtr_bottom"):
            assert first_point[key] == pytest.approx(second_point[key], abs=transition_tolerance)


def _assert_matching_common_points(
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    coefficient_tolerance: float,
    transition_tolerance: float,
) -> None:
    assert first["ok"] is True
    assert second["ok"] is True
    assert first["complete"] is True
    assert second["complete"] is True
    first_points = _points_by_alpha(first)
    second_points = _points_by_alpha(second)
    assert set(first_points) == set(second_points)
    for alpha, first_point in first_points.items():
        second_point = second_points[alpha]
        assert first_point["converged"] == second_point["converged"]
        assert first_point["transition_forced_top"] == second_point["transition_forced_top"]
        assert first_point["transition_forced_bottom"] == second_point["transition_forced_bottom"]
        for key in ("cl", "cd", "cm", "cdp"):
            assert first_point[key] == pytest.approx(second_point[key], abs=coefficient_tolerance)
        for key in ("xtr_top", "xtr_bottom"):
            assert first_point[key] == pytest.approx(second_point[key], abs=transition_tolerance)


def _points_by_alpha(summary: dict[str, Any]) -> dict[float, dict[str, Any]]:
    return {round(float(point["alpha_deg"]), 8): point for point in summary["points"]}


def _naca2412_viscous_options() -> dict[str, Any]:
    return {
        "viscous": True,
        "reynolds_number": 1_000_000.0,
        "mach_number": 0.0,
        "ncrit": 9.0,
        "xtr_top": 1.0,
        "xtr_bottom": 1.0,
        "panel_count": 160,
        "itmax": 80,
    }
