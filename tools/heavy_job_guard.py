"""Cross-thread/process serialization for resource-heavy Node jobs."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


class HeavyJobLockTimeout(RuntimeError):
    pass


class HeavyJobCancelled(RuntimeError):
    pass


_HEAVY_NODE_RE = re.compile(
    r"(?:^|[;&|()]\s*)(?:"
    r"(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:build|test|lint|typecheck)\b|"
    r"(?:npx|pnpm\s+exec|yarn\s+dlx|bunx)\s+(?:next|opennextjs-cloudflare|eslint|tsc|vitest)\b|"
    r"(?:next|opennextjs-cloudflare|eslint|tsc|vitest)\s+(?:build|run|--run)\b"
    r")",
    re.IGNORECASE,
)


def is_heavy_node_job(command: str) -> bool:
    return bool(_HEAVY_NODE_RE.search(str(command or "")))


@dataclass
class HeavyJobLease:
    file_obj: object
    path: Path
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        try:
            if os.name == "nt":
                import msvcrt

                self.file_obj.seek(0)
                msvcrt.locking(self.file_obj.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file_obj.fileno(), fcntl.LOCK_UN)
        finally:
            self.file_obj.close()


def acquire_heavy_job(
    command: str,
    *,
    timeout: float,
    cancelled: Optional[Callable[[], bool]] = None,
) -> HeavyJobLease | None:
    if not is_heavy_node_job(command):
        return None
    from hermes_constants import get_hermes_home

    path = Path(get_hermes_home()) / "runtime" / "heavy-node-job.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    file_obj = path.open("a+")
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if cancelled and cancelled():
            file_obj.close()
            raise HeavyJobCancelled("heavy Node job cancelled while waiting for the global slot")
        try:
            if os.name == "nt":
                import msvcrt

                file_obj.seek(0)
                if os.fstat(file_obj.fileno()).st_size == 0:
                    file_obj.write("0")
                    file_obj.flush()
                file_obj.seek(0)
                msvcrt.locking(file_obj.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return HeavyJobLease(file_obj=file_obj, path=path)
        except (BlockingIOError, OSError):
            if time.monotonic() >= deadline:
                file_obj.close()
                raise HeavyJobLockTimeout(
                    f"timed out waiting {timeout}s for the global heavy Node job slot"
                )
            time.sleep(0.05)


__all__ = [
    "HeavyJobCancelled",
    "HeavyJobLease",
    "HeavyJobLockTimeout",
    "acquire_heavy_job",
    "is_heavy_node_job",
]
