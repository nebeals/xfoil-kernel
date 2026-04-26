# Extracted Kernel Sources

This directory contains the tracked Fortran source used by the kernel
driver/session build. These files are selected subprogram extracts from the
vendored XFOIL 6.99 source snapshot, plus the include-file closure needed to
compile them.

The source groups are:

- Airfoil I/O: `aread.f`, `naca.f`, `userio_kernel_subs.f`
- Geometry: `xgdes_kernel_subs.f`, `xgeom_kernel_subs.f`, `spline.f`,
  `xutils.f`
- Panel and inviscid solve: `xfoil_kernel_subs.f`, `xoper_kernel_subs.f`,
  `xpanel.f`, `xsolve.f`
- Viscous boundary layer: `xbl.f`, `xblsys.f`

The build tools keep the same grouping in `KERNEL_SOURCE_GROUPS`. If a routine
is added or removed from this directory, update that grouping, regenerate with
`--refresh-extracted-sources` when appropriate, and rerun the kernel-driver
comparison against the tracked reference baselines.
