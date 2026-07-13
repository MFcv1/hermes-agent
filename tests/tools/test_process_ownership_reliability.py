from __future__ import annotations

import os
import json
import socket
import subprocess
import sys
import time

import psutil
import pytest

from tools.environments.local import LocalEnvironment
from tools.process_ownership import OWNER_ENV, new_owner_id, terminate_owned_processes
from tools.process_registry import ProcessRegistry


pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX fork lifecycle"),
    # These tests deliberately create and reap isolated subprocess trees,
    # including a reparented double-fork daemon outside pytest's PPID subtree.
    pytest.mark.live_system_guard_bypass,
]


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


def _port_closed(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def test_natural_exit_reaps_double_fork_and_listening_port(tmp_path):
    info = tmp_path / "daemon.info"
    script = tmp_path / "double_fork.py"
    script.write_text(
        "import os,socket,time\n"
        "if os.fork(): raise SystemExit(0)\n"
        "os.setsid()\n"
        "if os.fork(): raise SystemExit(0)\n"
        "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen()\n"
        f"open({str(info)!r},'w').write(f'{{os.getpid()}} {{s.getsockname()[1]}}')\n"
        "while True: time.sleep(1)\n",
        encoding="utf-8",
    )
    env = LocalEnvironment(cwd=str(tmp_path))

    result = env.execute(f"{sys.executable} {script}", timeout=5)

    assert result["returncode"] == 0
    pid, port = map(int, info.read_text().split())
    assert _wait_dead(pid)
    assert _port_closed(port)


def test_timeout_reaps_child_and_listening_port(tmp_path):
    info = tmp_path / "server.info"
    script = tmp_path / "server.py"
    script.write_text(
        "import os,socket,time\n"
        "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen()\n"
        f"open({str(info)!r},'w').write(f'{{os.getpid()}} {{s.getsockname()[1]}}')\n"
        "while True: time.sleep(1)\n",
        encoding="utf-8",
    )
    env = LocalEnvironment(cwd=str(tmp_path))

    result = env.execute(f"{sys.executable} {script}", timeout=1)

    assert result["returncode"] == 124
    pid, port = map(int, info.read_text().split())
    assert _wait_dead(pid)
    assert _port_closed(port)


def test_crash_recovery_reaps_owner_when_wrapper_pid_is_gone(tmp_path, monkeypatch):
    import tools.process_registry as registry_module

    info = tmp_path / "recovered-server.info"
    checkpoint = tmp_path / "processes.json"
    script = tmp_path / "recovered-server.py"
    script.write_text(
        "import os,socket,time\n"
        "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen()\n"
        f"open({str(info)!r},'w').write(f'{{os.getpid()}} {{s.getsockname()[1]}}')\n"
        "while True: time.sleep(1)\n",
        encoding="utf-8",
    )
    owner_id = new_owner_id()
    env = dict(os.environ)
    env[OWNER_ENV] = owner_id
    proc = subprocess.Popen([sys.executable, str(script)], env=env, start_new_session=True)
    try:
        deadline = time.monotonic() + 3
        while not info.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        pid, port = map(int, info.read_text().split())
        checkpoint.write_text(json.dumps([{
            "session_id": "proc_crash",
            "command": "server",
            "pid": 999_999_999,
            "pid_scope": "host",
            "host_start_time": None,
            "owner_id": owner_id,
        }]), encoding="utf-8")
        monkeypatch.setattr(registry_module, "CHECKPOINT_PATH", checkpoint)

        assert ProcessRegistry().recover_from_checkpoint() == 0
        assert _wait_dead(pid)
        assert _port_closed(port)
    finally:
        terminate_owned_processes(owner_id)
        if proc.poll() is None:
            proc.kill()
