import asyncio

from app.collectors.mikrotik import MikroTikClient, MikroTikError


class _FailingWriter:
    def write(self, _data: bytes) -> None:
        raise BrokenPipeError("socket closed")

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


class _DummyReader:
    async def readexactly(self, _length: int) -> bytes:
        return b""


def test_command_closes_broken_transport_and_raises_mikrotik_error() -> None:
    async def run() -> None:
        client = MikroTikClient(
            name="3deAbril",
            role="core",
            host="127.0.0.1",
            port=8728,
            username="user",
            password="pass",
        )
        client._reader = _DummyReader()
        client._writer = _FailingWriter()

        try:
            await client.get_system_resource()
        except MikroTikError as exc:
            assert "Failed to write MikroTik API request" in str(exc)
        else:
            raise AssertionError("Expected MikroTikError")

        assert client._reader is None
        assert client._writer is None

    asyncio.run(run())
