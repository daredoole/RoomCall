"""Command line: ``roomcall discover | serve | send``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import mimetypes
from pathlib import Path

from aiohttp import ClientSession, web

from . import __version__
from .config import Config
from .discovery import discover


def _discover(args: argparse.Namespace) -> int:
    speakers = asyncio.run(discover(args.timeout))
    if not speakers:
        print("No speakers found. Is this machine on the same network/VLAN as your speakers?")
        return 1
    width = max(len(s.name) for s in speakers)
    for s in speakers:
        print(f"{s.name:<{width}}  {s.kind.value:<8} {s.host:<15} {s.model or ''}")
    return 0


def _serve(args: argparse.Namespace) -> int:
    from .server import create_app
    from .sinks import default_sinks

    config = Config.load(args.config)
    if args.port:
        config.port = args.port
    if args.demo:
        from .demo import DEMO_ROOMS, DemoSink, demo_discover
        from .models import SpeakerKind

        config.rooms = DEMO_ROOMS
        app = create_app(config, {k: DemoSink() for k in SpeakerKind}, demo_discover)
    else:
        app = create_app(config, default_sinks())
    print(f"RoomCall {__version__} on http://{config.host}:{config.port}")
    print(f"Speakers fetch clips from {config.base_url()}")
    web.run_app(app, host=config.host, port=config.port, print=None)
    return 0


async def _send(url: str, target: str, name: str, audio: bytes, token: str | None) -> int:
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    headers = {"Content-Type": content_type}
    if token:
        headers["X-RoomCall-Token"] = token
    async with (
        ClientSession() as session,
        session.post(
            f"{url.rstrip('/')}/api/talk",
            params={"target": target},
            data=audio,
            headers=headers,
        ) as resp,
    ):
        print(await resp.text())
        return 0 if resp.status == 200 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="roomcall", description=__doc__)
    parser.add_argument("--version", action="version", version=f"roomcall {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("discover", help="list speakers found on the network")
    p.add_argument("--timeout", type=float, default=4.0)
    p.set_defaults(func=_discover)

    p = sub.add_parser("serve", help="run the intercom server and web app")
    p.add_argument("-c", "--config", type=Path, default=Path("roomcall.yaml"))
    p.add_argument("-p", "--port", type=int)
    p.add_argument("--demo", action="store_true", help="fake speakers; nothing is played")
    p.set_defaults(func=_serve)

    p = sub.add_parser("send", help="broadcast an audio file through a running server")
    p.add_argument("target", help="room, speaker name, or 'all'")
    p.add_argument("file", type=Path)
    p.add_argument("--url", default="http://127.0.0.1:8765")
    p.add_argument("--token")
    p.set_defaults(
        func=lambda a: asyncio.run(
            _send(a.url, a.target, a.file.name, a.file.read_bytes(), a.token)
        )
    )

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
