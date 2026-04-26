from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable, Sequence

from .paths import DEFAULT_XFOIL_ROOT, KERNEL_ROOT, require_kernel_root

DEFAULT_BUILD_ROOT = KERNEL_ROOT / "build" / "pristine-xfoil"
DEFAULT_KERNEL_DRIVER_BUILD_ROOT = KERNEL_ROOT / "build" / "kernel-driver"
KERNEL_DRIVER_SOURCE = KERNEL_ROOT / "fortran" / "xfoil_kernel_driver.f"
KERNEL_SESSION_SOURCE = KERNEL_ROOT / "fortran" / "xfoil_kernel_session.f"
KERNEL_CORE_SOURCE = KERNEL_ROOT / "fortran" / "xfoil_kernel_core.f"
KERNEL_PROMPT_STUB_SOURCE = KERNEL_ROOT / "fortran" / "kernel_prompt_stubs.f"
KERNEL_SOURCE_ROOT = KERNEL_ROOT / "fortran" / "kernel"


PLOTLIB_FORTRAN_SOURCES = [
    "plt_base.f",
    "plt_font.f",
    "plt_util.f",
    "plt_color.f",
    "set_subs.f",
    "gw_subs.f",
    "ps_subs.f",
    "plt_old.f",
    "plt_3D.f",
]
PLOTLIB_C_SOURCES = ["Xwin2.c"]

XFOIL_SRC_FORTRAN_SOURCES = [
    "xfoil.f",
    "xpanel.f",
    "xoper.f",
    "xtcam.f",
    "xgdes.f",
    "xqdes.f",
    "xmdes.f",
    "xsolve.f",
    "xbl.f",
    "xblsys.f",
    "xpol.f",
    "xplots.f",
    "pntops.f",
    "xgeom.f",
    "xutils.f",
    "modify.f",
    "blplot.f",
    "polplt.f",
    "aread.f",
    "naca.f",
    "spline.f",
    "plutil.f",
    "iopol.f",
    "gui.f",
    "sort.f",
    "dplot.f",
    "profil.f",
    "userio.f",
    "frplot.f",
    "ntcalc.f",
]
XFOIL_OSRC_FORTRAN_SOURCES = ["osmap.f"]
XFOIL_OSRC_C_SOURCES = ["getosfile.c"]

KERNEL_XFOIL_SUBROUTINES = [
    "INIT",
    "MRCL",
    "COMSET",
    "CPCALC",
    "CLCALC",
    "CDCALC",
    "LOAD",
    "NACA",
    "PANGEN",
    "TECALC",
]
KERNEL_XOPER_SUBROUTINES = ["SPECAL", "VISCAL"]
KERNEL_XGDES_SUBROUTINES = ["ABCOPY"]
KERNEL_XGEOM_SUBROUTINES = ["LEFIND", "NORM", "GEOPAR", "AECALC", "TCCALC", "SOPPS"]
KERNEL_USERIO_SUBROUTINES = ["GETFLT", "STRIP"]
KERNEL_AREAD_SUBROUTINES = ["AREAD"]
KERNEL_NACA_SUBROUTINES = ["NACA4", "NACA5"]
KERNEL_XUTILS_SUBPROGRAMS = ["SETEXP", "ATANC"]
KERNEL_XPANEL_SUBROUTINES = [
    "APCALC",
    "NCALC",
    "PSILIN",
    "PSWLIN",
    "GGCALC",
    "QWCALC",
    "QDCALC",
    "XYWAKE",
    "STFIND",
    "IBLPAN",
    "XICALC",
    "UICALC",
    "QVFUE",
    "QISET",
    "GAMQV",
    "STMOVE",
    "UESET",
]
KERNEL_XBL_SUBROUTINES = [
    "SETBL",
    "IBLSYS",
    "MRCHUE",
    "MRCHDU",
    "XIFSET",
    "UPDATE",
    "DSLIM",
    "BLPINI",
]
KERNEL_XBLSYS_SUBROUTINES = [
    "TRCHEK",
    "AXSET",
    "TRCHEK2",
    "BLSYS",
    "TESYS",
    "BLPRV",
    "BLKIN",
    "BLVAR",
    "BLMID",
    "TRDIF",
    "BLDIF",
    "DAMPL",
    "DAMPL2",
    "HKIN",
    "DIL",
    "DILW",
    "HSL",
    "CFL",
    "HST",
    "CFT",
    "HCT",
]
KERNEL_SPLINE_SUBPROGRAMS = [
    "SPLIND",
    "TRISOL",
    "SEVAL",
    "DEVAL",
    "D2VAL",
    "CURV",
    "SINVRT",
    "SCALC",
    "SEGSPL",
]
KERNEL_XSOLVE_SUBROUTINES = ["GAUSS", "LUDCMP", "BAKSUB", "BLSOLV"]
KERNEL_SOURCE_GROUPS = [
    ("airfoil_io", ["aread.f", "naca.f", "userio_kernel_subs.f"]),
    (
        "geometry",
        ["xgdes_kernel_subs.f", "xgeom_kernel_subs.f", "spline.f", "xutils.f"],
    ),
    (
        "panel_inviscid",
        ["xfoil_kernel_subs.f", "xoper_kernel_subs.f", "xpanel.f", "xsolve.f"],
    ),
    ("viscous_boundary_layer", ["xbl.f", "xblsys.f"]),
]
EXTRACTED_KERNEL_SOURCE_NAMES = [
    source_name
    for _group_name, source_names in KERNEL_SOURCE_GROUPS
    for source_name in source_names
]
EXTRACTED_KERNEL_INCLUDE_NAMES = [
    "XFOIL.INC",
    "XBL.INC",
    "BLPAR.INC",
    "PINDEX.INC",
]


