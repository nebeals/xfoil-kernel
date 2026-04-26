# License And Provenance

This staging package contains original kernel tooling plus a vendored XFOIL
source snapshot.

## Vendored XFOIL

`vendor/xfoil/` contains XFOIL 6.99 source material. XFOIL source-file notices
state that the code is licensed under the GNU General Public License, version 2
or later. The added `vendor/xfoil/COPYING` file contains GPL version 2 text.

The XFOIL plot library includes GNU Library General Public License, version 2
or later notices. The original plot-library license text is retained at
`vendor/xfoil/plotlib/GPL-library`.

See `vendor/README.md` and `vendor/xfoil/README.xfoil-kernel.md` for snapshot
provenance and import notes.

## Kernel Tooling

The Python and Fortran files outside `vendor/xfoil/` are local kernel tooling
for building and driving the vendored XFOIL sources. They are intended to be
distributed under terms compatible with the vendored GPL-derived executable.

## Application Boundary

Downstream applications can keep only generic interfaces and an optional
interprocess client so they do not directly import or link this GPL-derived
kernel.
