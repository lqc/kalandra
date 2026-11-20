import asyncio
import sys
from pathlib import Path

import pytest

from kalandra.auth import NoopCredentialProvider
from kalandra.transports.file import FileTransport


@pytest.mark.parametrize("cancel_caller", [False, True])
async def test_close_with_inherited_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_caller: bool):
    (tmp_path / "objects").mkdir()
    release = tmp_path / "release-child"
    transport = FileTransport(url=tmp_path.as_uri(), credentials_provider=NoopCredentialProvider())
    connection = transport.fetch()
    child_script = """
import pathlib
import sys
import time

release = pathlib.Path(sys.argv[1])
deadline = time.monotonic() + 10
while not release.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
"""
    service_script = """
import subprocess
import sys

subprocess.Popen([sys.executable, '-c', *sys.argv[1:]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
print('ready', flush=True)
sys.stdin.buffer.read()
"""
    monkeypatch.setattr(
        connection, "_service_argv", lambda _: [sys.executable, "-c", service_script, child_script, str(release)]
    )
    monkeypatch.setattr("kalandra.transports.file.STDERR_DRAIN_TIMEOUT", 10 if cancel_caller else 0.1)

    stdout, _ = await connection._open_fetch_service_connection()
    try:
        assert await asyncio.wait_for(stdout.readline(), timeout=2) == b"ready\n"
        close_task = asyncio.create_task(connection._close_service_connection())
        if cancel_caller:
            await asyncio.wait_for(connection._process_exited.wait(), timeout=2)
            close_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(close_task, timeout=2)
        else:
            await asyncio.wait_for(close_task, timeout=2)

        assert connection.process.get_returncode() is not None
        assert connection._err_task.cancelled()
    finally:
        release.touch()
        connection.process.close()