def build_pristine_xfoil(
    *,
    xfoil_root: Path = DEFAULT_XFOIL_ROOT,
    build_root: Path = DEFAULT_BUILD_ROOT,
    clean: bool = False,
    verbose: bool = False,
) -> Path:
    """Build a local pristine-XFOIL executable without writing into xfoil_root."""

    if clean and build_root.exists():
        shutil.rmtree(build_root)

    src_root = xfoil_root / "src"
    osrc_root = xfoil_root / "osrc"
    plotlib_root = xfoil_root / "plotlib"
    _require_directory(src_root)
    _require_directory(osrc_root)
    _require_directory(plotlib_root)

    objects_dir = build_root / "objects"
    plotlib_objects_dir = objects_dir / "plotlib"
    xfoil_objects_dir = objects_dir / "xfoil"
    bin_dir = build_root / "bin"
    for directory in (plotlib_objects_dir, xfoil_objects_dir, bin_dir):
        directory.mkdir(parents=True, exist_ok=True)

    x11_cflags, x11_libs = _pkg_config_x11()
    fortran_flags = [
        "-O2",
        "-fdefault-real-8",
        "-std=legacy",
        "-fallow-argument-mismatch",
        "-ffixed-line-length-none",
        f"-I{src_root}",
        f"-I{plotlib_root}",
    ]
    c_flags = [
        "-O2",
        "-DUNDERSCORE",
        "-Wno-implicit-function-declaration",
        f"-I{src_root}",
        f"-I{plotlib_root}",
        *x11_cflags,
    ]

    plotlib_objects = []
    for source_name in PLOTLIB_FORTRAN_SOURCES:
        source = plotlib_root / source_name
        obj = plotlib_objects_dir / f"{Path(source_name).stem}.o"
        _run(["gfortran", "-c", *fortran_flags, str(source), "-o", str(obj)], verbose)
        plotlib_objects.append(obj)

    for source_name in PLOTLIB_C_SOURCES:
        source = plotlib_root / source_name
        obj = plotlib_objects_dir / f"{Path(source_name).stem}.o"
        _run(["cc", "-c", *c_flags, str(source), "-o", str(obj)], verbose)
        plotlib_objects.append(obj)

    plotlib = build_root / "libPlt_gDP.a"
    _run(["ar", "rcs", str(plotlib), *map(str, plotlib_objects)], verbose)

    xfoil_objects = []
    for source_name in XFOIL_SRC_FORTRAN_SOURCES:
        source = src_root / source_name
        obj = xfoil_objects_dir / f"{Path(source_name).stem}.o"
        _run(["gfortran", "-c", *fortran_flags, str(source), "-o", str(obj)], verbose)
        xfoil_objects.append(obj)

    for source_name in XFOIL_OSRC_FORTRAN_SOURCES:
        source = osrc_root / source_name
        obj = xfoil_objects_dir / f"{Path(source_name).stem}.o"
        _run(["gfortran", "-c", *fortran_flags, str(source), "-o", str(obj)], verbose)
        xfoil_objects.append(obj)

    for source_name in XFOIL_OSRC_C_SOURCES:
        source = osrc_root / source_name
        obj = xfoil_objects_dir / f"{Path(source_name).stem}.o"
        _run(["cc", "-c", *c_flags, str(source), "-o", str(obj)], verbose)
        xfoil_objects.append(obj)

    executable = bin_dir / "xfoil"
    _run(
        [
            "gfortran",
            "-o",
            str(executable),
            *map(str, xfoil_objects),
            str(plotlib),
            *x11_libs,
        ],
        verbose,
    )
    return executable


