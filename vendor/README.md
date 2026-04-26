# Vendored Source

This directory holds third-party source snapshots used to make the kernel build
reproducible and auditable.

## `xfoil/`

`vendor/xfoil/` is a pristine source snapshot of XFOIL 6.99 and its plot
library, copied on 2026-04-25 from the XFOIL source checkout used during kernel
development.

The normal kernel driver/session build uses the tracked selected source tree
under `fortran/kernel/`, not the full vendored XFOIL tree. The vendored snapshot
is retained for provenance, pristine-XFOIL baseline comparisons, and explicit
refreshes of the selected extraction. Developers can override the source tree
with `XFOIL_ROOT=/path/to/xfoil` or the relevant `--xfoil-root` command-line
option when comparing against another XFOIL copy or intentionally refreshing the
extraction.

The snapshot intentionally excludes sample run-output directories and obvious
binary artifacts. Source files, build files, documentation, ORRS data/source,
and plot-library source are retained.
