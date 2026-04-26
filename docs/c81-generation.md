# Offline C81 Generation

The first production workflow for XFOIL-derived section data is offline:

1. run XFOIL through the kernel over a requested airfoil/Re/Mach/alpha grid,
2. write C81 tables with `c81_utils`,
3. point downstream analysis inputs at the generated C81 files.

Sizing, mission, and performance runs can then read stable C81 artifacts
instead of calling XFOIL inside the analysis loop.

## Requirements

- build the direct-call kernel driver:

  ```bash
  python xfoil-kernel/scripts/build_kernel_driver.py
  ```

  This build also emits `build/kernel-driver/bin/xfoil_kernel_session`, which
  is preferred for C81 generation because it preserves XFOIL state across
  compatible solve requests.

- make `c81_utils` importable. The generator delegates the C81 file writer to
  `c81_utils.from_dict.generate_c81` so generated files stay aligned with that
  table format.

## Manifest

Example:

```yaml
output_root: output/polars
report: output/polars/c81_generation_report.json
allow_incomplete: false

worker:
  driver_executable: build/kernel-driver/bin/xfoil_kernel_driver
  session_executable: build/kernel-driver/bin/xfoil_kernel_session
  use_session: true
  runtime_root: runs/c81-generation

airfoils:
  SC1095:
    type: coordinates
    path: data/airfoils/sc1095_selig.dat
  NACA0012:
    naca: "0012"

defaults:
  mach: [0.0, 0.2, 0.4]
  alpha: {start: -8.0, end: 12.0, step: 1.0}
  header_format: commas
  timeout_seconds: 120.0
  retry:
    enabled: true
    initial_sequence: warm_start
    warm_start_alpha_deg: 0.0
    reverse_sequence: true
    single_points: false
    refinement_factors: [0.5, 0.25]
    step_sizes_deg: []
    approach_from: [below, above]
  options:
    viscous: true
    itmax: 100
    panel_count: 180
    ncrit: 9.0
    xtr_top: 1.0
    xtr_bottom: 1.0

tables:
  - id: main_rotor_sc1095
    airfoil: SC1095
    c81_airfoil_id: SC1095
    output_dir: main_rotor
    reynolds: [500000, 1000000, 2000000]

  - id: tail_rotor_naca0012
    airfoil: NACA0012
    c81_airfoil_id: NACA0012
    output_dir: tail_rotor
    reynolds: [300000, 500000, 1000000]
    options:
      xtr_top: 0.8
      xtr_bottom: 0.8
```

Path rules:

- `output_root`, `report`, and coordinate-file paths are relative to the
  manifest file.
- table `output_dir` values are relative to `output_root`.
- `worker.driver_executable`, `worker.session_executable`, and
  `worker.runtime_root` paths are relative to the manifest file when they are
  not absolute. Command-line overrides are resolved relative to the current
  shell.
- `worker.use_session` defaults to `true`, using the persistent compiled
  session so compatible solve requests can reuse XFOIL state. Set it to
  `false`, or pass `--one-shot`, to use the direct-call driver fallback.
- See `examples/c81_naca0012.yaml` for a small runnable manifest.

Alpha grids:

```yaml
alpha: [-4.0, -2.0, 0.0, 2.0, 4.0]
```

or:

```yaml
alpha: {start: -4.0, end: 8.0, step: 2.0}
```

Transition settings:

- `ncrit` controls XFOIL's e^n natural-transition model. It is the normal
  public input because `Ncrit` nominally represents a common freestream
  disturbance environment.
- `ncrit_top` and `ncrit_bottom` may override the common value when intentionally
  modeling asymmetric contamination or matching legacy calibration data.
- `xtr_top` and `xtr_bottom` are XFOIL forced-trip locations. `0.0` forces
  transition at the leading edge. `1.0` places the forced trip at the trailing
  edge, which usually means transition is governed by `Ncrit` before any forced
  trip is reached.

Paneling settings:

- Coordinate airfoils default to `panel: true`, which uses XFOIL's
  `LOAD -> PANGEN` path. `PANGEN` splines the loaded buffer coordinates and
  generates the current airfoil panel nodes from curvature.