def build_kernel_driver(
    *,
    build_root: Path = DEFAULT_KERNEL_DRIVER_BUILD_ROOT,
    driver_source: Path = KERNEL_DRIVER_SOURCE,
    clean: bool = False,
    verbose: bool = False,
) -> Path:
    """Build the direct-call kernel driver from tracked extracted sources."""

    kernel_root = require_kernel_root(KERNEL_ROOT)
    if clean and build_root.exists():
        shutil.rmtree(build_root)

    _require_kernel_source(driver_source, "Kernel driver source", kernel_root)
    _require_kernel_source(KERNEL_SESSION_SOURCE, "Kernel session source", kernel_root)
    _require_kernel_source(KERNEL_CORE_SOURCE, "Kernel core source", kernel_root)
    _require_kernel_source(KERNEL_PROMPT_STUB_SOURCE, "Kernel prompt stub source", kernel_root)
    _require_kernel_source_directory(KERNEL_SOURCE_ROOT)
    kernel_sources = _extracted_kernel_sources(KERNEL_SOURCE_ROOT)
    for include_name in EXTRACTED_KERNEL_INCLUDE_NAMES:
        _require_kernel_source(
            KERNEL_SOURCE_ROOT / include_name,
            f"Kernel include {include_name}",
            kernel_root,
        )

    objects_dir = build_root / "objects"
    xfoil_objects_dir = objects_dir / "xfoil"
    bin_dir = build_root / "bin"
    for directory in (xfoil_objects_dir, bin_dir):
        directory.mkdir(parents=True, exist_ok=True)

    fortran_flags = [
        "-O2",
        "-fdefault-real-8",
        "-std=legacy",
        "-fallow-argument-mismatch",
        "-ffixed-line-length-none",
        f"-I{KERNEL_SOURCE_ROOT}",
    ]

    xfoil_objects = []
    prompt_stub_object = xfoil_objects_dir / "kernel_prompt_stubs.o"
    _run(["gfortran", "-c", *fortran_flags, str(KERNEL_PROMPT_STUB_SOURCE), "-o", str(prompt_stub_object)], verbose)
    xfoil_objects.append(prompt_stub_object)

    core_object = xfoil_objects_dir / "xfoil_kernel_core.o"
    _run(["gfortran", "-c", *fortran_flags, str(KERNEL_CORE_SOURCE), "-o", str(core_object)], verbose)
    xfoil_objects.append(core_object)

    for source in kernel_sources:
        obj = xfoil_objects_dir / f"{source.stem}.o"
        _run(["gfortran", "-c", *fortran_flags, str(source), "-o", str(obj)], verbose)
        xfoil_objects.append(obj)

    driver_object = xfoil_objects_dir / "xfoil_kernel_driver.o"
    _run(["gfortran", "-c", *fortran_flags, str(driver_source), "-o", str(driver_object)], verbose)

    executable = bin_dir / "xfoil_kernel_driver"
    _run(
        [
            "gfortran",
            "-o",
            str(executable),
            str(driver_object),
            *map(str, xfoil_objects),
        ],
        verbose,
    )

    session_object = xfoil_objects_dir / "xfoil_kernel_session.o"
    _run(["gfortran", "-c", *fortran_flags, str(KERNEL_SESSION_SOURCE), "-o", str(session_object)], verbose)

    session_executable = bin_dir / "xfoil_kernel_session"
    _run(
        [
            "gfortran",
            "-o",
            str(session_executable),
            str(session_object),
            *map(str, xfoil_objects),
        ],
        verbose,
    )
    return executable


