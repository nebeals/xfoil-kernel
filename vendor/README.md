# Vendored Source

This directory holds third-party source snapshots used to make the kernel build
reproducible.

## `xfoil/`

`vendor/xfoil/` is a pristine source snapshot of XFOIL 6.99 and its plot
library, copied on 2026-04-25 from the local XFOIL source checkout used during
kernel development.

The snapshot is used as the default build input for the direct-call kernel
driver. Users and developers can still override the source tree with
`XFOIL_ROOT=/path/to/xfoil` or the relevant `--xfoil-root` command-line option
when comparing against another XFOIL copy.

The snapshot intentionally excludes sample run-output directories and obvious
binary artifacts. Source files, build files, documentation, ORRS data/source,
and plot-library source are retained.
