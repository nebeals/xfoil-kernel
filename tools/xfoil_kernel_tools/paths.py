from __future__ import annotations

import os
from pathlib import Path


class KernelRootNotFoundError(FileNotFoundError):
    """Raised when a command needs the source checkout but cannot find it."""


def _looks_like_kernel_root(path: Path) -> bool:
    return (
        (path / "fortran" / "xfoil_kernel_driver.f").is_file()
        and (path / "baselines" / "cases.json").is_file()
    )


def _missing_kernel_root_message(candidates: list[Path]) -> str:
    searched = ", ".join(str(path) for path in candidates) or "<none>"
    return (
        "XFOIL kernel source tree not found. "
        "Run from an editable/source-tree install or set XFOIL_KERNEL_ROOT "
        "to a checkout containing fortran/xfoil_kernel_driver.f and "
        f"baselines/cases.json. Searched: {searched}"
    )


def find_kernel_root(*, required: bool = False) -> Path:
    """Return the source tree root for this staged kernel package."""

    env_root = os.environ.get("XFOIL_KERNEL_ROOT")
    if env_root:
        root = Path(env_root).expanduser().resolve()
        if required and not _looks_like_kernel_root(root):
            raise KernelRootNotFoundError(_missing_kernel_root_message([root]))
        return root

    module_path = Path(__file__).resolve()
    candidates = []
    for candidate in (module_path.parents[2], Path.cwd()):
        root = candidate.resolve()
        candidates.append(root)
        if _looks_like_kernel_root(root):
            return root

    if required:
        raise KernelRootNotFoundError(_missing_kernel_root_message(candidates))
    return candidates[0]


def require_kernel_root(kernel_root: str | Path | None = None) -> Path:
    """Return a validated source checkout root or raise a helpful error."""

    if kernel_root is None:
        return find_kernel_root(required=True)

    root = Path(kernel_root).expanduser().resolve()
    if not _looks_like_kernel_root(root):
        raise KernelRootNotFoundError(_missing_kernel_root_message([root]))
    return root


def has_kernel_source_tree(kernel_root: str | Path | None = None) -> bool:
    """Return whether a path looks like a source checkout root."""

    root = (
        find_kernel_root()
        if kernel_root is None
        else Path(kernel_root).expanduser().resolve()
    )
    return _looks_like_kernel_root(root)


def find_default_xfoil_root(kernel_root: Path | None = None) -> Path:
    """Return the default external XFOIL source tree location."""

    if kernel_root is None:
        kernel_root = find_kernel_root()

    env_root = os.environ.get("XFOIL_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    candidates = (
        kernel_root / "vendor" / "xfoil",
        kernel_root.parent / "xfoil",
        kernel_root.parent.parent / "xfoil",
    )
    for candidate in candidates:
        if (candidate / "src").is_dir() and (candidate / "plotlib").is_dir():
            return candidate.resolve()
    return candidates[0].resolve()


KERNEL_ROOT = find_kernel_root()
DEFAULT_XFOIL_ROOT = find_default_xfoil_root(KERNEL_ROOT)
