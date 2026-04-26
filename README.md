# xfoil-kernel

A standalone, non-interactive XFOIL kernel for generating 2-D airfoil section
polars, C81 tables, and structured convergence diagnostics.

`xfoil-kernel` packages a narrow XFOIL-derived workflow around the parts needed
by airfoil polar providers: airfoil loading, panel generation, inviscid solves,
viscous boundary-layer solves, transition controls, and coefficient reporting.
It is intended for scripted engineering workflows where interactive menus,
plotting, and hand-edited polar files are the wrong interface.

The project currently provides:

- tracked pristine-XFOIL comparison baselines,
- a selected extracted Fortran kernel source tree under `fortran/kernel/`,
- a one-shot Fortran driver for direct alpha-sequence solves,
- a persistent Fortran session that keeps warm XFOIL state between compatible
  requests,
- a JSON-lines Python worker process,
- a public Python API and `xfoil-kernel-api` command-line interface,
- an offline C81 table-generation workflow,
- tests that compare the extracted kernel against stored XFOIL references.

## Status

This is alpha engineering software. The current implementation is useful for
local scripted workflows, but the package boundary is still source-tree
oriented: build, baseline, and C81-generation commands expect access to this
checkout's `fortran/`, `baselines/`, `data/`, and `vendor/` directories.

The extracted numerical routines still use XFOIL's original COMMON-block state
and legacy Fortran structure. A small shared Fortran core centralizes the
non-interactive setup and solve path, but this is not yet a reentrant library
API.

## Requirements

- Python 3.10 or newer
- `gfortran` for building the kernel executables
- `pytest` for tests, via the `test` extra
- `c81_utils` only when generating C81 tables

The pristine-XFOIL comparison build may also require the platform dependencies
needed by the original XFOIL/plotlib build. The normal extracted-kernel build
does not link plotlib or X11.

## Install

For development and normal source-tree use:

```bash
git clone git@github.com:nebeals/xfoil-kernel.git
cd xfoil-kernel
python -m pip install -e ".[test]"
```

A normal wheel install currently exposes the Python import surface and console
entry points, but it does not yet bundle the full Fortran, baseline, data, and
vendored-source payload. For build and table-generation workflows, run from a
source checkout or set:

```bash
export XFOIL_KERNEL_ROOT=/path/to/xfoil-kernel
```

## Build

Build the extracted kernel driver and persistent session:

```bash
python scripts/build_kernel_driver.py
```

or, after an editable install:

```bash
xfoil-kernel-build-driver
xfoil-kernel-build-session
```

The normal build uses the tracked selected source tree under `fortran/kernel/`.
The vendored XFOIL snapshot under `vendor/xfoil/` is retained for provenance,
refreshing the extraction, and pristine-reference comparisons.

Only refresh the extracted sources intentionally:

```bash
python scripts/build_kernel_driver.py --refresh-extracted-sources
```

## Quick Start

Show worker status:

```bash
xfoil-kernel-api status
```

Solve a single alpha point:

```bash
xfoil-kernel-api solve-alpha \
  --naca 0012 \
  --alpha 4 \
  --reynolds 1000000
```

Solve an alpha sequence:

```bash
xfoil-kernel-api solve-alpha-sequence \
  --naca 0012 \
  --alpha -4 -2 0 2 4 \
  --reynolds 1000000 \
  --panel-count 180
```

Generate C81 tables from a manifest:

```bash
xfoil-kernel-api generate-c81 examples/c81_naca0012.yaml
```

C81 generation requires `c81_utils` to be installed or otherwise importable.

## Python API

```python
from xfoil_kernel import AirfoilSpec, SolveOptions, XfoilKernelClient

with XfoilKernelClient() as client:
    client.register_airfoil("naca0012", AirfoilSpec.naca("0012"))
    result = client.solve_alpha_sequence(
        "naca0012",
        alpha_deg=[-4.0, -2.0, 0.0, 2.0, 4.0],
        options=SolveOptions(
            viscous=True,
            reynolds_number=1_000_000.0,
            mach_number=0.0,
            ncrit=9.0,
            xtr_top=1.0,
            xtr_bottom=1.0,
            panel_count=180,
            itmax=100,
        ),
    )

for point in result.points:
    print(point.alpha_deg, point.cl, point.cd, point.cm, point.converged)
```