def refresh_extracted_kernel_sources(
    *,
    xfoil_root: Path = DEFAULT_XFOIL_ROOT,
    kernel_source_root: Path = KERNEL_SOURCE_ROOT,
) -> list[Path]:
    """Refresh the tracked selected kernel sources from XFOIL."""

    src_root = xfoil_root / "src"
    _require_directory(src_root)
    kernel_source_root.mkdir(parents=True, exist_ok=True)
    written = [
        _write_selected_subroutines_source(
            src_root / "xfoil.f",
            kernel_source_root / "xfoil_kernel_subs.f",
            KERNEL_XFOIL_SUBROUTINES,
            transforms=(_remove_kernel_plot_initialization,),
            quiet=True,
        ),
        _write_selected_subroutines_source(
            src_root / "xoper.f",
            kernel_source_root / "xoper_kernel_subs.f",
            KERNEL_XOPER_SUBROUTINES,
            transforms=(_remove_viscal_debug_file_dump, _remove_hinge_moment_postprocessing),
            quiet=True,
        ),
        _write_selected_subroutines_source(
            src_root / "xgdes.f",
            kernel_source_root / "xgdes_kernel_subs.f",
            KERNEL_XGDES_SUBROUTINES,
            quiet=True,
        ),
        _write_selected_subroutines_source(
            src_root / "xgeom.f",
            kernel_source_root / "xgeom_kernel_subs.f",
            KERNEL_XGEOM_SUBROUTINES,
            quiet=True,
        ),
        _write_selected_subroutines_source(
            src_root / "userio.f",
            kernel_source_root / "userio_kernel_subs.f",
            KERNEL_USERIO_SUBROUTINES,
            quiet=True,
        ),
        _write_selected_subroutines_source(
            src_root / "aread.f",
            kernel_source_root / "aread.f",
            KERNEL_AREAD_SUBROUTINES,
            quiet=True,
        ),
        _write_selected_subroutines_source(
            src_root / "naca.f",
            kernel_source_root / "naca.f",
            KERNEL_NACA_SUBROUTINES,
            quiet=True,
        ),
        _write_selected_subroutines_source(
            src_root / "xutils.f",
            kernel_source_root / "xutils.f",
            KERNEL_XUTILS_SUBPROGRAMS,
            quiet=True,
        ),
        _write_selected_subroutines_source(
            src_root / "xpanel.f",
            kernel_source_root / "xpanel.f",
            KERNEL_XPANEL_SUBROUTINES,
            quiet=True,
        ),
        _write_selected_subroutines_source(
            src_root / "xbl.f",
            kernel_source_root / "xbl.f",
            KERNEL_XBL_SUBROUTINES,
            quiet=True,
        ),
        _write_selected_subroutines_source(
            src_root / "xblsys.f",
            kernel_source_root / "xblsys.f",
            KERNEL_XBLSYS_SUBROUTINES,
            quiet=True,
        ),
        _write_selected_subroutines_source(
            src_root / "spline.f",
            kernel_source_root / "spline.f",
            KERNEL_SPLINE_SUBPROGRAMS,
            quiet=True,
        ),
        _write_selected_subroutines_source(
            src_root / "xsolve.f",
            kernel_source_root / "xsolve.f",
            KERNEL_XSOLVE_SUBROUTINES,
            quiet=True,
        ),
    ]
    for include_name in EXTRACTED_KERNEL_INCLUDE_NAMES:
        source = src_root / include_name
        destination = kernel_source_root / include_name
        shutil.copyfile(source, destination)
        written.append(destination)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a local pristine-XFOIL executable.")
    parser.add_argument("--xfoil-root", type=Path, default=DEFAULT_XFOIL_ROOT)
    parser.add_argument("--build-root", type=Path, default=DEFAULT_BUILD_ROOT)
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    try:
        executable = build_pristine_xfoil(
            xfoil_root=args.xfoil_root,
            build_root=args.build_root,
            clean=args.clean,
            verbose=args.verbose,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"build failed: {exc}")
        return 1

    print(executable)
    return 0


