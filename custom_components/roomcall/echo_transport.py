"""TECHO5 intercom/1 client: authenticated Noise records carrying live PCM.

Wire specification: TECHO5 echod/internal/feature/phone/intercom.go and
internal/lib/sealed/sealed.go. Audio is mono signed little-endian PCM16 at 16 kHz.
No audio is recorded, and the receiver's Answer button controls acceptance.
"""

from __future__ import annotations

import asyncio
import hashlib
import struct
from typing import Any

from noise.connection import NoiseConnection

PROTOCOL = b"techo5-intercom/1"
MAX_RECORD = 60064
MAX_PAYLOAD = 1280
FRAME_BYTES = 640


class WordRefused(Exception):
    """The Echo closed the handshake: its house word is not the paired one."""


class EchoLink:
    """One encrypted TCP call; only one coroutine may receive at a time."""

    def __init__(self, reader: asyncio.StreamReader, writer: Any, noise: Any) -> None:
        self.reader, self.writer, self.noise = reader, writer, noise
        self.buffer = bytearray()
        self.lock = asyncio.Lock()

    @classmethod
    async def connect(cls, host: str, port: int, word: str, name: str) -> EchoLink:
        """Authenticate before sending the caller's name or opening any audio."""
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port, limit=4096), 5)
        try:
            writer.write(
                b"GET /intercom HTTP/1.1\r\nHost: "
                + (f"[{host}]" if ":" in host else host).encode("ascii")
                + b"\r\nConnection: Upgrade\r\nUpgrade: "
                + PROTOCOL
                + b"\r\n\r\n"
            )
            headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            lines = headers.lower().split(b"\r\n")
            if (
                not headers.startswith(b"HTTP/1.1 101 ")
                or b"upgrade: " + PROTOCOL not in lines
                or b"connection: upgrade" not in lines
            ):
                raise ValueError("intercom upgrade refused")
            noise = NoiseConnection.from_name(b"Noise_NNpsk0_25519_ChaChaPoly_SHA256")
            noise.set_as_initiator()
            noise.set_prologue(PROTOCOL)
            noise.set_psks(psk=hashlib.sha256(("techo5-intercom psk:" + word).encode()).digest())
            noise.start_handshake()
            link = cls(reader, writer, noise)
            await link._write_record(bytes(noise.write_message()))
            try:
                reply = await asyncio.wait_for(link._read_record(256), 5)
            except asyncio.IncompleteReadError as err:
                # TECHO5 hangs up without a reply when the house words differ.
                raise WordRefused from err
            noise.read_message(reply)
            caller = "".join(char for char in name if char.isprintable())[:40] or "RoomCall"
            await link.send(b"H", caller.encode())
            return link
        except BaseException:
            writer.close()
            raise

    async def _write_record(self, value: bytes) -> None:
        self.writer.write(struct.pack(">I", len(value)) + value)
        await asyncio.wait_for(self.writer.drain(), 2)

    async def _read_record(self, limit: int = MAX_RECORD) -> bytes:
        size = struct.unpack(">I", await self.reader.readexactly(4))[0]
        if not 0 < size <= limit:
            raise ValueError("invalid intercom record size")
        return await self.reader.readexactly(size)

    async def send(self, kind: bytes, payload: bytes = b"") -> None:
        if len(kind) != 1 or len(payload) > MAX_PAYLOAD:
            raise ValueError("invalid intercom message")
        async with self.lock:
            await self._write_record(
                self.noise.encrypt(kind + struct.pack(">H", len(payload)) + payload)
            )

    async def _read(self, size: int) -> bytes:
        while len(self.buffer) < size:
            self.buffer.extend(self.noise.decrypt(await self._read_record()))
        value = bytes(self.buffer[:size])
        del self.buffer[:size]
        return value

    async def receive(self) -> tuple[bytes, bytes]:
        header = await self._read(3)
        size = struct.unpack(">H", header[1:])[0]
        if size > MAX_PAYLOAD:
            raise ValueError("invalid intercom payload size")
        return header[:1], await self._read(size)

    async def close(self) -> None:
        try:
            await self.send(b"X")
        finally:
            self.writer.close()
