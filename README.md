# XFOIL Kernel Staging Area

This directory is an in-tree staging area for a future standalone XFOIL kernel
project. It is intentionally isolated so the eventual XFOIL-derived code can be
moved into its own repository with a clean license and distribution boundary.

## Current State

This tree is a working staging package, not just a plan:

- pristine-XFOIL baseline cases and compact reference outputs are tracked,
- XFOIL 6.99 source material is vendored under `vendor/xfoil/`,
- an extracted Fortran kernel source tree exists under
  `fortran/kernel/`,
- a direct-call Fortran driver exists at `fortran/xfoil_kernel_driver.f`,
- a persistent Fortran session exists at `fortran/xfoil_kernel_session.f`,
- shared Fortran core routines at `fortran/xfoil_kernel_core.f` centralize the
  COMMON-touching setup and solve path used by both executable front ends,
- the kernel-driver build compiles from the tracked extracted source tree by
  default, removes plot initialization, and does not compile plotlib/X11,
- every tracked Fortran source in the extracted kernel tree is generated as a
  selected subprogram extract,
- `scripts/xfoil_worker.py` provides a JSON-lines Python worker around the
  direct-call driver or persistent compiled session,
- `tools/xfoil_kernel/` provides the public Python API over the worker and C81
  generation workflow,
- `scripts/generate_c81.py` can generate C81 tables through `c81_utils`,
- `tests/` covers baseline parsing, worker protocol behavior, direct-driver
  comparison when the driver is built, option propagation, persistent-session
  behavior, modernization safety characterization, and C81 generation logic.

The current driver can solve alpha sequences and return `cl`, `cd`, `cm`,
transition outputs, and convergence diagnostics. The persistent session keeps
one XFOIL COMMON-block state alive, reusing geometry/panel state and warm
viscous state across compatible solve requests. Smoke and stress C81 generation
runs have also been performed locally under `runs/`; run outputs are
intentionally ignored because they are generated artifacts.

What is not done yet: the worker is still a Python front end, the JSON protocol
is not parsed by a compiled standalone executable, the kernel is not packaged as
a separate repository, and the extracted numerical routines still use XFOIL's
COMMON-block state and original legacy structure.

## Install

From this directory, install the Python tooling in editable mode:

```bash
python -m pip install -e ".[test]"
```

Editable/source-tree installation is the supported development and staging
install mode. It keeps the Python entry points connected to the tracked
`fortran/`, `baselines/`, `data/`, and vendored `vendor/xfoil/` source material
used by the build and reference commands.

A normal wheel install currently provides the public Python import surface and
console entry points, but it does not yet bundle the full Fortran/source tree
payload. Build, baseline, and table-generation workflows therefore need either
an editable/source-tree install or `XFOIL_KERNEL_ROOT=/path/to/xfoil-kernel`
pointing at a source checkout.

The command-line entry points are then available as:

```text
xfoil-kernel-build-pristine
xfoil-kernel-build-driver
xfoil-kernel-build-session
xfoil-kernel-run-pristine
xfoil-kernel-run-driver
xfoil-kernel-compare-driver
xfoil-kernel-write-references
xfoil-kernel-worker
xfoil-kernel-generate-c81
xfoil-kernel-api
```

The pristine-XFOIL build tools use `vendor/xfoil/` by default. The kernel
driver/session build uses tracked sources under `fortran/kernel/`; pass
`--refresh-extracted-sources` only when intentionally regenerating that
extraction from `vendor/xfoil/` or another source tree. Set
`XFOIL_KERNEL_ROOT=/path/to/xfoil-kernel` only when running installed tools from
outside the source checkout and the automatic root detection is not enough. If
the tools cannot find a source checkout, commands that need the staged Fortran
or baseline files fail with a source-root diagnostic instead of silently using a
Python installation directory as the project root.

Build the pristine baseline executable and kernel executables with:

```bash
python scripts/build_pristine_xfoil.py
python scripts/build_kernel_driver.py
```

or, from an editable install:

```bash
xfoil-kernel-build-pristine
xfoil-kernel-build-driver
xfoil-kernel-build-session
```

## Goal

Extract the subset of XFOIL needed by airfoil polar providers:

- airfoil loading from coordinates or NACA designations,
- normalization, spline, and panel setup,
- inviscid operating-point solve,
- viscous boundary-layer solve through the `VISCAL` path,
- forced-transition controls, free-transition settings, and actual transition
  outputs,
- `cl`, `cd`, `cm`, and convergence diagnostics.

XFOIL's forced-transition input is `XSTRIP(1)` / `XSTRIP(2)`, shown in
XFOIL's `VPAR` menu as top/bottom `Xtr` or `Xtrip`. The kernel protocol uses
`xtr_top` and `xtr_bottom` for those inputs. `0.0` forces transition at the
leading edge; `1.0` puts the forced trip at the trailing edge, so transition is
effectively free before then and is governed by the e^n `Ncrit` settings.
Actual computed transition locations should also be returned from `XOCTR(1)` /
`XOCTR(2)`.