def kernel_driver_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the direct-call XFOIL kernel driver.")
    parser.add_argument(
        "--xfoil-root",
        type=Path,
        default=DEFAULT_XFOIL_ROOT,
        help="XFOIL source tree used only when --refresh-extracted-sources is set.",
    )
    parser.add_argument("--build-root", type=Path, default=DEFAULT_KERNEL_DRIVER_BUILD_ROOT)
    parser.add_argument("--driver-source", type=Path, default=KERNEL_DRIVER_SOURCE)
    parser.add_argument(
        "--refresh-extracted-sources",
        action="store_true",
        help="Regenerate fortran/kernel from the configured XFOIL source tree before building.",
    )
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.refresh_extracted_sources:
            refresh_extracted_kernel_sources(xfoil_root=args.xfoil_root)
        executable = build_kernel_driver(
            build_root=args.build_root,
            driver_source=args.driver_source,
            clean=args.clean,
            verbose=args.verbose,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"build failed: {exc}")
        return 1

    print(executable)
    return 0


def build_kernel_session(
    *,
    build_root: Path = DEFAULT_KERNEL_DRIVER_BUILD_ROOT,
    driver_source: Path = KERNEL_DRIVER_SOURCE,
    clean: bool = False,
    verbose: bool = False,
) -> Path:
    """Build the direct-call driver artifacts and return the persistent session executable."""

    build_kernel_driver(
        build_root=build_root,
        driver_source=driver_source,
        clean=clean,
        verbose=verbose,
    )
    return build_root / "bin" / "xfoil_kernel_session"


def kernel_session_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the persistent XFOIL kernel session executable.")
    parser.add_argument(
        "--xfoil-root",
        type=Path,
        default=DEFAULT_XFOIL_ROOT,
        help="XFOIL source tree used only when --refresh-extracted-sources is set.",
    )
    parser.add_argument("--build-root", type=Path, default=DEFAULT_KERNEL_DRIVER_BUILD_ROOT)
    parser.add_argument("--driver-source", type=Path, default=KERNEL_DRIVER_SOURCE)
    parser.add_argument(
        "--refresh-extracted-sources",
        action="store_true",
        help="Regenerate fortran/kernel from the configured XFOIL source tree before building.",
    )
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.refresh_extracted_sources:
            refresh_extracted_kernel_sources(xfoil_root=args.xfoil_root)
        executable = build_kernel_session(
            build_root=args.build_root,
            driver_source=args.driver_source,
            clean=args.clean,
            verbose=args.verbose,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"build failed: {exc}")
        return 1

    print(executable)
    return 0


