"""Tests for local-only, non-identifying hardware and resource capture."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest

import app.ai.evaluation.hardware as hardware_module
from app.ai.evaluation.benchmark_contracts import HardwareKind, HardwareSnapshot
from app.ai.evaluation.hardware import LocalHardwareProbe, LocalResourceMonitor


def test_hardware_probe_records_versions_without_host_identity(tmp_path: Path) -> None:
    outputs = {
        "nvidia-smi": "NVIDIA RTX Fixture, 8192\n",
        "dpkg-query": "6.0-fixture\n",
        "nvcc": "Cuda compilation tools, release 12.6, fixture\n",
        "ollama": "ollama version is 0.11.0\n",
    }

    snapshot = LocalHardwareProbe(
        command_runner=lambda command: outputs.get(command[0])
    ).capture(tmp_path)

    assert snapshot.hardware_kind is HardwareKind.WORKSTATION
    assert snapshot.accelerator_name == "NVIDIA RTX Fixture"
    assert snapshot.accelerator_memory_bytes == 8192 * 1024 * 1024
    assert snapshot.cuda_version == "12.6"
    assert snapshot.ollama_version == "0.11.0"
    serialized = snapshot.model_dump(mode="json", by_alias=True)
    assert "hostname" not in serialized
    assert "username" not in serialized


def test_jetson_probe_uses_exact_model_and_shared_system_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_reader = hardware_module._read_text

    def fake_reader(path: Path) -> str | None:
        if path == hardware_module._JETSON_MODEL_PATH:
            return "NVIDIA Jetson AGX Orin Developer Kit"
        if path == hardware_module._NVIDIA_RELEASE_PATH:
            return "# R36 (release), REVISION: 4.3"
        return real_reader(path)

    monkeypatch.setattr(hardware_module, "_read_text", fake_reader)
    monkeypatch.setattr(hardware_module, "_total_memory_bytes", lambda: 64)

    snapshot = LocalHardwareProbe(command_runner=lambda command: None).capture(tmp_path)

    assert snapshot.hardware_kind is HardwareKind.JETSON
    assert snapshot.jetson_model == "NVIDIA Jetson AGX Orin Developer Kit"
    assert snapshot.accelerator_uses_shared_memory is True
    assert snapshot.accelerator_memory_bytes == 64
    assert snapshot.nvidia_platform_release == "# R36 (release), REVISION: 4.3"


async def test_resource_monitor_captures_peak_without_affecting_operation() -> None:
    second_sampled = Event()
    samples = iter(
        (
            hardware_module._ResourceSample(40, 100, 60),
            hardware_module._ResourceSample(75, 100, 72),
        )
    )

    def read_sample() -> hardware_module._ResourceSample | None:
        sample = next(samples, None)
        if sample is not None and sample.memory_used_bytes == 75:
            second_sampled.set()
        return sample

    monitor = LocalResourceMonitor(
        _hardware(),
        sample_interval_seconds=0.001,
        sample_reader=read_sample,
    )

    async def operation() -> str:
        observed = await asyncio.to_thread(second_sampled.wait, 1)
        assert observed, "resource monitor did not collect the second fixture sample"
        return "completed"

    result, observation = await monitor.measure(operation)

    assert result == "completed"
    assert observation.sample_count == 2
    assert observation.peak_memory_used_bytes == 75
    assert observation.memory_pressure_ratio == 0.75
    assert observation.max_temperature_celsius == 72


async def test_resource_monitor_stops_when_operation_fails() -> None:
    monitor = LocalResourceMonitor(
        _hardware(),
        sample_interval_seconds=0.001,
        sample_reader=lambda: hardware_module._ResourceSample(40, 100),
    )

    async def operation() -> str:
        raise ValueError("fixture failure")

    with pytest.raises(ValueError, match="fixture failure"):
        await monitor.measure(operation)


def _hardware() -> HardwareSnapshot:
    return HardwareSnapshot(
        captured_at_utc=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
        hardware_kind=HardwareKind.WORKSTATION,
        operating_system="Linux",
        operating_system_release="fixture",
        architecture="x86_64",
        total_memory_bytes=100,
        available_storage_bytes=100,
        accelerator_name="NVIDIA fixture",
        accelerator_memory_bytes=100,
    )
