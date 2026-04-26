# Python API

This document describes the first public Python API for the kernel. The API is
a thin facade over the existing worker/session/C81 machinery; the lower-level
JSON worker remains the process boundary and regression target.

Normal users should not need to assemble Fortran namelists, JSON-lines payloads,
or script-only entry points for common workflows.

## Design Goals

- Keep the public API small and explicit.
- Prefer alpha sequences over isolated points, while still making single-alpha
  calls convenient.
- Preserve XFOIL convergence diagnostics. Do not fabricate missing polar
  points.
- Make transition, Reynolds number, Mach number, panel settings, and iteration
  settings part of the request identity.
- Support both online coefficient queries and offline C81 table generation.
- Keep the package usable as a standalone project.

## Installation Boundary

The current package supports editable/source-tree installs:

```bash
python -m pip install -e ".[test]"
```

The public `xfoil_kernel` API and console entry points can be imported from a
normal wheel install, but the wheel does not yet bundle the full Fortran,
baseline, data, and vendored XFOIL source payload. Workflows that build the
kernel executables, compare baselines, or generate C81 tables should run from
an editable/source-tree install or set `XFOIL_KERNEL_ROOT` to the source
checkout. This keeps the current package boundary honest until a relocatable
wheel strategy is chosen.

## Public Import Surface

The public module is `xfoil_kernel`. The `xfoil_kernel_tools` package
contains lower-level build, driver, baseline, and worker support code. Normal
users should not need to import it directly. Names imported from
`xfoil_kernel` are the supported public API; `xfoil_kernel_tools` should be
treated as internal support unless a lower-level development workflow
explicitly requires it.

Top-level exports:

```python
from xfoil_kernel import (
    __version__,
    AirfoilRegistrationError,
    AirfoilSpec,
    AlphaSequenceResult,
    C81GenerationError,
    C81GenerationRequest,
    C81GenerationResult,
    IncompleteSolveError,
    KernelConfig,
    KernelError,
    KernelExecutableNotFound,
    KernelProtocolError,
    PointResult,
    RetryPolicy,
    SolveOptions,
    XfoilKernelClient,
    generate_c81,
    generate_c81_from_manifest,
)
```

`__version__` mirrors the installed package version when package metadata is
available and falls back to the source-tree version when running directly
from a checkout.

## Configuration

`KernelConfig` describes executable paths and runtime behavior.

```python
from pathlib import Path

from xfoil_kernel import KernelConfig

config = KernelConfig(
    session_executable=Path("build/kernel-driver/bin/xfoil_kernel_session"),
    driver_executable=Path("build/kernel-driver/bin/xfoil_kernel_driver"),
    runtime_root=Path("runs/api"),
    use_session=True,
    timeout_seconds=120.0,
)
```

Fields:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `session_executable` | `Path | None` | build default | Persistent compiled session executable. |
| `driver_executable` | `Path | None` | build default | One-shot direct driver executable. |
| `runtime_root` | `Path | None` | package run default | Directory for generated namelists, transcripts, and summaries. |
| `use_session` | `bool` | `True` | Prefer the persistent session path. |
| `timeout_seconds` | `float` | `120.0` | Default timeout for one solve request. |

The session path should be the default because XFOIL's viscous state is useful
across compatible solve requests. The one-shot driver remains valuable for
debugging, isolation, and comparison.

## Airfoils

`AirfoilSpec` describes either a NACA designation or coordinates.

```python
from xfoil_kernel import AirfoilSpec

naca0012 = AirfoilSpec.naca("0012")

custom = AirfoilSpec.coordinates_file(
    "data/airfoils/custom.dat",
    panel=True,
)

inline = AirfoilSpec.coordinates(
    x=[1.0, 0.5, 0.0, 0.5, 1.0],
    y=[0.0, 0.05, 0.0, -0.05, 0.0],
    panel=True,
)
```

Fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `kind` | `"naca" | "coordinates"` | Airfoil source type. |
| `code` | `str | None` | NACA code when `kind == "naca"`. |
| `path` | `Path | None` | Coordinate file path. |
| `x`, `y` | `Sequence[float] | None` | Inline coordinates. |
| `panel` | `bool` | For coordinate airfoils, `True` means `LOAD -> PANGEN`; `False` means `ABCOPY`. |

Coordinate input is expected in XFOIL-style airfoil order. The API should not
silently reorder or repair arbitrary point clouds. If later geometry utilities
are added, they should be separate explicit preprocessing functions.

Validation:

- NACA codes must be non-empty positive digit strings.
- Inline coordinate arrays must be finite numeric sequences with the same
  length and at least three points.
- `panel` must be a boolean.

## Solve Options

`SolveOptions` maps directly to the current worker options.

```python
from xfoil_kernel import SolveOptions

options = SolveOptions(
    viscous=True,
    reynolds_number=1_000_000.0,
    mach_number=0.10,
    ncrit=9.0,
    xtr_top=1.0,
    xtr_bottom=1.0,
    itmax=100,
    panel_count=180,
)
```

Fields:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `viscous` | `bool` | `True` | Run the viscous boundary-layer solve. |
| `reynolds_number` | `float | None` | `None` | Reynolds number. Required for online viscous solves; C81 grids may supply it per run. |
| `mach_number` | `float` | `0.0` | Freestream Mach number. |
| `ncrit` | `float | None` | `9.0` | Common e^n transition critical amplification value. |
| `ncrit_top` | `float | None` | `None` | Advanced top-surface override. |
| `ncrit_bottom` | `float | None` | `None` | Advanced bottom-surface override. |
| `xtr_top` | `float` | `1.0` | Forced-transition trip location on the top surface. |
| `xtr_bottom` | `float` | `1.0` | Forced-transition trip location on the bottom surface. |
| `itmax` | `int` | `50` | Viscous iteration limit. |
| `panel_count` | `int` | `160` | Requested generated panel count, mapped to `NPAN`. |

Transition notes:

- `xtr_top=0.0` or `xtr_bottom=0.0` forces transition at the leading edge.
- `xtr_top=1.0` or `xtr_bottom=1.0` puts the forced trip at the trailing edge,
  so natural transition can occur earlier through `Ncrit`.
- `ncrit_top` and `ncrit_bottom` are advanced overrides. Most users should set
  only `ncrit`.

Validation:

- Online viscous solves require `reynolds_number`. Offline C81 generation may
  omit it in `SolveOptions` because the C81 Reynolds grid supplies it per run.
- Numeric values must be finite.
- `reynolds_number`, `ncrit`, `ncrit_top`, and `ncrit_bottom` must be positive
  when supplied.
- `mach_number` must be non-negative.
- `xtr_top` and `xtr_bottom` must be in `[0.0, 1.0]`.
- `itmax` must be a positive integer.
- `panel_count` must be an integer greater than one.

## Client Lifecycle

`XfoilKernelClient` owns one worker/session state. It should be usable as a
context manager and should close the persistent process when leaving the
context.

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
        ),
    )
```

Construction:

```python
client = XfoilKernelClient(config=KernelConfig(...))
```

Methods:

```python
client.register_airfoil(
    airfoil_id: str,
    airfoil: AirfoilSpec,
) -> None

client.solve_alpha_sequence(
    airfoil_id: str,
    *,
    alpha_deg: Sequence[float],
    options: SolveOptions,
    timeout_seconds: float | None = None,
) -> AlphaSequenceResult

client.solve_alpha(
    airfoil_id: str,
    *,
    alpha_deg: float,
    options: SolveOptions,
    warm_start: bool | Sequence[float] = True,
    timeout_seconds: float | None = None,
) -> PointResult

client.reset_boundary_layer_state(
    *,
    timeout_seconds: float | None = None,
) -> Mapping[str, Any]

