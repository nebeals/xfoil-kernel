# XFOIL Vendored Snapshot

This directory is a vendored XFOIL source snapshot used by the standalone
kernel tooling.

## Provenance

- Source: local XFOIL source checkout available during kernel development.
- Imported: 2026-04-25.
- XFOIL version reported by `src/xfoil.f`: 6.99.
- Primary copyright notices in XFOIL sources: Copyright (C) 2000 Mark Drela,
  with some files also naming Harold Youngren.
- Plot-library notices: Copyright (C) 1996 Harold Youngren, Mark Drela.

## Local Changes At Import

This directory should be treated as upstream source material. At import time,
the source files were not modified. The only import-time changes were:

- sample run-output directories were omitted,
- obvious binary artifacts were omitted,
- a top-level `COPYING` file containing GPL version 2 text was added because
  the source file notices reference the GNU General Public License but the local
  checkout did not include a top-level GPL text file.

Kernel build scripts may generate modified build-only copies under
`build/kernel-driver/generated/`. Those generated files are not tracked and are
not part of this vendored snapshot.

## Licensing Notes

XFOIL source files state that they are licensed under the GNU General Public
License, version 2 or later. Plot-library files include GNU Library General
Public License, version 2 or later notices and the original license text at
`plotlib/GPL-library`.
