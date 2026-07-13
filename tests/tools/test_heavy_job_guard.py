from __future__ import annotations

import threading
import time

from tools.heavy_job_guard import acquire_heavy_job, is_heavy_node_job


def test_classifies_node_builds_without_serializing_normal_commands():
    assert is_heavy_node_job("npm run build")
    assert is_heavy_node_job("pnpm exec next build")
    assert not is_heavy_node_job("git status --short")


def test_two_builds_are_serialized(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    active = 0
    max_active = 0
    state_lock = threading.Lock()
    barrier = threading.Barrier(3)

    def _build():
        nonlocal active, max_active
        barrier.wait()
        lease = acquire_heavy_job("npm run build", timeout=3)
        try:
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.15)
            with state_lock:
                active -= 1
        finally:
            lease.release()

    threads = [threading.Thread(target=_build) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert max_active == 1
