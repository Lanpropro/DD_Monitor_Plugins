"""Run one loopback match room and its temporary Windows tunnel for the hosting session."""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager, suppress
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile

from aiohttp import ClientError, ClientTimeout, web
from .watch_room_server import make_app
from .watch_party_link import room_proxy

DIGEST = "555b46c85582461d189cac30d14ad05480d612613801be9f110b8ce08c5b2113"


def tunnel_helper():
    exe = Path(__file__).resolve().parent / "native" / "cloudflared" / "cloudflared.exe"
    with exe.open("rb") as f:
        digest = hashlib.sha256()
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != DIGEST:
        raise ValueError("tunnel_integrity")
    return exe


def own_tunnel(pid):
    """Close this tunnel even when Windows terminates the hosting plugin process."""
    import ctypes as c
    from ctypes import wintypes as w

    class Basic(c.Structure):
        _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64), ("flags", w.DWORD),
                    ("min_set", c.c_size_t), ("max_set", c.c_size_t), ("limit", w.DWORD),
                    ("affinity", c.c_size_t), ("priority", w.DWORD), ("schedule", w.DWORD)]

    class Limits(c.Structure):
        _fields_ = [("basic", Basic), ("io", c.c_uint64 * 6), ("memory", c.c_size_t * 4)]

    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = kernel.OpenProcess.restype = w.HANDLE
    job = w.HANDLE(kernel.CreateJobObjectW(None, None))
    process = w.HANDLE(kernel.OpenProcess(0x0101, False, pid))  # SET_QUOTA | TERMINATE
    try:
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not (job and process and kernel.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits))
                and kernel.AssignProcessToJobObject(job, process)):
            raise c.WinError(c.get_last_error())
    except OSError:
        if job:
            kernel.CloseHandle(job)
        raise
    finally:
        if process:
            kernel.CloseHandle(process)
    return lambda: kernel.CloseHandle(job)


@asynccontextmanager
async def local_room(session, host_key, notify):
    async with AsyncExitStack() as resources:
        for attempt in range(2):
            try:
                room = await resources.enter_async_context(start_local_room(session, host_key, notify))
                break
            except ValueError as error:
                if str(error) != "tunnel_start" or attempt:
                    raise
                notify("首次公网入口未连通，正在重试一次…")
        yield room


@asynccontextmanager
async def start_local_room(session, host_key, notify):
    exe = await asyncio.to_thread(tunnel_helper)
    runner = web.AppRunner(make_app(host_key=host_key), access_log=None, shutdown_timeout=2)
    await runner.setup()
    process = reader = close_job = None
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        notify("正在为本机房间建立公网入口…")
        with tempfile.TemporaryDirectory(prefix="ddm-watch-") as temp:
            config = Path(temp) / "config.yml"
            config.write_text("{}\n", encoding="ascii")
            process = await asyncio.create_subprocess_exec(
                str(exe), "tunnel", "--config", str(config), "--no-autoupdate", "--protocol", "http2",
                "--metrics", "127.0.0.1:0", "--url", f"http://127.0.0.1:{port}",
                creationflags=subprocess.CREATE_NO_WINDOW, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE)
            close_job = own_tunnel(process.pid)
            ready = asyncio.get_running_loop().create_future()

            async def read_log():
                origin = None
                async for line in process.stderr:
                    found = re.search(rb"https://[a-z0-9-]+\.trycloudflare\.com\b", line)
                    if found:
                        origin = found[0].decode("ascii")
                    if origin and b"Registered tunnel connection" in line and not ready.done():
                        ready.set_result(origin)
                if not ready.done():
                    ready.set_exception(ValueError("tunnel_start"))

            reader = asyncio.create_task(read_log())
            try:
                origin = await asyncio.wait_for(ready, 75)
            except TimeoutError as exc:
                raise ValueError("tunnel_start") from exc
            # The edge may register before the public hostname can reach our origin.
            until = asyncio.get_running_loop().time() + 30
            while True:
                try:
                    async with session.get(origin + "/health", proxy=room_proxy(origin), allow_redirects=False,
                                           timeout=ClientTimeout(total=4)) as response:
                        if response.status == 200 and (await response.json()).get("service") == "match-watch-party":
                            break
                except (ClientError, TimeoutError, ValueError):
                    pass
                if process.returncode is not None or asyncio.get_running_loop().time() > until:
                    raise ValueError("tunnel_start")
                await asyncio.sleep(1)
            yield f"ws://127.0.0.1:{port}/live", origin, process
    finally:
        if close_job:
            close_job()
        if process is not None and process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        if reader:
            await reader
        await runner.cleanup()
