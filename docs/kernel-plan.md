# XFOIL Kernel Extraction Plan

## Scope

The kernel exists to provide 2-D section coefficients to downstream clients:

```text
airfoil geometry or NACA id
+ panel settings
+ Reynolds number, Mach number, transition settings, iteration settings
+ alpha sequence
-> cl, cd, cm, transition, convergence diagnostics
```

Everything else in XFOIL is out of scope unless required to support that path.

## Status Summary

- Phase 1 is in place: baseline case definitions, tracked reference summaries,
  parser tests, and optional pristine-XFOIL comparison tooling exist.
- Phase 2 is in place as a first direct-call driver: the namelist executable
  calls the needed XFOIL subroutine path, builds from tracked extracted kernel
  sources, and matches current references within small tolerances when built
  locally.
- Phase 3 is substantially in place for the current driver: extracted XFOIL
  sources redirect stdout chatter, remove one debug-file side effect, remove
  plot initialization, compile only selected XFOIL subroutine blocks, and do not
  compile plotlib/X11. These extracted sources are now tracked under
  `fortran/kernel/`.
- Phase 4 is in place for the alpha-sequence protocol: a JSON-lines Python worker
  exists and supports registered airfoils plus alpha-sequence solves. It uses the
  persistent compiled session by default, keeps XFOIL COMMON-block state alive
  across compatible solve requests, advertises protocol status/capabilities,
  exposes XFOIL's "resetting the boundary layer" operation as
  `reset_boundary_layer_state`, and returns structured errors for timeouts,
  parse failures, invalid raw solve requests, missing executables, and hard
  driver/session failures. Incomplete solves return structured
  nonconvergence diagnostics. Real JSON-lines subprocess tests cover the
  protocol loop, and persistent-session reuse tests cover compatible requests,
  reset behavior, airfoil/coordinate geometry changes, and option invalidation.
  The one-shot driver remains an explicit fallback for isolation and debugging.
- The offline C81 workflow exists: `scripts/generate_c81.py` can run the worker
  over a YAML-requested grid, retry missing points, write a convergence report,
  and emit C81 files through `c81_utils`.
- The public option contract is regression-tested against pristine XFOIL for
  `ncrit`, split `ncrit_top` / `ncrit_bottom`, forced transition, Reynolds
  number, Mach number, and panel count.
- A vendored XFOIL source snapshot exists under `vendor/xfoil/` for pristine
  comparison and provenance. The normal kernel driver/session build now uses
  the tracked selected source tree under `fortran/kernel/`.
- Phase 5 is in place for the current staged package boundary: the public
  `xfoil_kernel` API covers airfoil registration, status/capability queries,
  single-alpha solves, alpha-sequence solves, typed results, C81 generation
  wrappers, examples, docs, and the `xfoil-kernel-api` CLI.
- Phase 6 is complete for the staged extraction boundary: the extracted kernel
  source tree is tracked and used by the normal kernel driver/session build, the
  refresh recipe writes only selected subprograms for every tracked Fortran
  source file in `fortran/kernel/`, and source groups are documented.
- Phase 7 is complete for the current modernization pass: the characterization
  suite now covers refresh reproducibility, one-shot/session equivalence,
  option-change invalidation, warm-start direction, boundary-layer reset
  behavior, stress-airfoil smoke cases, and pristine-reference stress cases.
  The driver and persistent session share a small Fortran core layer for
  setup, option application, geometry loading, boundary-layer reset, and
  alpha-point solves, reducing duplicate direct touches of XFOIL COMMON state.

## Transition Controls

Transition must be part of the kernel's operating-point definition, not an
implementation detail. XFOIL has two relevant controls/outputs:

- `ACRIT(1:2)`: top/bottom critical amplification ratio for free transition
  through the e^n model. The worker-facing input should prefer one common
  `ncrit` scalar and reserve split top/bottom values for deliberate advanced
  cases.
