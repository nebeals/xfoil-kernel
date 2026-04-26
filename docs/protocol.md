# Worker Protocol

The first worker protocol is line-delimited JSON. JSON is easy to inspect,
easy to regression-test, and fast enough if requests are batched. The encoding
can later move to MessagePack, CBOR, or a binary frame without changing
the airfoil-polar interface exposed to client applications.

The current implementation is `scripts/xfoil_worker.py`, a Python persistent
worker around either the one-shot direct-call Fortran driver or the persistent
compiled session executable. The persistent session is the default worker mode
because it preserves compatible XFOIL state between solves. Use `--one-shot`
to force the direct-call driver fallback. This keeps JSON handling in Python
while the compiled kernel architecture evolves.

Each request is one JSON object on one line. Each response is one JSON object
on one line. Include `request_id` when the client wants to correlate responses.

## Status

The `status` command reports protocol metadata, active mode, registered
airfoils, and advertised capabilities. Clients can use this to verify that the
worker supports the commands and solve options they plan to use.

Request:

```json
{"request_id":"s1","cmd":"status"}
```

Response:

```json
{
  "request_id": "s1",
  "ok": true,
  "protocol_version": 1,
  "implementation": "python-json-lines",
  "mode": "session",
  "session_active": false,
  "registered_airfoils": [],
  "runtime_root": "runs/worker",
  "driver_executable": "build/kernel-driver/bin/xfoil_kernel_driver",
  "session_executable": "build/kernel-driver/bin/xfoil_kernel_session",
  "capabilities": {
    "commands": [
      "ping",
      "status",
      "register_airfoil",
      "reset_boundary_layer_state",
      "solve_alpha_sequence",
      "shutdown"
    ],
    "airfoil_types": ["naca", "coordinates"],
    "sequence_types": ["alpha"],
    "solve_options": [
      "viscous",
      "reynolds_number",
      "mach_number",
      "ncrit",
      "ncrit_top",
      "ncrit_bottom",
      "xtr_top",
      "xtr_bottom",
      "itmax",
      "panel_count"
    ],
    "persistent_session": true,
    "cl_sequence": false
  }
}
```

## Register A NACA Airfoil

Request:

```json
{"request_id":"r1","cmd":"register_airfoil","airfoil_id":"naca0012","naca":"0012"}
```

Response:

```json
{"request_id":"r1","ok":true,"airfoil_id":"naca0012"}
```

## Register Coordinate Airfoil

Coordinates are normalized chord coordinates unless otherwise specified. By
default coordinate airfoils use XFOIL's `LOAD -> PANGEN` path. Set
`panel: false` to use `ABCOPY` and run directly on the supplied coordinates.
The `airfoil` object form can reference a file path or inline coordinate
arrays.

Request:

```json
{
  "request_id": "r2",
  "cmd": "register_airfoil",
  "airfoil_id": "custom",
  "airfoil": {
    "type": "coordinates",
    "path": "data/airfoils/custom.dat",
    "panel": true
  }
}
```

Response:

```json
{"request_id":"r2","ok":true,"airfoil_id":"custom","geometry_path":"..."}
```

## Solve Alpha Sequence

Request:

```json
{
  "request_id": "r3",
  "cmd": "solve_alpha_sequence",
  "airfoil_id": "naca0012",
  "options": {
    "reynolds_number": 1200000.0,
    "mach_number": 0.12,
    "viscous": true,
    "ncrit": 9.0,
    "xtr_top": 1.0,
    "xtr_bottom": 1.0,
    "itmax": 40,
    "panel_count": 160
  },
  "alpha_deg": [-4.0, -2.0, 0.0, 2.0, 4.0]
}
```

Response:

```json
{
  "request_id": "r3",
  "ok": true,
  "airfoil_id": "naca0012",
  "complete": true,
  "requested_alpha_deg": [-4.0, -2.0, 0.0, 2.0, 4.0],
  "converged_alpha_deg": [-4.0, -2.0, 0.0, 2.0, 4.0],
  "missing_alpha_deg": [],
  "nonconvergence_diagnostics": [],
  "failure_markers": [],
  "diagnostics": {
    "schema": 1,
    "version": 6.99,
    "n_panels": 160,
    "viscous": true,
    "reynolds": 1200000.0,
    "mach": 0.12,
    "ncrit_top": 9.0,
    "ncrit_bottom": 9.0,
    "xtr_top": 1.0,
    "xtr_bottom": 1.0,
    "geometry_changed": false,
    "options_changed": false
  },
  "points": [
    {
      "alpha_deg": 0.0,
      "cl": 0.0,
      "cd": 0.0085,
      "cdp": 0.0012,
      "cm": -0.001,
      "converged": true,
      "rms_bl": 0.00001,
      "xtr_top": 0.72,
      "xtr_bottom": 0.68,
      "transition_forced_top": false,
      "transition_forced_bottom": false
    }
  ],
  "artifacts": {
    "case_id": "naca0012_000001_abcd1234",
    "input_file": "...",
    "transcript_file": "..."
  }
}
```