- Set `panel: false` only when intentionally running on the supplied coordinate
  points directly through `ABCOPY`.
- `options.panel_count` maps to XFOIL's `NPAN`. Other `PPAR` controls are not
  exposed yet.

## Running

```bash
python xfoil-kernel/scripts/generate_c81.py path/to/c81_manifest.yaml
```

The command uses the persistent compiled session by default. To force the
one-shot driver fallback:

```bash
python xfoil-kernel/scripts/generate_c81.py path/to/c81_manifest.yaml --one-shot
```

XFOIL's viscous solver is path dependent, so the generator does not treat the
first failed ASeq as final. By default it tries:

1. a warm-start sequence beginning near `warm_start_alpha_deg`, usually zero,
   then walking outward through the requested alpha grid,
2. local approach sequences for missing targets on the warm-start side before
   the broad reverse sweep,
3. the reversed warm-start sequence,
4. post-reverse local approach sequences, which are allowed to repeat the same
   alpha schedule because the XFOIL initial guess may now be different,
5. optional cold single-alpha retries when `single_points: true`.

The JSON report records every attempt, the alpha sequence used, completed
target points, missing target points, parsed worker diagnostics,
nonconvergence diagnostics, transcript failure markers, and worker artifacts.
Persistent-session diagnostics include `geometry_changed` and
`options_changed`, which help explain whether a retry reused or reset XFOIL
state. Nonconvergence diagnostics preserve the last failed point row and
operating-condition context when XFOIL emits one. After retries, the command is
still strict by default: if any requested alpha point is missing for any
Mach/Reynolds table, the affected C81 table is not written and the command
exits nonzero.

The default warm-start sequence may repeat the anchor alpha. For example,
`[-2, 0, 2]` with `warm_start_alpha_deg: 0` is attempted as
`[0, 2, 0, -2]`, so both positive and negative branches start from a mild,
usually reliable operating point. Set `initial_sequence: as_requested` to run
the manifest alpha order exactly.

`warm_start_alpha_deg` does not need to be part of the requested table grid.
If the requested grid is `[-2, 2]`, the default warm start still attempts
`[0, 2, 0, -2]`; the extra zero-alpha solves are used only to establish XFOIL's
initial guess and are not written as C81 target points.

To write tables from the common converged alpha points anyway:

```bash
python xfoil-kernel/scripts/generate_c81.py path/to/c81_manifest.yaml --allow-incomplete
```

Use this only after inspecting the report. Missing points near stall are common,
but silent table truncation is not acceptable for a production model.

## Convergence Review

XFOIL is an attached-flow viscous/inviscid tool with path-dependent boundary
layer convergence. A missing point can mean the retry path was poor, but it can
also mean the requested condition is outside the usable range of the airfoil and
model. For example, a highly cambered or high-curvature airfoil can fail near a
negative-stall point when the bottom surface separates.

Recommended workflow:

1. Run strict generation first with `allow_incomplete: false`.
2. Inspect the JSON report when points are missing.
3. Decide whether to narrow the alpha range, adjust the grid near the missing
   point, or accept a truncated common-alpha table.
4. Regenerate with `allow_incomplete: true` only after that review.

The generated report is part of the deliverable. Keep it next to the C81 files
so later users can tell which requested points were solved and which were not.

## Using Generated Tables In A Client Application

Reference the generated files from the client application's airfoil-polar
configuration:

```yaml
airfoil_polars:
  main_rotor_sections:
    type: c81
    bounds_policy: error
    files:
      SC1095:
        500000: polars/main_rotor/SC1095_Re_500000.c81
        1000000: polars/main_rotor/SC1095_Re_1000000.c81
        2000000: polars/main_rotor/SC1095_Re_2000000.c81
```

Then attach the library to a solver input in whatever form the client
application expects. For example:

```yaml
section_model:
  airfoil_polar:
    ref: main_rotor_sections
```

The exact schema belongs to the client application; the important artifact here
is the generated set of C81 files plus the convergence report.