- `XSTRIP(1:2)`: top/bottom forced-transition trip locations. Positive values
  are chordwise `x/c`; values greater than or equal to `1.0` put the forced
  trip at the trailing edge, leaving earlier free transition possible. `0.0`
  forces transition at the leading edge. The worker contract accepts the
  normalized range `[0.0, 1.0]`, with `1.0` representing the trailing-edge
  trip/no earlier forced trip case.
- `XOCTR(1:2)`: top/bottom actual transition `x/c` locations after a solved
  point. This can be upstream of `XSTRIP` if free transition occurs first.

The worker protocol names the forced-trip inputs `xtr_top` and `xtr_bottom`
because those match XFOIL's user-facing `Xtr` / `Xtrip` terminology. Cache keys
and baselines must include these values.

## Phase 1: Pristine-XFOIL Baselines

Create repeatable baselines before changing XFOIL-derived code.

Current cases:

- `NACA0012`, inviscid alpha sweep,
- `NACA0012`, viscous sweep at `Re = 1.0e6` with free transition,
- `NACA0012`, viscous sweep at `Re = 1.0e6` with early forced transition,
- `NACA0012`, one-point option checks for low `ncrit`, split top/bottom
  `ncrit`, lower Reynolds number, nonzero Mach number, and changed panel count,
- `NACA2412`, viscous sweep at `Re = 1.0e6`,
- `SC1095`, viscous coordinate-file rotorcraft baseline,
- a difficult `SC1095` point that currently produces a polar but reports the
  missing non-converged alpha explicitly.

Additional Reynolds numbers can be added once the extracted driver is far
enough along that broader coverage has useful comparison value.

Baseline outputs:

- alpha,
- `cl`, `cd`, `cm`,
- convergence flag,
- requested forced-transition inputs, `xtr_top` and `xtr_bottom`,
- actual transition outputs when available,
- raw transcript for debugging.

The kernel/pristine comparison helper checks transition locations for viscous
cases as well as `cl`, `cd`, and `cm`.

The first extraction target is numerical equivalence with these baselines, not
clean architecture.

## Phase 2: Minimal Non-Interactive Driver

Add a small driver around the existing computational subroutines. The first
version should keep original numerical code as intact as possible.

Intended call sequence:

```text
INIT
LOAD or NACA
PANGEN or ABCOPY
set LVISC, REINF1, MINF1, ACRIT, XSTRIP, ITMAX, RETYP, MATYP
MRCL
COMSET

for each requested alpha:
    set ADEG, ALFA, LALFA, QINF
    SPECAL
    if viscous:
        VISCAL
    collect CL, CD, CM, LVCONV, RMSBL, XOCTR, transition state
```

The driver should solve alpha sequences, not isolated alpha points, because
XFOIL's viscous convergence benefits from warm-started boundary-layer state.

Current status: an initial namelist driver exists at
`fortran/xfoil_kernel_driver.f`. It links against the tracked extracted kernel
source under `fortran/kernel/`, calls the sequence above, and matches the
tracked pristine references to about `5e-5` in `CL`/`CM` and `4e-6` in `CD` for
the current baseline set. Transition locations match pristine polar outputs to
about `5e-5` in the current option/baseline cases. The SC1095 case reports the
same missing `0.0 deg` viscous point as the pristine baseline.

The extracted XFOIL sources redirect internal `WRITE(*)` chatter away from
stdout, the extracted `xoper` block removes VISCAL's hard-coded `.bl`
debug-file dump, the extracted `INIT` block removes plot initialization, and
legacy prompt calls route to fail-fast stubs instead of reading stdin. The
one-shot namelist executable now has a persistent compiled session companion.

## Phase 3: Remove Non-Kernel Side Effects

Remove or stub behavior that is not part of section-coefficient generation:

- plotting calls and plot-library linkage,
- interactive prompt calls,
- polar plotting and hardcopy routines,
- menu dispatch,
- debug scratch-file writes that are not explicitly requested.

When in doubt, preserve numerical state and only remove I/O or presentation
behavior.

## Phase 4: Worker Executable

Expose the kernel through a persistent worker process. The worker should accept
batched commands and keep airfoil/panel state warm between calls.

Responsibilities:

- register and cache airfoils by ID,
- build/rebuild panel state when geometry or panel settings change,
- run alpha sequences,
- report convergence and failure diagnostics,
- avoid interactive prompts,
- avoid process-global cross-talk by using one worker state per process.

Current status: `scripts/xfoil_worker.py` implements the JSON-lines protocol as
a persistent Python front end. It supports airfoil registration, NACA and
coordinate-array inputs, alpha-sequence solves, structured errors, raw solve
request validation, and protocol-only stdout. The default path talks to the
persistent compiled session executable, which preserves geometry/options state
and warm-started viscous state across compatible solve requests. Solve
responses expose parsed `geometry_changed` and `options_changed` diagnostics
from the session header so clients can verify state reuse and invalidation
without scraping transcripts. Use `--one-shot` for the direct-call driver
fallback. The `reset_boundary_layer_state` command deliberately resets the
active persistent session's boundary-layer/wake convergence state without
rebuilding geometry or panels. The `status` command reports protocol version,
active mode, registered airfoils, supported commands, supported solve options,
and the fact that protocol version 1 supports alpha sequences but not CL
sequences. Incomplete alpha solves return `nonconvergence_diagnostics` entries
with reason codes, last point rows, boundary-layer residuals, operating
condition context, and any recognized transcript failure markers.

Coordinate airfoils default to XFOIL's normal `LOAD -> PANGEN` path. Setting
`panel: false` uses `ABCOPY` instead. The current public paneling option is
`panel_count`; the full `PPAR` set of bunching/refinement controls is not yet
exposed. That is intentional for protocol version 1 because full XFOIL's
plotting tools are still the best way to inspect detailed panel clustering.

## Paneling Controls

XFOIL's `PPAR` menu controls the `PANGEN` distribution used to turn the buffer
airfoil geometry into the current panel geometry. The current kernel exposes
only `panel_count`, mapped to XFOIL's `NPAN`.

Other `PPAR` controls available in XFOIL are:

- `CVPAR`: panel bunching parameter. `0` approaches uniform spacing around the
  airfoil; values near `1` strongly attract panel nodes toward high-curvature
  regions. XFOIL's default is `1.0`.
- `CTERAT`: trailing-edge panel density relative to leading-edge panel density.
  Lower values reduce TE clustering relative to the LE. XFOIL's default is
  `0.15`.
- `CTRRAT`: refined-area panel density relative to leading-edge panel density.
  This is used with local refinement zones. XFOIL's default is `0.2`.
- `XSREF1`, `XSREF2`: top/suction-side local refinement `x/c` limits.
- `XPREF1`, `XPREF2`: bottom/pressure-side local refinement `x/c` limits.

The refinement-zone defaults are all `1.0`, so no finite zone is active unless
the user sets a range such as `0.2` to `0.6`. Internally, `PANGEN` adds
fictitious curvature in the requested top or bottom refinement zone so the node
distribution clusters there. If these controls are exposed later, they must be
part of the geometry/panel cache key and persistent-session invalidation
contract.

## Phase 5: Client Interfaces

Client applications should call this kernel through a process boundary or
through a small Python API that preserves the same request/response semantics.

Client behavior:

- cache by airfoil geometry hash, Reynolds number, Mach number, transition
  settings, panel settings, and alpha schedule,
- batch alpha requests whenever possible,
- expand one-off alpha requests into short warm-start sequences when needed,
- raise structured airfoil-data errors when convergence fails.

Near-term package work should continue polishing the Python-facing API around
the existing worker and C81 generator before deeper Fortran modernization. The
lower-level JSON protocol remains useful for regression tests and process
isolation.

Current status: `tools/xfoil_kernel/` exposes the public `xfoil_kernel` import
surface. It wraps the existing worker/session code, keeps incomplete viscous
solves as typed results, raises structured setup/protocol errors, validates the
public request contract before worker calls, and provides manifest and
typed-request wrappers for offline C81 generation. Editable/source-tree package
installation is verified for imports and console entry points. Wheel builds are
currently Python-only and intentionally documented as not yet carrying the full
Fortran/baseline/data/vendor payload needed for build and table-generation
workflows; source-root detection now reports that boundary explicitly. The
examples directory now includes both a manifest-driven C81 workflow and a
public-API alpha-sequence script, with smoke tests that compile the example
scripts and exercise the bundled manifest through a fake worker. The public API
now exposes worker `status()` metadata, and the packaged `xfoil-kernel-api`
CLI provides status, single-alpha, alpha-sequence, and C81-generation commands
over the same client layer. Phase 5 is complete for this staged source-tree
workflow.