`diagnostics.geometry_changed` and `diagnostics.options_changed` are reported
by the persistent session. They let clients verify whether the solve reused the
existing airfoil/panel/options state or rebuilt/reset it. The one-shot driver
can report header settings such as panel count and operating options, but
state-reuse flags are only meaningful in persistent-session mode.

## Reset Boundary-Layer State

`reset_boundary_layer_state` exposes XFOIL's "resetting the boundary layer"
operation for callers that want to deliberately discard the current
boundary-layer/wake convergence state without rebuilding the airfoil geometry
or paneling.

Request:

```json
{"request_id":"b1","cmd":"reset_boundary_layer_state"}
```

Response when a persistent session is active:

```json
{
  "request_id": "b1",
  "ok": true,
  "mode": "session",
  "reset_performed": true,
  "message": "XK_OK reset_boundary_layer_state"
}
```

If no persistent session is active yet, the command succeeds as a no-op:

```json
{
  "request_id": "b1",
  "ok": true,
  "mode": "session",
  "reset_performed": false,
  "reason": "no_active_session"
}
```

In one-shot mode the command also succeeds as a no-op because each solve already
uses a fresh process:

```json
{
  "request_id": "b1",
  "ok": true,
  "mode": "one_shot",
  "reset_performed": false,
  "reason": "one_shot_mode_has_no_persistent_boundary_layer_state"
}
```

## Supported Options

`solve_alpha_sequence.options` currently supports:

| Option | Meaning |
| --- | --- |
| `viscous` | `true` runs XFOIL's viscous `VISCAL` path; `false` runs the inviscid path. |
| `reynolds_number` | Reynolds number for `REINF1`; required for viscous solves. |
| `mach_number` | Mach number for `MINF1`; defaults to `0.0`. |
| `ncrit` | Common e^n transition critical amplification value. |
| `ncrit_top`, `ncrit_bottom` | Advanced top/bottom overrides for `ACRIT(1:2)`. |
| `xtr_top`, `xtr_bottom` | Forced-transition trip locations passed to `XSTRIP(1:2)`. |
| `itmax` | Viscous iteration limit. |
| `panel_count` | Requested generated panel nodes, mapped to `NPAN`. |

`panel` is an airfoil-registration option rather than a solve option. For
coordinate airfoils, `panel: true` is the default and uses `PANGEN`; `panel:
false` uses `ABCOPY`.

Raw worker requests are validated before a driver or persistent session solve
is attempted. `alpha_deg` values must be finite numbers. `viscous` must be a
JSON boolean. Viscous solves require a positive `reynolds_number`.
`mach_number` must be finite and non-negative. `ncrit`, `ncrit_top`, and
`ncrit_bottom` must be positive finite numbers. `xtr_top` and `xtr_bottom`
must be finite values in `[0.0, 1.0]`. `itmax` must be a positive integer, and
`panel_count` must be an integer greater than one. Unknown solve option keys
are rejected with `invalid_request`.

## Error Response

Errors should be structured enough for a client application to decide whether
to fail the analysis, retry with a different alpha schedule, or fall back to
another polar source. Protocol errors, invalid requests, missing executables,
timeouts, malformed driver output, and hard driver/session failures use
`ok: false`.

```json
{
  "request_id": "r4",
  "ok": false,
  "error": {
    "code": "driver_failed",
    "message": "XK_ERROR itmax must be positive",
    "case_id": "naca0012_000006_74e26bd9",
    "returncode": null
  }
}
```

Current worker error codes include:

| Code | Meaning |
| --- | --- |
| `invalid_json` | Input line was not valid JSON. |
| `invalid_request` | Request shape or field values were invalid. |
| `missing_field` | A required field was absent. |
| `unknown_command` | The command is not supported by this protocol version. |
| `unknown_airfoil` | The requested airfoil has not been registered. |
| `empty_alpha_sequence` | `alpha_deg` was empty. |
| `driver_not_found` | The configured driver or session executable was missing. |
| `driver_timeout` | The driver or persistent session timed out. |
| `driver_output_parse_failed` | The worker could not parse kernel result rows. |
| `driver_failed` | The driver/session rejected the case or exited unexpectedly. |
| `internal_error` | Unexpected worker-side failure. |