client.status() -> Mapping[str, Any]

client.close() -> None
```

`solve_alpha` is a convenience method. For nonzero viscous solves, the default
`warm_start=True` expands the request to `[0.0, alpha]`. Set
`warm_start=False` to submit only the requested alpha. Pass an explicit
sequence when the approach path matters; explicit sequences must include the
requested alpha.

`alpha_deg` and any explicit warm-start sequence values must be finite numbers.
`timeout_seconds`, when supplied, must be positive and finite.

`reset_boundary_layer_state` exposes XFOIL's "resetting the boundary layer"
operation. In persistent-session mode it discards the current boundary-layer
and wake convergence state without rebuilding geometry or panels. If no session
is active yet, or if the client is configured for one-shot mode, the call
succeeds as a no-op and returns `reset_performed: false`.

```python
with XfoilKernelClient() as client:
    client.register_airfoil("naca0012", AirfoilSpec.naca("0012"))
    client.solve_alpha_sequence(
        "naca0012",
        alpha_deg=[0.0, 2.0, 4.0],
        options=SolveOptions(viscous=True, reynolds_number=1_000_000.0),
    )
    reset = client.reset_boundary_layer_state()
    print(reset["reset_performed"])
```

`status()` queries the worker without solving a point. It returns the protocol
version, active mode, registered airfoil ids, and capability metadata such as
supported commands and solve options. This is useful for setup checks and CLI
diagnostics.

```python
with XfoilKernelClient() as client:
    status = client.status()
    print(status["capabilities"]["commands"])
```

## Result Objects

`PointResult` represents one operating point.

```python
point.alpha_deg
point.cl
point.cd
point.cm
point.cdp
point.converged
point.rms_bl
point.xtr_top
point.xtr_bottom
point.transition_forced_top
point.transition_forced_bottom
```

`AlphaSequenceResult` represents one requested alpha sequence.

```python
result.ok
result.complete
result.requested_alpha_deg
result.converged_alpha_deg
result.missing_alpha_deg
result.points
result.diagnostics
result.nonconvergence_diagnostics
result.failure_markers
result.artifacts
```

Convenience methods:

```python
result.require_complete() -> AlphaSequenceResult
result.point_at(alpha_deg: float, *, require_converged: bool = True) -> PointResult
result.to_dict() -> dict
```

`require_complete()` raises `IncompleteSolveError` when any requested alpha
point is missing. It does not convert incomplete numerical results into
protocol errors internally; it is a caller convenience for workflows that
require a full grid.

`result.diagnostics` contains parsed kernel header metadata. In persistent
session mode this includes `geometry_changed` and `options_changed`, which show
whether the session reused existing geometry/options state or rebuilt/reset it
for the request.

`result.nonconvergence_diagnostics` contains one structured entry for each
requested alpha without a converged result. Entries preserve the last point row
when XFOIL emitted one: reason code, message, `rms_bl`, last `cl`/`cd`/`cm`,
actual transition locations, requested operating condition, panel count, and
the effective `VISCAL` iteration limit. `result.failure_markers` contains
recognized transcript-level markers such as `VISCAL: Convergence failed` when
they are visible in kernel output.

## Single-Alpha Example

```python
from xfoil_kernel import AirfoilSpec, SolveOptions, XfoilKernelClient

options = SolveOptions(
    viscous=True,
    reynolds_number=1_000_000.0,
    mach_number=0.0,
    ncrit=9.0,
    xtr_top=1.0,
    xtr_bottom=1.0,
)

with XfoilKernelClient() as client:
    client.register_airfoil("naca0012", AirfoilSpec.naca("0012"))
    point = client.solve_alpha("naca0012", alpha_deg=4.0, options=options)

print(point.cl, point.cd, point.cm)
```

## Alpha-Sequence Example

```python
from xfoil_kernel import AirfoilSpec, SolveOptions, XfoilKernelClient

