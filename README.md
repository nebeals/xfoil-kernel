# xfoil-kernel

`xfoil-kernel` is a scriptable XFOIL-based tool for generating 2-D airfoil
section data: lift, drag, pitching moment, transition location, and convergence
information. It is meant for engineers who want repeatable airfoil polar runs
without driving XFOIL's interactive menus by hand.

The main uses are:

- solve one angle of attack or a full alpha sweep for a NACA or coordinate-file
  airfoil,
- generate C81 tables for rotor, propeller, and lifting-surface analyses,
- preserve XFOIL's useful continuation behavior during viscous alpha sweeps,
- report missing or non-converged points clearly instead of silently filling
  them,
- compare the extracted calculation path against stored XFOIL reference cases.

This project is still young. It is useful for local engineering studies, but
results should be reviewed the same way you would review any XFOIL polar: check
for missing points, suspicious behavior near stall, Reynolds/Mach applicability,
and sensitivity to alpha-step size and transition assumptions.

## What Is Included

The repository includes:

- XFOIL 6.99 source material for reference and license traceability,
- a smaller extracted Fortran calculation path used for normal solves,
- a one-run Fortran driver for isolated alpha sweeps,
- a persistent Fortran session that keeps XFOIL's previous solution available
  for nearby follow-on points,
- Python command-line tools for building, solving, comparing, and generating
  C81 files,
- Python functions for calling the solver from scripts,
- sample airfoils and stored reference cases.

The extracted Fortran still follows XFOIL's original numerical structure. It is
not a clean-room rewrite, and it is not meant to hold many independent XFOIL
calculations inside one running program. The safest use is one running copy of
the solver for one active XFOIL calculation.

## Requirements

- Python 3.10 or newer
- `gfortran`
- `pytest` if you want to run the checks
- `c81_utils` if you want to write C81 tables

The normal extracted-kernel build does not use XFOIL's plotting library or X11.
Building the full pristine XFOIL comparison executable may require the same
system libraries that ordinary XFOIL uses.

## Install From This Repository

Clone the repository and install it from the folder you cloned:

```bash
git clone git@github.com:nebeals/xfoil-kernel.git
cd xfoil-kernel
python -m pip install -e ".[test]"
```

For now, build and table-generation workflows should be run from this cloned
folder. If you run the tools from another directory, set:

```bash
export XFOIL_KERNEL_ROOT=/path/to/xfoil-kernel
```

## Build The Solver

Build the extracted Fortran driver and persistent session:

```bash
python scripts/build_kernel_driver.py
```

After installation, the same build can be started with:

```bash
xfoil-kernel-build-driver
xfoil-kernel-build-session
```

The normal build uses the selected Fortran files under `fortran/kernel/`. The
full XFOIL source copy under `vendor/xfoil/` is kept so the project can be
checked against the original code and so the extracted files can be refreshed
when needed.

Only refresh the extracted Fortran files intentionally:

```bash
python scripts/build_kernel_driver.py --refresh-extracted-sources
```

## Quick Start

Show the available solver commands and settings:

```bash
xfoil-kernel-api status
```

Solve one alpha point:

```bash
xfoil-kernel-api solve-alpha \
  --naca 0012 \
  --alpha 4 \
  --reynolds 1000000
```

Solve an alpha sweep:

```bash
xfoil-kernel-api solve-alpha-sequence \
  --naca 0012 \
  --alpha -4 -2 0 2 4 \
  --reynolds 1000000 \
  --panel-count 180
```

Generate C81 tables from a YAML input file:

```bash
xfoil-kernel-api generate-c81 examples/c81_naca0012.yaml
```

C81 generation requires `c81_utils`.

## Use From Python

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

See `docs/python-api.md` for the full Python calling details.

## How Alpha Sweeps Are Run

For viscous XFOIL runs, an alpha sweep is usually better than unrelated single
points. XFOIL often uses the previous converged pressure and boundary-layer
solution as the starting guess for the next nearby point. This is one reason
traditional `ASEQ` runs often converge better than a pile of independent
single-alpha runs.

The persistent session keeps the current airfoil, paneling, and boundary-layer
solution available for compatible requests. It resets that state when the
airfoil, panel count, Reynolds number, Mach number, transition settings, or
viscous/inviscid setting changes.

Transition inputs are:

- `ncrit`: common e^n critical amplification ratio,
- `ncrit_top` and `ncrit_bottom`: separate top and bottom values, when you
  deliberately need them,
- `xtr_top` and `xtr_bottom`: forced-transition trip locations.

`xtr=0.0` forces transition at the leading edge. `xtr=1.0` puts the forced trip
at the trailing edge, so transition is free to occur earlier through the e^n
model.

## Convergence And Missing Points

The tool does not invent polar points that XFOIL did not converge. A requested
run can be valid but incomplete. In that case the output lists the missing
angles of attack and includes the information needed to inspect what happened.

The C81 generator is strict by default: it will not write an incomplete table
unless you explicitly choose `allow_incomplete`. This is intentional. Missing
points near stall, separated flow, or difficult geometry should be reviewed by
an engineer before they become part of an aircraft model.

## Check The Installation

Run the Python checks:

```bash
PYTHONPATH=tools pytest -q tests
```

Build the extracted solver and compare it with the stored XFOIL reference data:

```bash
PYTHONPATH=tools python scripts/build_kernel_driver.py --clean
PYTHONPATH=tools python scripts/run_kernel_driver.py
PYTHONPATH=tools python scripts/compare_kernel_driver.py
```

One reference case, `sc1095_visc_re1e6_free_transition`, intentionally records
a missing point at `0.0 deg`. That case is kept because it documents current
XFOIL behavior for a difficult coordinate-file airfoil.

## Repository Layout

```text
xfoil-kernel/
  baselines/              reference cases and compact solved summaries
  data/airfoils/          sample and stress-test airfoil coordinate files
  docs/                   details for commands, Python use, C81 files, and plans
  examples/               small runnable examples
  fortran/                extracted Fortran drivers and selected XFOIL routines
  scripts/                build, solve, comparison, and C81-generation commands
  tests/                  checks used during development
  tools/xfoil_kernel/     Python functions intended for users
  tools/xfoil_kernel_tools/ lower-level support code used by the commands
  vendor/xfoil/           included XFOIL 6.99 source copy
```

## More Documentation

- `docs/python-api.md`: calling the solver from Python
- `docs/cli.md`: command-line usage
- `docs/protocol.md`: details for the line-by-line worker format
- `docs/c81-generation.md`: offline C81 table workflow
- `docs/warm-start-state.md`: what XFOIL state is preserved between points
- `docs/kernel-plan.md`: extraction history and future work

## License And Source Notes

This repository includes code derived from XFOIL and an included XFOIL 6.99
source copy. XFOIL is GPL-licensed, and plotlib components carry LGPL/GPL
license terms. See `LICENSE.md`, `vendor/README.md`, and
`vendor/xfoil/COPYING` for the current source and license notes.

If another aircraft-design program needs to keep XFOIL separate for licensing
or robustness reasons, run `xfoil-kernel` as a separate command-line program and
exchange files or line-by-line requests with it.

## Future Work

Likely next improvements are:

- solve prescribed-lift (`CL`) sequences, not only alpha sequences,
- make it easier to install and use without working from the cloned folder,
- expose more of XFOIL's paneling controls when users need them,
- consider a deeper Fortran cleanup only if there is a clear engineering reason
  to run many independent XFOIL states inside one program.
