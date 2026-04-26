from __future__ import annotations

import sys
from pathlib import Path


KERNEL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KERNEL_ROOT / "tools"))

from xfoil_kernel_tools import build as build_tools  # noqa: E402


def test_kernel_source_groups_cover_extracted_sources_once() -> None:
    grouped_sources = [
        source_name
        for _group_name, source_names in build_tools.KERNEL_SOURCE_GROUPS
        for source_name in source_names
    ]

    assert grouped_sources == build_tools.EXTRACTED_KERNEL_SOURCE_NAMES
    assert len(grouped_sources) == len(set(grouped_sources))


def test_kernel_build_uses_tracked_extracted_sources(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], verbose: bool) -> None:
        commands.append(command)

    monkeypatch.setattr(build_tools, "_run", fake_run)

    executable = build_tools.build_kernel_driver(build_root=tmp_path / "kernel-driver")

    compile_sources = [
        Path(command[command.index("-o") - 1])
        for command in commands
        if "-c" in command
    ]
    assert executable == tmp_path / "kernel-driver" / "bin" / "xfoil_kernel_driver"
    assert build_tools.KERNEL_CORE_SOURCE in compile_sources
    assert build_tools.KERNEL_SOURCE_ROOT / "xfoil_kernel_subs.f" in compile_sources
    assert build_tools.KERNEL_SOURCE_ROOT / "xoper_kernel_subs.f" in compile_sources
    assert build_tools.KERNEL_SOURCE_ROOT / "xpanel.f" in compile_sources
    assert not (tmp_path / "kernel-driver" / "generated").exists()
    assert not any("vendor/xfoil/src" in str(path) for path in compile_sources)
    assert any(
        f"-I{build_tools.KERNEL_SOURCE_ROOT}" in command
        for command in commands
    )


def test_extracted_kernel_sources_have_portable_provenance_headers() -> None:
    disallowed_fragments = ("/Users/", "Py" "charmProjects")
    for path in build_tools.KERNEL_SOURCE_ROOT.iterdir():
        if path.suffix.lower() not in {".f", ".inc"}:
            continue
        text = path.read_text(errors="replace")
        for fragment in disallowed_fragments:
            assert fragment not in text


def test_selected_utility_sources_are_trimmed_to_kernel_closure() -> None:
    xpanel_text = (build_tools.KERNEL_SOURCE_ROOT / "xpanel.f").read_text(errors="replace").upper()
    xblsys_text = (build_tools.KERNEL_SOURCE_ROOT / "xblsys.f").read_text(errors="replace").upper()
    spline_text = (build_tools.KERNEL_SOURCE_ROOT / "spline.f").read_text(errors="replace").upper()
    xsolve_text = (build_tools.KERNEL_SOURCE_ROOT / "xsolve.f").read_text(errors="replace").upper()

    assert "SUBROUTINE APCALC" in xpanel_text
    assert "SUBROUTINE UESET" in xpanel_text
    assert "SUBROUTINE UECALC" not in xpanel_text
    assert "SUBROUTINE DSSET" not in xpanel_text

    assert "SUBROUTINE TRCHEK" in xblsys_text
    assert "SUBROUTINE HCT" in xblsys_text
    assert "SUBROUTINE DIT" not in xblsys_text

    assert "SUBROUTINE SPLIND" in spline_text
    assert "FUNCTION CURV" in spline_text
    assert "SUBROUTINE SEGSPL" in spline_text
    assert "SUBROUTINE SPLINE" not in spline_text
    assert "SUBROUTINE SPLINA" not in spline_text
    assert "FUNCTION CURVS" not in spline_text
    assert "SUBROUTINE SPLNXY" not in spline_text
    assert "SUBROUTINE SEGSPLD" not in spline_text

    assert "SUBROUTINE GAUSS" in xsolve_text
    assert "SUBROUTINE BLSOLV" in xsolve_text
    assert "SUBROUTINE CGAUSS" not in xsolve_text