The kernel should not include the interactive menus, plotting system, inverse
design tools, polar plotting UI, or hardcopy/PostScript support.

## Supported Runtime Contract

Airfoils:

- built-in NACA designations through `naca`,
- coordinate files or coordinate arrays through `type: coordinates`,
- optional `panel: false` for direct use of supplied coordinates.

Coordinate airfoils default to `panel: true`, which follows XFOIL's normal
`LOAD -> PANGEN` path. `PANGEN` splines the buffer geometry, distributes panel
nodes from curvature and paneling settings, handles doubled/corner points, and
creates the current airfoil. `panel_count` maps to XFOIL's `NPAN`. The kernel
does not currently expose geometry-editing tools such as `ADDP`, `CORN`,
`DELP`, or `MOVP`, and it does not expose the full `PPAR` panel-shape controls
beyond panel count.

Solve options:

- `viscous`: `true` calls `VISCAL`; `false` runs the inviscid path and reports
  zero profile drag from the kernel driver,
- `reynolds_number`: XFOIL `REINF1`,
- `mach_number`: XFOIL `MINF1`,
- `ncrit`: common e^n critical amplification value,
- `ncrit_top` / `ncrit_bottom`: advanced top/bottom overrides,
- `xtr_top` / `xtr_bottom`: forced-transition trip locations,
- `itmax`: viscous iteration limit,
- `panel_count`: requested generated panel nodes.

Requests should prefer alpha sequences over isolated points. XFOIL's viscous
solver is path dependent, and nearby previous solutions are part of the
numerical method rather than just a speed optimization.

## Convergence Contract

The kernel should not fabricate missing polar points. A solve request can be
protocol-valid and still be physically or numerically incomplete. In that case
the worker response is `ok: true`, `complete: false`, and lists
`missing_alpha_deg`. The offline C81 generator is strict by default and refuses
to write a table when requested points are missing unless the user explicitly
chooses `allow_incomplete`.

This is expected near stall or strong separation. For example, a highly curved
airfoil can legitimately fail at a negative-alpha point where the bottom
surface is separated. The correct workflow is to inspect the report and choose
whether to narrow the requested alpha range or accept a truncated table.

## Package Boundary

The intended architecture is process-isolated:

```text
client application
  generic airfoil polar interface
  optional subprocess or Python API client

xfoil-kernel
  GPL-compatible XFOIL-derived worker executable
  JSON-lines protocol
  regression baselines against pristine XFOIL
```

Downstream applications do not need to import or link XFOIL-derived code
directly. A client can talk to this worker through stdin/stdout, a Unix socket,
another ordinary interprocess protocol, or the public Python API wrapping the
same kernel functions and higher-level table-generation routines.

## Current Layout

```text
xfoil-kernel/
  README.md
  LICENSE.md                 license intent and provenance notes
  .gitignore                 local build/run artifacts
  docs/
    kernel-plan.md           extraction and modernization plan
    protocol.md              initial worker protocol
    python-api.md            public Python API
    cli.md                   public CLI
    c81-generation.md        offline C81 table generation workflow
    warm-start-state.md      viscous continuation state notes
  examples/
    c81_naca0012.yaml        minimal offline C81 generation manifest
  fortran/
    kernel/                  selected extracted kernel sources and includes
    xfoil_kernel_core.f      shared non-interactive core routines
    xfoil_kernel_driver.f    first direct-call namelist driver
    xfoil_kernel_session.f   persistent command-loop session driver
    kernel_prompt_stubs.f    fail-fast replacements for legacy prompts
    README.md                Fortran extraction notes
  scripts/
    build_pristine_xfoil.py  local pristine-XFOIL build helper
    build_kernel_driver.py   direct-call driver build helper
    run_pristine_xfoil.py    baseline deck generation and runner
    run_kernel_driver.py     direct-call driver baseline runner
    compare_kernel_driver.py reference comparison helper
    xfoil_worker.py          JSON-lines worker front end
    generate_c81.py          offline XFOIL-to-C81 table generator
    write_reference_baselines.py
  tests/
    README.md                regression-test strategy
  tools/
    xfoil_kernel/            public Python API facade
    xfoil_kernel_tools/      baseline/build/driver/worker helper code
  baselines/
    cases.json               curated pristine-XFOIL cases
    reference/               compact tracked reference outputs
  vendor/
    xfoil/                    vendored XFOIL source snapshot
```

## Near-Term Steps

1. Keep using `scripts/generate_c81.py` as the first production workflow:
   generate C81 tables offline, inspect the convergence report, and point
   downstream analysis tools at the generated files.
2. Continue polishing the Python API around the existing worker and C81
   generator so users can call common workflows without assembling raw JSON.
3. Decide whether the persistent worker should keep using the Python front end
   or move JSON parsing into a compiled worker.
4. Continue reducing vendored XFOIL source only where it removes real packaging
   risk without disturbing the validated numerical path.