with XfoilKernelClient() as client:
    client.register_airfoil(
        "custom",
        AirfoilSpec.coordinates_file("data/airfoils/custom.dat", panel=True),
    )

    result = client.solve_alpha_sequence(
        "custom",
        alpha_deg=[0.0, 2.0, 4.0, 6.0, 8.0],
        options=SolveOptions(
            viscous=True,
            reynolds_number=750_000.0,
            mach_number=0.05,
            itmax=100,
        ),
    )

    if not result.complete:
        print("Missing:", result.missing_alpha_deg)

    for point in result.points:
        if point.converged:
            print(point.alpha_deg, point.cl, point.cd, point.cm)
```

## Command-Line API

The `xfoil-kernel-api` console script wraps the same public Python client for
quick setup checks and one-off solves:

```bash
xfoil-kernel-api status
xfoil-kernel-api status --json
```

Single alpha:

```bash
xfoil-kernel-api solve-alpha \
  --naca 0012 \
  --alpha 4 \
  --reynolds 1000000 \
  --mach 0.0 \
  --ncrit 9.0 \
  --xtr-top 1.0 \
  --xtr-bottom 1.0
```

Alpha sequence:

```bash
xfoil-kernel-api solve-alpha-sequence \
  --naca 0012 \
  --alpha -4 -2 0 2 4 \
  --reynolds 1000000 \
  --panel-count 180
```

C81 generation:

```bash
xfoil-kernel-api generate-c81 examples/c81_naca0012.yaml
```

The solve commands accept `--coordinates-file`, `--one-shot`, executable path
overrides, transition options, and `--json`. `generate-c81` accepts worker path
overrides, `--allow-incomplete`, and `--json`. See `docs/cli.md` for the full
CLI contract and exit codes. `solve-alpha-sequence` exits with status `2` when
the worker response is valid but incomplete, preserving the same distinction
the Python API makes between numerical nonconvergence and protocol/setup
errors.

## Errors And Incomplete Results

Protocol or setup failures raise `KernelError` or a more specific subclass:

```python
class KernelError(RuntimeError): ...
class KernelExecutableNotFound(KernelError): ...
class KernelProtocolError(KernelError): ...
class AirfoilRegistrationError(KernelError): ...
class IncompleteSolveError(KernelError): ...
```

Behavior:

- invalid inputs raise before calling the worker,
- missing executables raise `KernelExecutableNotFound`,
- worker `ok: false` responses raise `KernelProtocolError` or a specific
  subclass,
- viscous nonconvergence at requested alpha points returns an
  `AlphaSequenceResult` with `complete=False`,
- `result.require_complete()` raises `IncompleteSolveError`.

This keeps numerical nonconvergence visible without making it indistinguishable
from a broken executable or malformed request.

## Retry Policy

The online API should keep retry behavior modest. Expensive retry strategies
belong primarily in offline table generation, where the user can inspect the
report and decide how to handle missing points.

`RetryPolicy` is available for C81 generation and optional advanced sequence
solves:

```python
from xfoil_kernel import RetryPolicy

retry = RetryPolicy(
    enabled=True,
    initial_sequence="warm_start",
    warm_start_alpha_deg=0.0,
    reverse_sequence=True,
    single_points=False,
    refinement_factors=[0.5, 0.25],
    step_sizes_deg=[],
    approach_from=["below", "above"],
)
```

Normal `solve_alpha_sequence` calls submit the alpha sequence requested by the
caller. The broader retry strategy currently belongs to C81 generation.

## Offline C81 API

The C81 API exposes both a manifest loader and a typed request builder. The
manifest workflow remains first-class because it is easy to review, version,
and rerun.

Manifest entry point:

```python
from xfoil_kernel import generate_c81_from_manifest

report = generate_c81_from_manifest(
    "examples/c81_naca0012.yaml",
    use_session=True,
)

if not report.ok:
    print(report.report_file)
