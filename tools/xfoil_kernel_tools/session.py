from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import json
import queue
import shutil
import subprocess
import threading
from typing import Any, Sequence

from .baseline import BaselineCase
from .build import DEFAULT_KERNEL_DRIVER_BUILD_ROOT
from .driver import (
    build_case_namelist,
    build_nonconvergence_diagnostics,
    parse_kernel_driver_output,
    parse_kernel_failure_markers,
    parse_kernel_header,
)
from .paths import KERNEL_ROOT


DEFAULT_KERNEL_SESSION_EXECUTABLE = DEFAULT_KERNEL_DRIVER_BUILD_ROOT / "bin" / "xfoil_kernel_session"
DEFAULT_KERNEL_SESSION_RUN_ROOT = KERNEL_ROOT / "runs" / "kernel-session"


@dataclass(frozen=True)
class KernelSessionResult:
    """Raw result from one persistent-session solve request."""

    transcript: str
    returncode: int | None


class KernelSession:
    """Persistent process wrapper around the compiled XFOIL kernel session."""

    def __init__(
        self,
        *,
        session_executable: Path = DEFAULT_KERNEL_SESSION_EXECUTABLE,
        runtime_root: Path = DEFAULT_KERNEL_SESSION_RUN_ROOT,
        startup_timeout_seconds: float = 10.0,
    ) -> None:
        self.session_executable = session_executable.resolve()
        self.runtime_root = runtime_root
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        if not self.session_executable.exists():
            raise FileNotFoundError(
                f"Kernel session executable not found at {self.session_executable}. "
                "Build it first with scripts/build_kernel_driver.py."
            )

        self.process = subprocess.Popen(
            [str(self.session_executable)],
            cwd=self.runtime_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if self.process.stdout is None or self.process.stdin is None:
            raise RuntimeError("Could not open kernel session pipes.")

        self._lines: queue.Queue[str | None] = queue.Queue()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        ready = self._readline(startup_timeout_seconds)
        if not ready.startswith("XK_READY"):
            self.close(kill=True)
            raise RuntimeError(f"Kernel session did not become ready: {ready!r}")

    def solve_case(
        self,
        case: BaselineCase,
        *,
        run_root: Path = DEFAULT_KERNEL_SESSION_RUN_ROOT,
        kernel_root: Path = KERNEL_ROOT,
        timeout_seconds: float = 120.0,
    ) -> dict[str, Any]:
        """Run one case through the persistent session and return a summary."""

        case_dir = run_root / case.id
        case_dir.mkdir(parents=True, exist_ok=True)
        coordinate_file = self._prepare_coordinate_airfoil(case, kernel_root)
        namelist = build_case_namelist(case, kernel_root=kernel_root, coordinate_file=coordinate_file)
        input_text = "SOLVE\n" + namelist

        input_path = case_dir / "input.nml"
        transcript_path = case_dir / "transcript.txt"
        summary_path = case_dir / "summary.json"
        input_path.write_text(input_text)

        result = self._solve(input_text, timeout_seconds=timeout_seconds)
        transcript_path.write_text(result.transcript)

        points = parse_kernel_driver_output(result.transcript)
        diagnostics = parse_kernel_header(result.transcript)
        failure_markers = parse_kernel_failure_markers(result.transcript)
        converged_alpha = [point.alpha_deg for point in points if point.converged]
        missing_alpha = _missing_requested_alpha(case.alpha_deg, converged_alpha)
        error_lines = [line for line in result.transcript.splitlines() if line.startswith("XK_ERROR")]
        ok = result.returncode is None and not error_lines
        nonconvergence_diagnostics = build_nonconvergence_diagnostics(
            case.alpha_deg,
            points,
            options=case.options,
            header=diagnostics,
            failure_markers=failure_markers,
        )
        summary: dict[str, Any] = {
            "case_id": case.id,
            "returncode": result.returncode,
            "ok": ok,
            "complete": ok and not missing_alpha,
            "input_file": str(input_path),
            "transcript_file": str(transcript_path),
            "requested_alpha_deg": list(case.alpha_deg),
            "converged_alpha_deg": converged_alpha,
            "missing_alpha_deg": missing_alpha,
            "points": [point.to_dict() for point in points],
            "diagnostics": diagnostics,
            "nonconvergence_diagnostics": nonconvergence_diagnostics,
            "failure_markers": failure_markers,
        }
        if error_lines:
            summary["error"] = error_lines[0]
        elif result.returncode is not None:
            summary["error"] = f"Kernel session exited with return code {result.returncode}."
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        return summary

    def ping(self, *, timeout_seconds: float = 10.0) -> str:
        self._write("PING\n")
        return self._readline(timeout_seconds)

    def reset_boundary_layer_state(self, *, timeout_seconds: float = 10.0) -> str:
        """Reset XFOIL's persistent boundary-layer/wake convergence state."""

        self._write("RESET_BOUNDARY_LAYER_STATE\n")
        response = self._readline(timeout_seconds)
        if not response.startswith("XK_OK reset_boundary_layer_state"):
            raise RuntimeError(f"Kernel session could not reset boundary layer state: {response!r}")
        return response

    def _prepare_coordinate_airfoil(self, case: BaselineCase, kernel_root: Path) -> Path | None:
        if str(case.airfoil.get("type", "")).lower() != "coordinates":
            return None
        source = _resolve_kernel_path(str(case.airfoil["path"]), kernel_root)
        airfoil_dir = self.runtime_root / "airfoils"
        airfoil_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(str(source.resolve()).encode("utf-8")).hexdigest()[:12]
        destination = airfoil_dir / f"af_{digest}{source.suffix or '.dat'}"
        if source.resolve() != destination.resolve():
            shutil.copyfile(source, destination)
        return destination.relative_to(self.runtime_root)

    def close(self, *, kill: bool = False) -> None:
        if getattr(self, "process", None) is None:
            return
        process = self.process
        if process.poll() is None and process.stdin is not None and not kill:
            try:
                self._write("SHUTDOWN\n")
                self._readline(2.0)
            except (BrokenPipeError, RuntimeError, TimeoutError):
                pass
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5.0)

    def __enter__(self) -> KernelSession:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _solve(self, input_text: str, *, timeout_seconds: float) -> KernelSessionResult:
        self._write(input_text)
        lines = []
        while True:
            line = self._readline(timeout_seconds)
            lines.append(line)
            if line.startswith("XK_END") or line.startswith("XK_ERROR"):
                break
        return KernelSessionResult(transcript="\n".join(lines) + "\n", returncode=self.process.poll())

    def _write(self, text: str) -> None:
        if self.process.stdin is None:
            raise RuntimeError("Kernel session stdin is closed.")
        self.process.stdin.write(text)
        self.process.stdin.flush()

    def _readline(self, timeout_seconds: float) -> str:
        try:
            line = self._lines.get(timeout=timeout_seconds)
        except queue.Empty as exc:
            if self.process.poll() is not None:
                raise RuntimeError(f"Kernel session exited with return code {self.process.returncode}.") from exc
            raise TimeoutError("Timed out waiting for kernel session output.") from exc
        if line is None:
            raise RuntimeError(f"Kernel session exited with return code {self.process.poll()}.")
        return line

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._lines.put(line.rstrip("\n"))
        self._lines.put(None)


def _missing_requested_alpha(requested_alpha: Sequence[float], completed_alpha: Sequence[float]) -> list[float]:
    missing = []
    for requested in requested_alpha:
        if not any(abs(float(requested) - float(value)) <= 1.0e-6 for value in completed_alpha):
            missing.append(float(requested))
    return missing


def _resolve_kernel_path(path: str, kernel_root: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return (kernel_root / candidate).resolve()
