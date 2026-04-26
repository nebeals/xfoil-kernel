# Examples

These files are small, standalone examples for the XFOIL kernel tooling. They
are intentionally modest so they can be used as smoke tests or copied into
larger table-generation workflows.

## Before Running

Install the package in editable mode and build the kernel executables from the
`xfoil-kernel` directory:

```bash
python -m pip install -e ".[test]"
xfoil-kernel-build-driver
xfoil-kernel-build-session
```

If `c81_utils` is not installed, add its parent directory to `PYTHONPATH` or
install it in the environment before running C81 generation.

## Public API Alpha Sequence

Run a small NACA 0012 alpha sequence through the public Python API:

```bash
python examples/solve_alpha_sequence.py
```

The script uses the persistent session executable by default. Use `--one-shot`
to run through the direct-call driver fallback.

The packaged CLI exposes the same public API surface:

```bash
xfoil-kernel-api solve-alpha-sequence --naca 0012 --alpha -4 -2 0 2 4 --reynolds 1000000
```

## Offline C81 Manifest

Run the NACA 0012 C81 example:

```bash
xfoil-kernel-api generate-c81 examples/c81_naca0012.yaml
```

The manifest writes generated files under `runs/examples/naca0012-c81/`, which
is intentionally ignored as generated output. Inspect the JSON report next to
the generated tables before using them in another application.