## Incomplete Solve Response

Viscous nonconvergence at individual alpha points is not a protocol error.
The worker returns a normal response with `ok: true`, `complete: false`, and
the missing target alphas. This lets callers distinguish "the request was
valid, but XFOIL did not converge everywhere" from a malformed request or
failed executable.

```json
{
  "request_id": "r5",
  "ok": true,
  "airfoil_id": "custom",
  "complete": false,
  "requested_alpha_deg": [-8.0, -4.0, 0.0, 4.0],
  "converged_alpha_deg": [-4.0, 0.0, 4.0],
  "missing_alpha_deg": [-8.0],
  "nonconvergence_diagnostics": [
    {
      "index": 1,
      "requested_alpha_deg": -8.0,
      "alpha_deg": -8.0,
      "reason": "viscous_nonconvergence",
      "message": "Viscous boundary-layer solve did not report convergence for the requested alpha.",
      "rms_bl": 0.0142,
      "cl": -0.58,
      "cd": 0.082,
      "cm": 0.12,
      "cdp": 0.074,
      "actual_xtr_top": 0.81,
      "actual_xtr_bottom": 0.02,
      "transition_forced_top": false,
      "transition_forced_bottom": false,
      "viscous": true,
      "reynolds_number": 1000000.0,
      "mach_number": 0.2,
      "ncrit_top": 9.0,
      "ncrit_bottom": 9.0,
      "requested_xtr_top": 1.0,
      "requested_xtr_bottom": 1.0,
      "panel_count": 180,
      "itmax": 80,
      "viscal_iteration_limit": 85
    }
  ],
  "failure_markers": [
    {
      "code": "viscous_nonconvergence",
      "message": "VISCAL:  Convergence failed"
    }
  ],
  "points": [
    {
      "alpha_deg": -4.0,
      "cl": -0.12,
      "cd": 0.011,
      "cm": -0.07,
      "converged": true
    }
  ]
}
```

`nonconvergence_diagnostics` records one structured entry for each requested
alpha without a converged result. When XFOIL emits a nonconverged point row, the
entry preserves the last coefficients, boundary-layer RMS residual, actual
transition locations, requested operating condition, and the effective
`VISCAL` iteration limit. If no point row is emitted, the reason is
`no_result_row`. `failure_markers` contains recognized transcript-level markers
when they are visible in the kernel output.

## Protocol Notes

- Prefer sequences over single points.
- Keep one worker process per independent XFOIL state. The Python worker uses
  the persistent compiled session by default to keep airfoil/panel and
  boundary-layer state warm behind the same JSON protocol. The one-shot direct
  driver remains available for isolation and debugging.
- The current worker supports alpha sequences only. CL-sequence solving is not
  part of protocol version 1.
- Treat options as part of the cache key, especially `ncrit`, any explicit
  `ncrit_top` / `ncrit_bottom` overrides, `xtr_top`, `xtr_bottom`,
  `reynolds_number`, `mach_number`, `panel_count`, and `panel`.
- Keep raw worker logs available for debugging, but do not require transcript
  scraping for normal results.
- Do not fabricate missing polar points. Let clients or offline generators
  decide whether to narrow the requested range, retry with a different path, or
  accept a truncated result after review.

## Transition Field Names

`ncrit` is the common e^n critical amplification value passed to XFOIL.
XFOIL also supports separate top/bottom values (`ncrit_top` and
`ncrit_bottom`) through its `NT` and `NB` commands, but the common scalar is the
normal input because `Ncrit` nominally represents the freestream disturbance
environment.

`xtr_top` and `xtr_bottom` in request `options` are the forced-transition trip
locations passed to XFOIL's `XSTRIP(1)` and `XSTRIP(2)`. They are inputs, not
necessarily the actual transition locations. A value of `0.0` forces transition
at the leading edge. A value of `1.0` puts the forced trip at the trailing edge,
which usually means transition is allowed to occur naturally through `Ncrit`
before the trip is reached.

Per-point response `xtr_top` and `xtr_bottom` are the actual transition
locations returned from XFOIL's `XOCTR(1)` and `XOCTR(2)`. If this ambiguity
becomes annoying in code, split the response names into
`actual_xtr_top` / `actual_xtr_bottom` while retaining the request names for
the forced-trip settings.