See `docs/python-api.md` for the complete public API contract.

## Runtime Model

The preferred solve unit is an alpha sequence, not isolated alpha points. XFOIL's
viscous solver is path dependent: nearby previous solutions provide useful
initial guesses for the next point. The persistent session preserves geometry,
panel, and boundary-layer state across compatible requests and invalidates that
state when geometry, Reynolds number, Mach number, transition settings, or
viscous mode change.

The public transition inputs are:

- `ncrit`: common e^n critical amplification ratio,
- `ncrit_top` and `ncrit_bottom`: advanced split-surface overrides,
- `xtr_top` and `xtr_bottom`: forced-transition trip locations.

`xtr=0.0` forces transition at the leading edge. `xtr=1.0` places the forced
trip at the trailing edge, leaving earlier natural transition to the e^n model.

## Convergence Contract

The kernel does not fabricate missing polar points. A protocol-valid solve can
still be numerically incomplete; in that case the result identifies the missing
angles of attack and includes convergence diagnostics. The C81 generator is
strict by default and refuses to write incomplete tables unless the user
explicitly opts into `allow_incomplete`.

This behavior is intentional. Near stall, strong separation, or difficult
geometries, missing points are data that should be reviewed rather than silently
filled.

## Validation

Run the Python and integration test suite:

```bash
PYTHONPATH=tools pytest -q tests
```

Build the extracted driver and run the stored reference comparison:

```bash
PYTHONPATH=tools python scripts/build_kernel_driver.py --clean
PYTHONPATH=tools python scripts/run_kernel_driver.py
PYTHONPATH=tools python scripts/compare_kernel_driver.py
```

The `sc1095_visc_re1e6_free_transition` reference intentionally records one
known incomplete point at `0.0 deg`; the comparison tooling treats that as part
of the current characterized behavior.

## Project Layout

```text
xfoil-kernel/
  baselines/              curated cases and compact reference outputs
  data/airfoils/          sample and stress-test coordinate files
  docs/                   protocol, API, CLI, C81, and planning notes
  examples/               example C81 manifest and API usage
  fortran/                extracted kernel drivers and selected XFOIL sources
  scripts/                build, baseline, worker, and C81 command wrappers
  tests/                  regression and integration tests
  tools/xfoil_kernel/     public Python API
  tools/xfoil_kernel_tools/ internal build/driver/worker support code
  vendor/xfoil/           vendored XFOIL 6.99 source snapshot
```

## Documentation

- `docs/python-api.md`: public Python API
- `docs/cli.md`: command-line interface
- `docs/protocol.md`: JSON-lines worker protocol
- `docs/c81-generation.md`: offline C81 table workflow
- `docs/warm-start-state.md`: viscous state and reset behavior
- `docs/kernel-plan.md`: extraction and modernization plan

## License and Provenance

This repository includes XFOIL-derived code and a vendored XFOIL 6.99 source
snapshot. XFOIL is GPL-licensed, and plotlib components carry LGPL/GPL license
terms. See `LICENSE.md`, `vendor/README.md`, and `vendor/xfoil/COPYING` for the
current provenance and license notes.

Applications that need license isolation should communicate with this project
through a process boundary such as the JSON-lines worker or the Python API that
wraps that worker.

## Roadmap

Current future-development topics include:

- adding CL-sequence solving through XFOIL's `SPECCL` path,
- deciding whether direct in-process bindings are worth the packaging and
  numerical-state risk,
- replacing COMMON-block state with an explicit state object only if true
  reentrancy becomes important,
- choosing a relocatable wheel strategy for the Fortran, baseline, data, and
  vendored-source payload,
- exposing additional paneling controls if users need more than `panel_count`.