```

Typed request entry point:

```python
from pathlib import Path

from xfoil_kernel import (
    AirfoilSpec,
    C81GenerationRequest,
    RetryPolicy,
    SolveOptions,
    XfoilKernelClient,
    generate_c81,
)

request = C81GenerationRequest(
    output_root=Path("output/polars"),
    report_file=Path("output/polars/report.json"),
    allow_incomplete=False,
    airfoils={
        "NACA0012": AirfoilSpec.naca("0012"),
    },
    tables=[
        {
            "id": "naca0012_demo",
            "airfoil": "NACA0012",
            "c81_airfoil_id": "NACA0012",
            "output_dir": "tables",
            "reynolds": [1_000_000.0],
            "mach": [0.0, 0.2],
            "alpha_deg": [-4.0, -2.0, 0.0, 2.0, 4.0, 6.0, 8.0],
            "options": SolveOptions(
                viscous=True,
                itmax=100,
                panel_count=180,
                ncrit=9.0,
                xtr_top=1.0,
                xtr_bottom=1.0,
            ),
            "retry": RetryPolicy.default(),
        },
    ],
)

with XfoilKernelClient() as client:
    report = generate_c81(client, request)
```

`generate_c81_from_manifest` returns the same `C81GenerationResult` type as the
typed API. It continues to require optional `c81_utils` only when C81 files are
actually written.

## C81 Generation Result

`C81GenerationResult` exposes the same information currently written to the JSON
report:

```python
report.ok
report.report_file
report.output_root
report.allow_incomplete
report.tables
report.written_files
report.to_dict()
```

Each table report should include:

- requested Reynolds numbers, Mach numbers, and alpha grid,
- every attempt sequence,
- missing alpha points after each attempt,
- final common-alpha set written to each C81 table,
- written file paths,
- worker artifacts for traceability.

## Caching Contract

The first implementation relies on the worker/session state and filesystem
artifacts. The public API still defines the cache identity so later versions can
add stronger caching without changing user code.

Cache keys should include:

- airfoil geometry hash or NACA code,
- coordinate paneling choice,
- `panel_count`,
- `viscous`,
- Reynolds number,
- Mach number,
- `ncrit` or split `ncrit_top` / `ncrit_bottom`,
- `xtr_top` and `xtr_bottom`,
- alpha sequence and sequence order,
- kernel executable version or source/build fingerprint.

Do not reuse a viscous warm state across incompatible geometry, panel,
Reynolds, Mach, transition, or viscous-option changes.

## Serialization

Public objects support conversion to plain Python data where it is currently
needed:

```python
payload = result.to_dict()
manifest_payload = request.to_manifest_dict()
```

The plain-data shape should be compatible with the JSON worker protocol where
possible and with the existing YAML C81 manifest where practical. This makes it
straightforward to move between Python calls, JSON-lines regression tests, and
reviewable YAML workflows.

## Deliberately Not In Scope

The first API should not expose:

- inverse design,
- geometry editing commands such as `ADDP`, `CORN`, `DELP`, or `MOVP`,
- full `PPAR` controls beyond `panel_count`,
- plotting or hardcopy output,
- direct Fortran library bindings,
- automatic correction of badly ordered coordinate files.

These can be considered later if they are required by real workflows.

## First Implementation Choices

- The stable import package is `xfoil_kernel`.
- `solve_alpha` defaults to `[0.0, alpha]` for nonzero viscous solves.
- `reset_boundary_layer_state` keeps XFOIL's user-facing "resetting the
  boundary layer" language and resets persistent BL/wake convergence state
  without rebuilding geometry.
- Result response fields retain `xtr_top` / `xtr_bottom` for actual transition
  locations to match current worker output and XFOIL polar terminology.
- `generate_c81` accepts plain dictionaries for table specs in the first
  implementation.
- The typed C81 API writes the JSON convergence report. It does not yet write a
  regenerated YAML manifest.
