# Baseline Cases

This directory holds definitions and generated outputs for pristine-XFOIL
regression baselines.

- `cases.json`: curated baseline case definitions.
- `reference/`: compact tracked JSON snapshots promoted from generated runs.
- `pristine/`: suggested output directory for generated XFOIL input decks,
  transcripts, polar files, and parsed JSON summaries. This directory is
  ignored because it is a local run artifact.

Generate scripts without launching XFOIL:

```bash
python xfoil-kernel/scripts/run_pristine_xfoil.py --dry-run
```

Run against a built pristine XFOIL executable:

```bash
python xfoil-kernel/scripts/build_pristine_xfoil.py
python xfoil-kernel/scripts/run_pristine_xfoil.py \
  --xfoil-executable xfoil-kernel/build/pristine-xfoil/bin/xfoil
python xfoil-kernel/scripts/write_reference_baselines.py
```

The runner reports a missing XFOIL executable cleanly and supports `--dry-run`
for generating input decks before the executable is built.

The reference snapshots intentionally distinguish a successful run from a
complete alpha sweep. For example, a viscous case can produce a usable polar
file while omitting one requested alpha because the boundary-layer solve did
not converge there; that point appears in `missing_alpha_deg`.

The tracked reference set includes both simple NACA cases and selected
coordinate-file stress cases. The stress cases exercise non-default panel
counts, thick geometry, high camber, free transition, forced transition, and a
lower Reynolds number before Fortran state modernization work.