def _pkg_config_x11() -> tuple[list[str], list[str]]:
    try:
        cflags = subprocess.check_output(
            ["pkg-config", "--cflags", "x11"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).split()
        libs = subprocess.check_output(
            ["pkg-config", "--libs", "x11"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).split()
        return cflags, libs
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ["-I/usr/X11/include"], ["-L/usr/X11R6/lib", "-lX11"]


def _require_directory(path: Path) -> None:
    if not path.is_dir():
        raise FileNotFoundError(f"Required directory not found: {path}")


def _require_kernel_source(path: Path, description: str, kernel_root: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(
            f"{description} not found: {path}. "
            f"Expected a source checkout rooted at {kernel_root}."
        )


def _require_kernel_source_directory(path: Path) -> None:
    if not path.is_dir():
        raise FileNotFoundError(
            f"Extracted kernel source directory not found: {path}. "
            "Run refresh_extracted_kernel_sources() from the source checkout."
        )


def _extracted_kernel_sources(kernel_source_root: Path) -> list[Path]:
    sources = []
    for source_name in EXTRACTED_KERNEL_SOURCE_NAMES:
        source = kernel_source_root / source_name
        if not source.is_file():
            raise FileNotFoundError(f"Extracted kernel source not found: {source}")
        sources.append(source)
    return sources


def _run(command: list[str], verbose: bool) -> None:
    if verbose:
        print(" ".join(command))
    subprocess.run(command, check=True)


def _write_selected_subroutines_source(
    source: Path,
    destination: Path,
    subroutine_names: Sequence[str],
    *,
    transforms: Sequence[Callable[[str], str]] = (),
    quiet: bool = False,
) -> Path:
    """Write a build artifact containing only selected subroutine/function blocks."""

    blocks = _fortran_blocks(source)
    selected = []
    for name in subroutine_names:
        normalized = name.upper()
        if normalized not in blocks:
            raise ValueError(f"Could not find {name} in {source}")
        selected.append(blocks[normalized])

    body = "\n".join(selected) + "\n"
    for transform in transforms:
        body = transform(body)
    if quiet:
        body = _quiet_xfoil_stdout(body)

    destination.write_text(
        "C Extracted kernel source. Original source: "
        f"{_source_label(source)}\n"
        "C Contains only subroutines needed by the non-interactive kernel driver.\n"
        + body
    )
    return destination


def _fortran_blocks(source: Path) -> dict[str, str]:
    text = source.read_text(errors="replace")
    matches = list(re.finditer(r"(?im)^      (SUBROUTINE|FUNCTION)\s+(\w+)\b", text))
    blocks = {}
    for index, match in enumerate(matches):
        name = match.group(2).upper()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks[name] = text[match.start() : end]
    return blocks


def _source_label(source: Path) -> str:
    try:
        return str(source.resolve().relative_to(KERNEL_ROOT))
    except ValueError:
        return str(source)


def _quiet_xfoil_stdout(text: str) -> str:
    """Redirect XFOIL chatter away from stdout in generated build sources."""

    return re.sub(r"\b(write)\s*\(\s*\*", r"\1(99", text, flags=re.IGNORECASE)


def _remove_viscal_debug_file_dump(text: str) -> str:
    """Remove VISCAL's hard-coded .bl debug-file dump from generated xoper.f."""

    start_marker = "\n\n        is = 1\n        hkmax = 0.\n"
    end_marker = "\n        close(lu)\n\n\n\n      RETURN"
    start = text.find(start_marker)
    end = text.find(end_marker, start)
    if start < 0 or end < 0:
        raise ValueError("Could not find VISCAL debug .bl dump block in xoper.f.")
    return (
        text[:start]
        + "\n\nC---- Kernel build: removed hard-coded VISCAL .bl debug-file dump.\n"
        + text[end + len("\n        close(lu)\n\n\n") :]
    )


def _remove_kernel_plot_initialization(text: str) -> str:
    """Remove INIT calls into plotlib from the non-interactive kernel build."""

    text = re.sub(
        r"(?im)^      CALL PLINITIALIZE\s*\n",
        "C---- Kernel build: plotting initialization removed.\n",
        text,
    )
    return re.sub(
        r"(?im)^      CALL COLORSPECTRUMHUES\([^\n]*\)\s*\n",
        "C---- Kernel build: plotting color setup removed.\n",
        text,
    )


def _remove_hinge_moment_postprocessing(text: str) -> str:
    """Remove optional hinge-moment postprocessing from generated xoper.f."""

    return re.sub(
        r"(?im)^      IF\(LFLAP\) CALL MHINGE\s*\n",
        "C---- Kernel build: hinge-moment postprocessing is outside this path.\n",
        text,
    )


if __name__ == "__main__":
    raise SystemExit(main())
