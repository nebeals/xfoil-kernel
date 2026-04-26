# Fortran Kernel Notes

This directory holds the non-interactive driver/session programs, their shared
Fortran core routines, and the tracked extracted Fortran kernel source.

The `kernel/` subtree is a conservative selected extraction from the vendored
XFOIL 6.99 source snapshot. It contains the selected subroutine blocks and
the include-file closure needed to compile those sources. The normal kernel
build now compiles from `kernel/` instead of regenerating files from
`vendor/xfoil/src`. Every tracked Fortran source in `kernel/` is produced as a
selected extract rather than a full copied XFOIL file. The local
`kernel/README.md` lists the current airfoil I/O, geometry, panel/inviscid, and
viscous source groups.

## Initial Extraction Target

The first working driver calls XFOIL's computational path directly:

- `INIT`
- `LOAD` or `NACA`
- `PANGEN` or `ABCOPY`
- `MRCL`
- `COMSET`
- `SPECAL`
- `VISCAL`
- `CLCALC`
- `CDCALC`

The initial goal is to preserve original numerical behavior. Refactoring
`COMMON` state into a modern explicit state object comes later, after
regression baselines are passing.

`xfoil_kernel_core.f` is the first modernization boundary around that legacy
state. It centralizes setup, option application, geometry loading,
boundary-layer reset, operating-point preparation, and alpha-point solves for
both executable front ends while leaving the original COMMON-backed numerical
state intact.

## Expected Worker API

The narrow Fortran-facing API should look conceptually like:

```text
xk_init()
xk_set_airfoil_from_naca(code)
xk_set_airfoil_from_coordinates(n, x, y)
xk_set_options(re, mach, ncrit, xtr_top, xtr_bottom, itmax)
xk_solve_alpha_sequence(n_alpha, alpha_deg, results)
```

The actual command parsing can live in a small worker program around this API.
If a caller explicitly asks for split top/bottom `Ncrit`, the worker can map
those advanced overrides into the lower-level `ACRIT(1:2)` state.

## Current Driver

`xfoil_kernel_driver.f` is a proof driver, not the final worker protocol. It
reads one `&xkcase` namelist from stdin, calls the shared core routines to set
up and solve an alpha sequence, and emits `XK_POINT` rows that are easy for the
Python harness to parse.

`xfoil_kernel_session.f` is the first persistent compiled worker. It reads
simple line commands (`PING`, `RESET_BOUNDARY_LAYER_STATE`, `SOLVE`,
`SHUTDOWN`) and a `&xkcase` namelist for each solve. It uses the same shared
core routines as the one-shot driver, keeps one XFOIL COMMON-block state alive,
reloads geometry only when the requested
geometry/panel settings change, resets viscous state when Reynolds, Mach,
transition, or viscous-mode settings change, and exposes XFOIL's "resetting the
boundary layer" operation without rebuilding geometry.

Current behavior:

- supports NACA designations and coordinate-file airfoils,
- uses XFOIL's `PANGEN` panel generator for coordinate airfoils by default,
  with `ABCOPY` available through `panel: false`,
- supports viscous and inviscid alpha sequences,
- maps common `ncrit` values or explicit `ncrit_top` / `ncrit_bottom`
  overrides, plus `xtr_top` and `xtr_bottom`, into XFOIL COMMON state,
- reports `CL`, `CD`, `CM`, `CDp`, `LVCONV`, `RMSBL`, `XOCTR`, and `TFORCE`,
- keeps XFOIL's warm-started viscous state across the requested sequence,
- supports explicitly resetting the boundary-layer/wake convergence state.
- extracted XFOIL source copies redirect internal `WRITE(*)` chatter away from
  stdout, leaving stdout for `XK_HEADER`, `XK_POINT`, and `XK_END` rows.
- the kernel-driver build removes VISCAL's hard-coded `.bl` debug-file dump
  from the generated `xoper.f` artifact.
- the kernel-driver build generates selected subroutine-only copies of the
  broad legacy files that used to drag in menus and plotting. Those selected
  copies are now tracked under `fortran/kernel/`.
- the tracked panel, spline, boundary-layer, and linear-solver extracts contain
  only the subprograms reachable from the kernel solve path.
- the generated `INIT` copy removes plot initialization, so the kernel driver
  no longer links plotlib, X11, or plot stubs.
- `kernel_prompt_stubs.f` turns any accidentally reachable legacy prompt into
  an explicit `XK_ERROR` instead of blocking on stdin.

Known temporary rough edges:

- the selected viscous and panel routines still use XFOIL's original
  COMMON-block state and numerical structure,
- the shared core reduces duplicated COMMON-touching setup code but is not a
  reentrant explicit state object,
- the persistent compiled session has a minimal line protocol, with JSON still
  handled by the Python worker front end.

## Refreshing The Selected Extraction

The extracted files should normally be edited and reviewed as tracked source.
If the source snapshot changes, regenerate the selected extraction explicitly:

```bash
python scripts/build_kernel_driver.py --refresh-extracted-sources
```

or from an editable install:

```bash
xfoil-kernel-build-driver --refresh-extracted-sources
```

The refresh path uses `vendor/xfoil/` by default. Pass `--xfoil-root` only when
intentionally refreshing from another XFOIL checkout. Always rerun pristine
comparison tests after refreshing or editing extracted kernel sources.

## Transition State Mapping

Keep these XFOIL variables in the kernel API:

- `ACRIT(1)` / `ACRIT(2)`: top/bottom e^n transition parameter. The public
  API should prefer a single `ncrit` value and reserve split values for
  deliberate advanced use.
- `XSTRIP(1)` / `XSTRIP(2)`: top/bottom forced-trip locations, exposed by the
  protocol as `xtr_top` and `xtr_bottom`. `0.0` forces leading-edge
  transition; `1.0` is XFOIL's default and places the trip at the trailing
  edge, leaving natural e^n transition free to occur upstream.
- `XOCTR(1)` / `XOCTR(2)`: top/bottom actual transition `x/c` after solving.
- `TFORCE(1)` / `TFORCE(2)`: whether transition was forced rather than free.

Changing any transition input should invalidate the current viscous solution
state and be included in the client-side cache key.

## Important Constraints

- XFOIL global state is not reentrant or thread-safe.
- A worker process should own exactly one active solver state.
- Warm-starting alpha sequences is part of the numerical method, not just an
  optimization.
- Any remaining prompt path should fail explicitly rather than reading stdin.
