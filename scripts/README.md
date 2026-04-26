# Script Notes

This directory holds helper scripts for pristine-XFOIL baselines, direct-call
kernel-driver checks, and the JSON-lines worker front end.

When the package is installed, prefer the matching console commands declared in
`pyproject.toml` (`xfoil-kernel-build-driver`,
`xfoil-kernel-build-session`, `xfoil-kernel-generate-c81`, and so on). These
scripts remain useful when running directly from a source checkout.

`run_pristine_xfoil.py` generates input decks from `baselines/cases.json`, runs
a built pristine XFOIL executable when available, and parses the resulting
polar save files into JSON summaries.

`build_pristine_xfoil.py` compiles a local pristine-XFOIL executable into
`build/pristine-xfoil/bin/xfoil` using the vendored XFOIL source
tree as read-only input. Set `XFOIL_ROOT` or pass `--xfoil-root` to compare
against another source checkout.

`write_reference_baselines.py` copies generated parsed summaries into compact,
tracked JSON reference files under `baselines/reference/`.

`build_kernel_driver.py` builds `fortran/xfoil_kernel_driver.f` into
`build/kernel-driver/bin/xfoil_kernel_driver`. It compiles the
tracked extracted sources under `fortran/kernel/`, where every Fortran source
file is a selected subprogram extract. The driver build avoids
plotlib/X11 entirely and uses fail-fast prompt stubs for any legacy prompt
branch that should not be reachable from the worker path. Pass
`--refresh-extracted-sources` only when intentionally regenerating
`fortran/kernel/` from the vendored XFOIL source snapshot or another source
checkout selected with `--xfoil-root`.

`run_kernel_driver.py` runs cases from `baselines/cases.json` through the
direct-call driver and writes local run summaries under `runs/kernel-driver/`.

The same build also produces
`build/kernel-driver/bin/xfoil_kernel_session`, a persistent
compiled session that the JSON-lines worker can use with `--use-session`.

`compare_kernel_driver.py` compares those run summaries with the tracked
pristine references and reports max absolute coefficient and transition-location
differences.

`xfoil_worker.py` starts the persistent JSON-lines worker. The current worker
is a Python protocol front end around the persistent compiled session by
default, with `--one-shot` available for the direct-call driver fallback. This
keeps the client-facing protocol stable while the compiled kernel evolves.

`generate_c81.py` runs XFOIL over a YAML-requested airfoil/Re/Mach/alpha grid
and writes C81 files through `c81_utils.from_dict.generate_c81`. It also writes
a JSON convergence report that records missing alpha points and worker
artifacts. See `docs/c81-generation.md`.

The kernel-driver pytest suite includes an optional integration check that
runs these reference comparisons when
`build/kernel-driver/bin/xfoil_kernel_driver` exists.

Recommended baseline data format:

```text
case_id, airfoil_id, re, mach, viscous, alpha_deg, cl, cd, cm, converged
```

Keep raw XFOIL transcripts alongside parsed data so numerical differences can
be diagnosed without rerunning every case.