## Phase 6: Extracted Kernel Source

Goal: make `fortran/kernel/` the normal build input and reduce it toward a true
narrow kernel while preserving numerical equivalence.

Current status: the normal kernel driver/session build uses tracked extracted
sources and include files under `fortran/kernel/`. `vendor/xfoil/` remains the
pristine source snapshot for baseline comparison, provenance, and explicit
`--refresh-extracted-sources` regeneration. The extraction recipe writes
selected subprograms for every tracked Fortran source file, removing unused
panel helpers, unused spline helpers, `xblsys`'s unused `DIT` helper, and the
unused complex Gaussian solver while preserving the current driver comparison
baselines. The selected sources are grouped as airfoil I/O, geometry,
panel/inviscid solve, and viscous boundary layer in both the build tooling and
`fortran/kernel/README.md`.

Phase 6 is complete for this source-tree staging boundary. Further numerical
restructuring belongs in Phase 7, where the cost and risk of changing XFOIL's
COMMON-block state can be handled deliberately.

## Phase 7: Modernization

Before changing XFOIL's numerical state structure, harden the characterization
suite enough that modernized code can be compared against current behavior.

### Phase 7a: Modernization Safety Harness

Current status: complete for the current modernization pass. The tests verify
that `--refresh-extracted-sources` is deterministic, that the one-shot driver
and persistent session produce equivalent coefficients for representative
inviscid and viscous sequences, that persistent-session option changes match
fresh one-shot solves for forced transition, asymmetric transition, `ncrit`,
Reynolds number, Mach number, and panel-count variants, that ascending and
descending viscous warm-start paths remain within the current numerical
envelope at common alpha points, that resetting the boundary layer reproduces
an isolated viscous point without rebuilding geometry/options, and that the
stress coordinate airfoils can be paneled and solved on a simple inviscid
operating point. The pristine/reference baseline set also includes selected
stress-airfoil cases: a thick coordinate-file NACA 0024 inviscid sweep with
non-default panel count, an S102 viscous free-transition case, and a low-Re
forced-transition S1223 case.

### Phase 7b: State Modernization

Current status: complete for the current modernization pass. The one-shot
driver and persistent session now call shared routines in
`fortran/xfoil_kernel_core.f` for plotting suppression, default case setup,
case validation, geometry loading, option application, boundary-layer reset,
operating-point preparation, and alpha-point solving. This keeps XFOIL's
original COMMON-backed numerical state intact, but gives future refactors one
smaller boundary to change and removes duplicate state-touching code from the
two executable front ends.

A full replacement of XFOIL COMMON blocks with an explicit kernel state object
is intentionally not part of this pass. It would touch most numerical routines,
so it should only be attempted as a separate major refactor after deciding that
direct in-process library bindings or true reentrancy are worth the risk.

## Future Areas

- Add CL-sequence solving through XFOIL's `SPECCL` path. This is deliberately
  deferred because alpha sequences are the common C81/offline-table workflow and
  CL-prescribed operation is much less used. A future protocol version could add
  `solve_cl_sequence` while keeping protocol version 1 alpha-only.
- Replace XFOIL COMMON-block state with an explicit kernel state object if
  future direct library bindings or reentrant multi-state solves justify the
  numerical refactor risk.
- Decide on a relocatable wheel strategy for the Fortran, baseline, data, and
  vendored-source payload. Current wheel builds intentionally expose the Python
  API and console entry points only; build and C81 workflows require an
  editable/source-tree install or `XFOIL_KERNEL_ROOT`.
- Consider emitting a regenerated YAML manifest from typed C81 requests if
  users start relying on Python-built requests as reviewable artifacts.
