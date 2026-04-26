"""Helper tools for the staged XFOIL kernel."""

from .baseline import (
    BaselineCase,
    PolarFile,
    PolarPoint,
    build_input_deck,
    load_cases,
    parse_xfoil_polar,
    run_case,
    write_reference_baselines,
)
from .build import build_kernel_driver, build_pristine_xfoil
from .driver import (
    KernelDriverPoint,
    build_case_namelist,
    compare_to_reference,
    parse_kernel_driver_output,
    run_kernel_case,
)
from .worker import RegisteredAirfoil, XFoilKernelWorker
from .c81_generator import C81GenerationError, generate_c81_from_manifest

__all__ = [
    "BaselineCase",
    "C81GenerationError",
    "KernelDriverPoint",
    "RegisteredAirfoil",
    "PolarFile",
    "PolarPoint",
    "XFoilKernelWorker",
    "build_input_deck",
    "build_case_namelist",
    "load_cases",
    "parse_xfoil_polar",
    "parse_kernel_driver_output",
    "run_case",
    "run_kernel_case",
    "compare_to_reference",
    "write_reference_baselines",
    "build_kernel_driver",
    "build_pristine_xfoil",
    "generate_c81_from_manifest",
]
