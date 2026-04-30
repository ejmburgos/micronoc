import asyncio
import hashlib

class MikroTikError(Exception):
    pass


class MikroTikClient:
    def __init__(
        self,
        *,
        name: str,
        role: str,
        host: str,
        port: int,
        username: str,
        password: str,
        wan_interface: str | None = None,
    ) -> None:
        if not host:
            raise MikroTikError("MikroTik host is not configured")
        if not username:
            raise MikroTikError("MikroTik user is not configured")
        if not password:
            raise MikroTikError("MikroTik password is not configured")

        self.name = name
        self.role = role
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.wan_interface = wan_interface.strip() if wan_interface else None
        self.timeout = 10.0
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        if self._reader is not None and self._writer is not None:
            return

        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout,
            )
            await self._login()
        except (OSError, asyncio.TimeoutError) as exc:
            await self.close()
            raise MikroTikError("Failed to connect to MikroTik router") from exc
        except MikroTikError:
            await self.close()
            raise

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()
        self._reader = None
        self._writer = None

    async def get_system_resource(self) -> list[dict[str, str]]:
        return await self._command("/system/resource/print")

    async def get_interfaces(self) -> list[dict[str, str]]:
        return await self._command("/interface/print")

    async def get_interface_traffic(self, interface_name: str | None = None) -> list[dict[str, str]]:
        params: dict[str, str] = {"once": "true"}
        if interface_name:
            params["interface"] = interface_name.strip()
        return await self._command("/interface/monitor-traffic", **params)

    async def get_torch(self, interface_name: str, duration_seconds: int = 1) -> list[dict[str, str]]:
        params = {
            "interface": interface_name.strip(),
            "duration": f"{max(1, duration_seconds)}s",
        }
        return await self._command("/tool/torch", **params)

    async def _login(self) -> None:
        replies = await self._command_raw("/login", name=self.username, password=self.password)
        ret = self._extract_ret(replies)
        if ret:
            digest = hashlib.md5()
            digest.update(b"\x00")
            digest.update(self.password.encode("utf-8"))
            digest.update(bytes.fromhex(ret))
            response = "00" + digest.hexdigest()
            await self._command_raw("/login", name=self.username, response=response)

    async def _command(self, command: str, **params: str) -> list[dict[str, str]]:
        try:
            await self.connect()
            return await self._command_raw(command, **params)
        except MikroTikError:
            await self.close()
            raise
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
            await self.close()
            raise MikroTikError("Failed to communicate with MikroTik router") from exc

    async def _command_raw(self, command: str, **params: str) -> list[dict[str, str]]:
        assert self._reader is not None and self._writer is not None
        words = [command] + [f"={key}={value}" for key, value in params.items()]
        await self._send_sentence(words)

        rows: list[dict[str, str]] = []
        while True:
            sentence = await self._read_sentence()
            if not sentence:
                continue

            kind = sentence[0]
            if kind == "!re":
                rows.append(self._parse_attrs(sentence[1:]))
                continue
            if kind == "!trap":
                attrs = self._parse_attrs(sentence[1:])
                message = attrs.get("message", "MikroTik API error")
                raise MikroTikError(f"MikroTik API error: {message}")
            if kind == "!fatal":
                attrs = self._parse_attrs(sentence[1:])
                message = attrs.get("message", "MikroTik fatal error")
                raise MikroTikError(f"MikroTik API fatal error: {message}")
            if kind == "!done":
                break

        return rows

    @staticmethod
    def _extract_ret(rows: list[dict[str, str]]) -> str | None:
        for row in rows:
            if "ret" in row:
                return row["ret"]
        return None

    @staticmethod
    def _parse_attrs(words: list[str]) -> dict[str, str]:
        attrs: dict[str, str] = {}
        for word in words:
            if not word.startswith("="):
                continue
            parts = word[1:].split("=", 1)
            if len(parts) == 2:
                attrs[parts[0]] = parts[1]
        return attrs

    async def _send_sentence(self, words: list[str]) -> None:
        assert self._writer is not None
        try:
            for word in words:
                encoded = word.encode("utf-8")
                self._writer.write(self._encode_length(len(encoded)))
                self._writer.write(encoded)
            self._writer.write(b"\x00")
            await self._writer.drain()
        except (OSError, asyncio.TimeoutError) as exc:
            raise MikroTikError("Failed to write MikroTik API request") from exc

    async def _read_sentence(self) -> list[str]:
        words: list[str] = []
        while True:
            length = await self._read_length()
            if length == 0:
                return words
            words.append(await self._read_word(length))

    async def _read_word(self, length: int) -> str:
        assert self._reader is not None
        try:
            raw = await asyncio.wait_for(self._reader.readexactly(length), timeout=self.timeout)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError) as exc:
            raise MikroTikError("Failed to read MikroTik API response") from exc
        return raw.decode("utf-8", errors="replace")

    async def _read_length(self) -> int:
        assert self._reader is not None
        try:
            first = (await asyncio.wait_for(self._reader.readexactly(1), timeout=self.timeout))[0]
        except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError) as exc:
            raise MikroTikError("Failed to read MikroTik API response") from exc
        if first < 0x80:
            return first
        if first < 0xC0:
            second = (await self._read_exactly(1))[0]
            return ((first & 0x3F) << 8) + second
        if first < 0xE0:
            rest = await self._read_exactly(2)
            return ((first & 0x1F) << 16) + (rest[0] << 8) + rest[1]
        if first < 0xF0:
            rest = await self._read_exactly(3)
            return ((first & 0x0F) << 24) + (rest[0] << 16) + (rest[1] << 8) + rest[2]
        if first == 0xF0:
            rest = await self._read_exactly(4)
            return (rest[0] << 24) + (rest[1] << 16) + (rest[2] << 8) + rest[3]
        raise MikroTikError("Received invalid length prefix from MikroTik API")

    async def _read_exactly(self, length: int) -> bytes:
        assert self._reader is not None
        try:
            return await asyncio.wait_for(self._reader.readexactly(length), timeout=self.timeout)
        except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError) as exc:
            raise MikroTikError("Failed to read MikroTik API response") from exc

    @staticmethod
    def _encode_length(length: int) -> bytes:
        if length < 0x80:
            return bytes([length])
        if length < 0x4000:
            length |= 0x8000
            return bytes([(length >> 8) & 0xFF, length & 0xFF])
        if length < 0x200000:
            length |= 0xC00000
            return bytes([(length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF])
        if length < 0x10000000:
            length |= 0xE0000000
            return bytes(
                [(length >> 24) & 0xFF, (length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF]
            )
        return bytes(
            [0xF0, (length >> 24) & 0xFF, (length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF]
        )
