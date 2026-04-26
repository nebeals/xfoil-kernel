# Command-Line Interface

The public command-line entry point is `xfoil-kernel-api`. It wraps the same
`xfoil_kernel` Python client described in `docs/python-api.md`.

Install from the source checkout in editable mode:

```bash
python -m pip install -e ".[test]"
```

Build the kernel executables before running solve or C81-generation commands:

```bash
xfoil-kernel-build-driver
xfoil-kernel-build-session
```

## Commands

Show worker status and capabilities:

```bash
xfoil-kernel-api status
xfoil-kernel-api status --json
```

Solve one alpha point:

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

Generate C81 tables from a YAML manifest:

```bash
xfoil-kernel-api generate-c81 examples/c81_naca0012.yaml
```

Lower-level commands such as `xfoil-kernel-worker` and
`xfoil-kernel-generate-c81` remain available for development and regression
work. Normal users should prefer `xfoil-kernel-api` unless they need to
exercise those internal surfaces directly.

## Common Options

The solve commands accept either `--naca` or `--coordinates-file`. Coordinate
files use XFOIL's `LOAD -> PANGEN` path by default; pass `--no-panel` to use the
supplied points directly.

Solver options mirror `SolveOptions`:

```text
--viscous / --inviscid
--reynolds
--mach
--ncrit
--ncrit-top
--ncrit-bottom
--xtr-top
--xtr-bottom
--itmax
--panel-count
```

Runtime options are:

```text
--driver-executable
--session-executable
--use-session / --one-shot
--runtime-root
--kernel-root
```

Solve commands also accept `--timeout-seconds`. `generate-c81` reads per-table
timeouts from the manifest.

## Exit Codes

- `0`: command completed successfully.
- `1`: setup/protocol failure, or a C81 report with failed tables.
- `2`: valid alpha-sequence solve with missing requested points, or an invalid
  C81-generation request.

The `2` exit code for incomplete alpha sequences preserves the API distinction
between numerical nonconvergence and broken setup. Use `--json` when an
automation script needs the full structured result or report.
