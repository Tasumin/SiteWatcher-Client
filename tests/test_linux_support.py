import os

import pytest

from sitewatch_agent import host_monitor


@pytest.mark.skipif(os.name == "nt", reason="Linux runtime test")
def test_linux_memory_collection():
    percent, total, available = host_monitor._linux_memory()
    assert total > 0
    assert 0 <= available <= total
    assert percent is None or 0 <= percent <= 100


@pytest.mark.skipif(os.name == "nt", reason="Linux runtime test")
def test_linux_disk_collection():
    disks = host_monitor._linux_disks()
    assert disks
    assert all("name" in disk and "usedPercent" in disk for disk in disks)


@pytest.mark.skipif(os.name == "nt", reason="Linux runtime test")
def test_linux_cpu_collection():
    percent = host_monitor._linux_cpu_percent(0.01)
    assert percent is None or 0 <= percent <= 100
