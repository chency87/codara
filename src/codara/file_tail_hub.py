from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional


def _is_run_ended(meta_path: Optional[Path]) -> bool:
    if not meta_path or not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return bool(meta.get("ended_at"))
    except Exception:
        return False


@dataclass(frozen=True)
class _HubKey:
    path: Path
    meta_path: Optional[Path]
    poll_ms: int


class TailReader:
    def __init__(
        self,
        *,
        path: Path,
        meta_path: Optional[Path],
        poll_ms: int,
        history_max_bytes: int = 256 * 1024,
        max_chunk_bytes: int = 64 * 1024,
        subscriber_queue_max: int = 32,
    ) -> None:
        self.path = path
        self.meta_path = meta_path
        self.poll_ms = max(50, int(poll_ms))
        self.history_max_bytes = max(0, int(history_max_bytes))
        self.max_chunk_bytes = max(4096, int(max_chunk_bytes))
        self.subscriber_queue_max = max(1, int(subscriber_queue_max))

        self._history = bytearray()
        self._subscribers: set[asyncio.Queue[Optional[bytes]]] = set()
        self._lock = asyncio.Lock()
        self._seed_lock = asyncio.Lock()
        self._seeded = False
        self._task: Optional[asyncio.Task[None]] = None
        self._closed = False

    async def _seed_history(self) -> None:
        if self._seeded or self._closed:
            return
        async with self._seed_lock:
            if self._seeded or self._closed:
                return
            if self.path.exists() and self.history_max_bytes > 0:
                try:
                    size = self.path.stat().st_size
                    start = max(0, size - self.history_max_bytes)
                    with self.path.open("rb") as handle:
                        handle.seek(start)
                        seeded = handle.read()
                    if seeded:
                        self._history.extend(seeded)
                        if len(self._history) > self.history_max_bytes:
                            self._history = self._history[-self.history_max_bytes :]
                except OSError:
                    pass
            self._seeded = True

    async def start(self) -> None:
        if self._closed:
            return
        if self._task is not None:
            if not self._task.done():
                return
            self._task = None

        await self._seed_history()
        self._task = asyncio.create_task(self._run(), name=f"tail:{self.path.name}")

    async def subscriber_count(self) -> int:
        async with self._lock:
            return len(self._subscribers)

    async def close(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        async with self._lock:
            subscribers = list(self._subscribers)
            self._subscribers.clear()
        for q in subscribers:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, *, tail_bytes: int) -> AsyncIterator[bytes]:
        if self._closed:
            return
        await self._seed_history()

        queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=self.subscriber_queue_max)

        async with self._lock:
            initial = b""
            if tail_bytes and tail_bytes > 0:
                bounded = min(int(tail_bytes), self.history_max_bytes or int(tail_bytes))
                if bounded > 0 and self._history:
                    initial = bytes(self._history[-bounded:])
            if initial:
                try:
                    queue.put_nowait(initial)
                except asyncio.QueueFull:
                    return
            self._subscribers.add(queue)

        await self.start()
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    async def _broadcast(self, payload: Optional[bytes]) -> None:
        async with self._lock:
            subscribers = list(self._subscribers)
        if not subscribers:
            return

        dead: list[asyncio.Queue[Optional[bytes]]] = []
        for q in subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        if dead:
            async with self._lock:
                for q in dead:
                    self._subscribers.discard(q)
                    try:
                        q.put_nowait(None)
                    except asyncio.QueueFull:
                        pass

    async def _run(self) -> None:
        try:
            if not self.path.exists():
                await self._broadcast(None)
                return
            with self.path.open("rb") as handle:
                # Start at end-of-file: history seeding already captured the tail snapshot.
                try:
                    handle.seek(0, 2)
                except OSError:
                    pass
                while True:
                    buffer = bytearray()
                    while len(buffer) < self.max_chunk_bytes:
                        chunk = handle.read(min(4096, self.max_chunk_bytes - len(buffer)))
                        if not chunk:
                            break
                        buffer.extend(chunk)

                    if buffer:
                        if self.history_max_bytes > 0:
                            self._history.extend(buffer)
                            if len(self._history) > self.history_max_bytes:
                                self._history = self._history[-self.history_max_bytes :]
                        await self._broadcast(bytes(buffer))
                        continue

                    if _is_run_ended(self.meta_path):
                        await self._broadcast(None)
                        return

                    async with self._lock:
                        if not self._subscribers:
                            return

                    await asyncio.sleep(max(0.05, self.poll_ms / 1000))
        except asyncio.CancelledError:
            raise
        except FileNotFoundError:
            await self._broadcast(None)


class FileTailHub:
    def __init__(self) -> None:
        self._readers: dict[_HubKey, TailReader] = {}
        self._lock = asyncio.Lock()

    async def subscribe(
        self,
        *,
        path: Path,
        meta_path: Optional[Path],
        tail_bytes: int,
        poll_ms: int,
    ) -> AsyncIterator[bytes]:
        key = _HubKey(path=path.expanduser().resolve(), meta_path=meta_path.expanduser().resolve() if meta_path else None, poll_ms=int(poll_ms))
        async with self._lock:
            reader = self._readers.get(key)
            if reader is None:
                reader = TailReader(path=key.path, meta_path=key.meta_path, poll_ms=key.poll_ms)
                self._readers[key] = reader

        async def iterator() -> AsyncIterator[bytes]:
            try:
                async for chunk in reader.subscribe(tail_bytes=int(tail_bytes)):
                    yield chunk
            finally:
                # Best-effort cleanup: if reader has no subscribers, retire it.
                count = await reader.subscriber_count()
                if count == 0:
                    async with self._lock:
                        existing = self._readers.get(key)
                        if existing is reader:
                            self._readers.pop(key, None)
                    await reader.close()

        return iterator()
