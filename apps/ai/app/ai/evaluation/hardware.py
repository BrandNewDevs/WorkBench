"""Sanitized local hardware capture and bounded benchmark resource sampling."""

import asyncio
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from app.ai.evaluation.benchmark_contracts import (
    HardwareKind,
    HardwareSnapshot,
    ResourceObservation,
    utc_now,
)

ResultT = TypeVar("ResultT")
_MIB = 1024 * 1024
_JETSON_MODEL_PATH = Path("/proc/device-tree/model")
_NVIDIA_RELEASE_PATH = Path("/etc/nv_tegra_release")
_MEMINFO_PATH = Path("/proc/meminfo")
_THERMAL_ROOT = Path("/sys/class/thermal")


class HardwareProbe(Protocol):
    """Small interface for obtaining non-identifying local machine facts."""

    def capture(self, storage_root: Path) -> HardwareSnapshot:
        """Capture hardware facts without contacting a network endpoint."""
        ...


class ResourceMonitor(Protocol):
    """Measure peak local resources while one asynchronous evaluation executes."""

    async def measure(
        self,
        operation: Callable[[], Awaitable[ResultT]],
    ) -> tuple[ResultT, ResourceObservation]:
        """Return the operation result and content-free resource observations."""
        ...


CommandRunner = Callable[[tuple[str, ...]], str | None]


class LocalHardwareProbe:
    """Read standard local OS and NVIDIA facts without host or user identifiers."""

    def __init__(self, command_runner: CommandRunner | None = None) -> None:
        self._run_command = command_runner or _command_output

    def capture(self, storage_root: Path) -> HardwareSnapshot:
        """Capture facts needed to reproduce a model benchmark."""

        approved_storage = storage_root.resolve(strict=True)
        if not approved_storage.is_dir():
            raise ValueError("benchmark storage root must be an existing directory")

        diagnostics: list[str] = []
        total_memory = _total_memory_bytes()
        jetson_model = _read_text(_JETSON_MODEL_PATH)
        hardware_kind = (
            HardwareKind.JETSON
            if jetson_model and "jetson" in jetson_model.casefold()
            else HardwareKind.WORKSTATION
        )
        accelerator_name, accelerator_memory = _nvidia_accelerator(self._run_command)
        uses_shared_memory = hardware_kind is HardwareKind.JETSON
        if uses_shared_memory:
            accelerator_name = accelerator_name or jetson_model
            accelerator_memory = total_memory
        if accelerator_name is None:
            diagnostics.append("NVIDIA accelerator details were unavailable.")
        if total_memory is None:
            diagnostics.append("Total system memory could not be measured.")

        jetpack_version = _package_version(self._run_command, "nvidia-jetpack")
        nvidia_release = _first_line(_read_text(_NVIDIA_RELEASE_PATH))
        return HardwareSnapshot(
            captured_at_utc=utc_now(),
            hardware_kind=hardware_kind,
            operating_system=platform.system() or "unknown",
            operating_system_release=platform.release() or "unknown",
            architecture=platform.machine() or "unknown",
            total_memory_bytes=total_memory,
            available_storage_bytes=shutil.disk_usage(approved_storage).free,
            accelerator_name=accelerator_name,
            accelerator_memory_bytes=accelerator_memory,
            accelerator_uses_shared_memory=uses_shared_memory,
            jetson_model=jetson_model if hardware_kind is HardwareKind.JETSON else None,
            jetpack_version=jetpack_version,
            nvidia_platform_release=nvidia_release,
            cuda_version=_cuda_version(self._run_command),
            ollama_version=_ollama_version(self._run_command),
            diagnostics=tuple(diagnostics),
        )


@dataclass(frozen=True, slots=True)
class _ResourceSample:
    memory_used_bytes: int
    memory_capacity_bytes: int
    temperature_celsius: float | None = None
    thermal_throttling: bool | None = None


SampleReader = Callable[[], _ResourceSample | None]


class LocalResourceMonitor:
    """Sample dedicated GPU memory or Jetson shared memory during evaluation."""

    def __init__(
        self,
        hardware: HardwareSnapshot,
        *,
        sample_interval_seconds: float = 1.0,
        sample_reader: SampleReader | None = None,
    ) -> None:
        if sample_interval_seconds <= 0:
            raise ValueError("resource sample interval must be greater than zero")
        self._hardware = hardware
        self._sample_interval_seconds = sample_interval_seconds
        self._sample_reader = sample_reader or _sample_reader_for(hardware)

    async def measure(
        self,
        operation: Callable[[], Awaitable[ResultT]],
    ) -> tuple[ResultT, ResourceObservation]:
        """Sample in the background and always stop the sampler cleanly."""

        stop = asyncio.Event()
        samples: list[_ResourceSample] = []
        sampler = asyncio.create_task(self._sample_until_stopped(stop, samples))
        try:
            result = await operation()
        finally:
            stop.set()
            await sampler
        return result, _summarize_samples(self._hardware, samples)

    async def _sample_until_stopped(
        self,
        stop: asyncio.Event,
        samples: list[_ResourceSample],
    ) -> None:
        while True:
            sample = await asyncio.to_thread(self._sample_reader)
            if sample is not None:
                samples.append(sample)
            if stop.is_set():
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._sample_interval_seconds)
            except TimeoutError:
                continue


def _sample_reader_for(hardware: HardwareSnapshot) -> SampleReader:
    if hardware.hardware_kind is HardwareKind.JETSON:
        return _sample_jetson_resources
    return _sample_nvidia_resources


