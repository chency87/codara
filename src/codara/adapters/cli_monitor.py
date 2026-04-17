import asyncio
from typing import Any, Optional


async def communicate_with_stall_detection(
    proc: Any,
    *,
    input_data: Optional[bytes] = None,
    stall_timeout_seconds: int,
    process_label: str,
) -> tuple[bytes, bytes]:
    """Communicate with a CLI process and kill it if it is alive but silent."""
    if stall_timeout_seconds <= 0:
        return await proc.communicate(input_data) if input_data is not None else await proc.communicate()

    stdout_reader = getattr(proc, "stdout", None)
    stderr_reader = getattr(proc, "stderr", None)
    if not hasattr(stdout_reader, "read") or not hasattr(stderr_reader, "read"):
        return await proc.communicate(input_data) if input_data is not None else await proc.communicate()

    loop = asyncio.get_running_loop()
    last_output_at = loop.time()
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    async def write_stdin() -> None:
        nonlocal last_output_at
        stdin = getattr(proc, "stdin", None)
        if stdin is None:
            return
        if input_data:
            stdin.write(input_data)
            drain = getattr(stdin, "drain", None)
            if callable(drain):
                await drain()
            last_output_at = loop.time()
        close = getattr(stdin, "close", None)
        if callable(close):
            close()
        wait_closed = getattr(stdin, "wait_closed", None)
        if callable(wait_closed):
            await wait_closed()

    async def read_stream(reader: Any, chunks: list[bytes]) -> None:
        nonlocal last_output_at
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            chunks.append(chunk)
            last_output_at = loop.time()

    stdin_task = asyncio.create_task(write_stdin())
    stdout_task = asyncio.create_task(read_stream(stdout_reader, stdout_chunks))
    stderr_task = asyncio.create_task(read_stream(stderr_reader, stderr_chunks))
    wait_task = asyncio.create_task(proc.wait())
    tasks = [stdin_task, stdout_task, stderr_task, wait_task]
    try:
        while not wait_task.done():
            await asyncio.sleep(min(1.0, max(0.1, stall_timeout_seconds / 10)))
            idle_seconds = loop.time() - last_output_at
            if idle_seconds >= stall_timeout_seconds:
                await terminate_process(proc)
                raise RuntimeError(
                    f"{process_label} stalled: no stdout/stderr output for {stall_timeout_seconds}s"
                )
        await asyncio.gather(stdin_task, stdout_task, stderr_task)
        return b"".join(stdout_chunks), b"".join(stderr_chunks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def terminate_process(proc: Any) -> None:
    if getattr(proc, "returncode", None) is not None:
        return
    try:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=5)
    except Exception:
        kill = getattr(proc, "kill", None)
        if callable(kill):
            kill()
            await proc.wait()
