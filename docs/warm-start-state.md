# XFOIL Warm-Start State

XFOIL's viscous solver is path dependent. This is not just a performance
detail: the next viscous point normally starts from the boundary-layer solution
left by the previous point. The `ASEQ` workflow works because each alpha is a
small continuation step from the last available solution.

## Native XFOIL Behavior

The `OPER` alpha sequence loop in `xoper.f` does the following for each point:

1. Set the requested alpha.
2. Call `SPECAL` to set the inviscid circulation and pressure distribution.
3. Mark the wake or viscous solution stale if alpha/Mach changed.
4. Call `VISCAL`.
5. Leave the converged viscous arrays in COMMON state for the next point.

`VISCAL` only initializes the boundary layer when `LBLINI` is false. If
`LBLINI` is true, the existing boundary-layer arrays become the initial guess
for the new operating point. If `LVCONV` is true, `VISCAL` first rebuilds the
viscous velocities and coefficients from that existing solution before the next
Newton iteration.

The XFOIL manual explicitly describes this behavior: the Newton solve uses the
last available solution as the starting guess, and large jumps should be
handled by reinitializing the boundary layers with `INIT`.

## State To Preserve

The minimum useful warm-start snapshot is the viscous operating-point state
from `XFOIL.INC`:

- operating point identity: `ALFA`, `ADEG`, `AVISC`, `MVISC`, `MINF`, `REINF`,
  `CL`, `CM`, `CD`, `CDP`, `CDF`, `QINF`
- validity flags: `LBLINI`, `LVCONV`, `LWAKE`, `LIPAN`
- wake geometry and identity: `NW`, `AWAKE`, wake portions of `X`, `Y`, `S`,
  `NX`, `NY`, `APANEL`, `WGAP`
- inviscid/viscous velocity fields: `QINV`, `QINV_A`, `QVIS`, `CPV`, `CPI`,
  `UINV`, `UINV_A`, `UEDG`
- BL topology: `IST`, `SST`, `IBLTE`, `NBL`, `IPAN`, `ISYS`, `NSYS`,
  `ITRAN`, `XSSI`, `VTI`
- BL primary unknowns: `MASS`, `THET`, `DSTR`, `CTAU`
- BL derived/output values useful for continuity and diagnostics: `TAU`,
  `DIS`, `CTQ`, `DELT`, `TSTR`
- transition results: `XOCTR`, `YOCTR`, `XSSITR`, `TFORCE`

Some arrays can be recomputed from others, but initially it is safer to cache a
broader snapshot and reduce it later with regression tests. The topology and
wake state matter because a restart from a different stagnation-point indexing
or wake geometry is not the same numerical continuation path.

## Cache Key

A warm-start state is only valid for the same numerical problem. The cache key
should include:

- airfoil geometry identity after paneling, including panel count and paneling
  options
- Reynolds/Mach/type settings
- viscous model settings: `ncrit`, any explicit `ncrit_top` / `ncrit_bottom`
  overrides, `xtr_top`, `xtr_bottom`, `itmax`, `VACCEL`, and BL parameter
  constants
- XFOIL/kernel build identity

Changing any of those should invalidate the warm-start state. Alpha is not part
of the invalidation key; it is metadata used to choose the nearest suitable
state.

## Current Kernel Behavior

The one-shot proof driver preserves warm-start state only inside one
`solve_alpha_sequence` process. The persistent compiled session keeps one XFOIL
COMMON-block state alive across compatible solve requests and invalidates that
state when geometry, panel settings, Reynolds number, Mach number, transition
settings, or viscous mode changes.

The offline C81 generator still treats each retry path as an explicit
continuation sequence: it starts near zero alpha, walks outward, uses local
continuation sequences from already-converged requested points, then may run a
reverse branch and post-reverse local refinements. This makes missing points
visible instead of silently filling them from a numerically unrelated state.

## Future State Handles

If the Python worker needs finer restart control, add explicit state handles:

```text
solve_alpha_sequence(alpha_deg, initial_state_id = null, save_states = true)
save_state(label = null) -> state_id
restore_state(state_id)
drop_state(state_id)
```

The first implementation should keep snapshots in the worker process, not on
disk. A later implementation can serialize snapshots after the state contents
are validated. The C81 generator can then prefer restart paths such as
`0 -> ... -> target`, or `nearest_converged -> ... -> target`, while preserving
the actual XFOIL initial guess instead of launching each retry cold.