def _sample_nvidia_resources() -> _ResourceSample | None:
    output = _command_output(
        (
            "nvidia-smi",
            "--query-gpu=memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
        )
    )
    if output is None:
        return None
    first_gpu = _first_line(output)
    if first_gpu is None:
        return None
    fields = tuple(field.strip() for field in first_gpu.split(","))
    if len(fields) != 3:
        return None
    try:
        memory_used = int(float(fields[0]) * _MIB)
        memory_capacity = int(float(fields[1]) * _MIB)
    except ValueError:
        return None
    try:
        temperature = float(fields[2])
    except ValueError:
        temperature = None
    return _ResourceSample(
        memory_used_bytes=memory_used,
        memory_capacity_bytes=memory_capacity,
        temperature_celsius=temperature,
        thermal_throttling=_nvidia_thermal_throttling(),
    )


def _nvidia_thermal_throttling() -> bool | None:
    output = _command_output(
        (
            "nvidia-smi",
            "--query-gpu=clocks_event_reasons.sw_thermal_slowdown",
            "--format=csv,noheader,nounits",
        )
    )
    first_gpu = _first_line(output)
    if first_gpu is None:
        return None
    normalized = first_gpu.casefold()
    if "not active" in normalized:
        return False
    if normalized == "active":
        return True
    return None


def _sample_jetson_resources() -> _ResourceSample | None:
    meminfo = _read_text(_MEMINFO_PATH)
    if meminfo is None:
        return None
    values: dict[str, int] = {}
    for line in meminfo.splitlines():
        key, separator, remainder = line.partition(":")
        if not separator:
            continue
        raw_value = remainder.strip().split(maxsplit=1)[0]
        try:
            values[key] = int(raw_value) * 1024
        except ValueError:
            continue
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if total is None or available is None:
        return None
    temperatures = tuple(_thermal_temperatures())
    return _ResourceSample(
        memory_used_bytes=max(0, total - available),
        memory_capacity_bytes=total,
        temperature_celsius=max(temperatures) if temperatures else None,
    )


def _thermal_temperatures() -> tuple[float, ...]:
    if not _THERMAL_ROOT.is_dir():
        return ()
    temperatures: list[float] = []
    for path in _THERMAL_ROOT.glob("thermal_zone*/temp"):
        raw = _read_text(path)
        if raw is None:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        normalized = value / 1_000 if value > 1_000 else value
        if 0 <= normalized <= 200:
            temperatures.append(normalized)
    return tuple(temperatures)


def _summarize_samples(
    hardware: HardwareSnapshot,
    samples: list[_ResourceSample],
) -> ResourceObservation:
    source = (
        "linux-procfs-shared-memory"
        if hardware.hardware_kind is HardwareKind.JETSON
        else "nvidia-smi"
    )
    if not samples:
        return ResourceObservation(
            measurement_source=source,
            sample_count=0,
            diagnostics=("Resource sampling was unavailable on this machine.",),
        )
    throttling_values = tuple(
        sample.thermal_throttling
        for sample in samples
        if sample.thermal_throttling is not None
    )
    temperatures = tuple(
        sample.temperature_celsius
        for sample in samples
        if sample.temperature_celsius is not None
    )
    capacities = {sample.memory_capacity_bytes for sample in samples}
    if len(capacities) != 1:
        return ResourceObservation(
            measurement_source=source,
            sample_count=0,
            diagnostics=("Resource samples reported inconsistent memory capacity.",),
        )
    return ResourceObservation(
        measurement_source=source,
        sample_count=len(samples),
        memory_capacity_bytes=capacities.pop(),
        peak_memory_used_bytes=max(sample.memory_used_bytes for sample in samples),
        max_temperature_celsius=max(temperatures) if temperatures else None,
        thermal_throttling_observed=(
            any(throttling_values) if throttling_values else None
        ),
        diagnostics=(
            (
                "Thermal-throttling state was not available from the local sampler."
                if not throttling_values
                else "Thermal-throttling state was sampled locally."
            ),
        ),
    )


def _nvidia_accelerator(runner: CommandRunner) -> tuple[str | None, int | None]:
    output = runner(
        (
            "nvidia-smi",
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        )
    )
    first_gpu = _first_line(output)
    if first_gpu is None:
        return None, None
    name, separator, raw_memory = first_gpu.rpartition(",")
    if not separator:
        return None, None
    try:
        return name.strip(), int(float(raw_memory.strip()) * _MIB)
    except ValueError:
        return None, None


def _package_version(runner: CommandRunner, package: str) -> str | None:
    return _first_line(runner(("dpkg-query", "-W", "-f=${Version}", package)))


def _cuda_version(runner: CommandRunner) -> str | None:
    output = runner(("nvcc", "--version"))
    if output is None:
        return None
    match = re.search(r"\brelease\s+([0-9.]+)", output, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _ollama_version(runner: CommandRunner) -> str | None:
    output = runner(("ollama", "--version"))
    if output is None:
        return None
    match = re.search(r"\b([0-9]+(?:\.[0-9]+){1,3})\b", output)
    return match.group(1) if match else None


def _total_memory_bytes() -> int | None:
    if not hasattr(os, "sysconf"):
        return None
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError):
        return None
    return pages * page_size if pages > 0 and page_size > 0 else None


def _command_output(arguments: tuple[str, ...]) -> str | None:
    executable = shutil.which(arguments[0])
    if executable is None:
        return None
    try:
        result = subprocess.run(
            (executable, *arguments[1:]),
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = result.stdout.strip()
    return output if result.returncode == 0 and output else None


def _read_text(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8", errors="replace").replace("\x00", "").strip()
    except OSError:
        return None
    return value or None


def _first_line(value: str | None) -> str | None:
    if value is None:
        return None
    line = value.splitlines()[0].strip()
    return line or None
