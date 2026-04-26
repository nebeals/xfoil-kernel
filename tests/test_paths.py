from __future__ import annotations

import sys
from pathlib import Path

import pytest


KERNEL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KERNEL_ROOT / "tools"))

from xfoil_kernel_tools.paths import (  # noqa: E402
    KernelRootNotFoundError,
    find_kernel_root,
    has_kernel_source_tree,
    require_kernel_root,
)


def _make_minimal_kernel_root(root: Path) -> Path:
    (root / "fortran").mkdir(parents=True)
    (root / "baselines").mkdir(parents=True)
    (root / "fortran" / "xfoil_kernel_driver.f").write_text("      END\n")
    (root / "baselines" / "cases.json").write_text("[]\n")
    return root


def test_find_kernel_root_honors_explicit_source_tree(monkeypatch, tmp_path: Path) -> None:
    source_root = _make_minimal_kernel_root(tmp_path / "kernel")
    monkeypatch.setenv("XFOIL_KERNEL_ROOT", str(source_root))

    assert find_kernel_root() == source_root.resolve()
    assert find_kernel_root(required=True) == source_root.resolve()
    assert require_kernel_root() == source_root.resolve()
    assert has_kernel_source_tree() is True


def test_require_kernel_root_reports_missing_source_tree(monkeypatch, tmp_path: Path) -> None:
    missing_root = tmp_path / "not-a-kernel"
    monkeypatch.setenv("XFOIL_KERNEL_ROOT", str(missing_root))

    with pytest.raises(KernelRootNotFoundError, match="XFOIL_KERNEL_ROOT"):
        find_kernel_root(required=True)

    with pytest.raises(KernelRootNotFoundError, match="fortran/xfoil_kernel_driver.f"):
        require_kernel_root(missing_root)

    assert has_kernel_source_tree(missing_root) is False
