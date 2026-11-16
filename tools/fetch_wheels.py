"""通过 HTTP 代理（CONNECT 隧道）从 PyPI 下载 wheel。

沙箱里 pip 走代理会卡住，但裸 socket + CONNECT 是通的，所以自己实现一遍。
只用于构建，不参与运行时。

    python tools/fetch_wheels.py [--proxy 127.0.0.1:15236] pyinstaller ...
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
from urllib.parse import urlsplit

PROXY = ("127.0.0.1", 15236)
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       ".build", "wheels")


def _dechunk(data: bytes) -> bytes:
    out = bytearray()
    while data:
        line, _, rest = data.partition(b"\r\n")
        try:
            size = int(line.split(b";")[0], 16)
        except ValueError:
            break
        if size == 0:
            break
        out += rest[:size]
        data = rest[size + 2:]
    return bytes(out)


def http_get(url: str, timeout: float = 30.0) -> bytes:
    parts = urlsplit(url)
    host, port = parts.hostname, parts.port or 443
    sock = socket.create_connection(PROXY, timeout=timeout)
    sock.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("proxy closed connection")
        head += chunk
    if b" 200 " not in head.split(b"\r\n")[0]:
        raise RuntimeError(f"proxy refused: {head[:120]!r}")
    ctx = ssl.create_default_context()
    tls = ctx.wrap_socket(sock, server_hostname=host)
    path = parts.path + (("?" + parts.query) if parts.query else "")
    tls.sendall(
        f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: pip/24.3\r\n"
        f"Accept: */*\r\nConnection: close\r\n\r\n".encode()
    )
    data = b""
    while True:
        chunk = tls.recv(65536)
        if not chunk:
            break
        data += chunk
    tls.close()
    header, _, body = data.partition(b"\r\n\r\n")
    if b"transfer-encoding: chunked" in header.lower():
        body = _dechunk(body)
    return body


def pick(pkg: str) -> tuple[str, str]:
    meta = json.loads(http_get(f"https://pypi.org/pypi/{pkg}/json"))
    version = meta["info"]["version"]
    files = meta["releases"][version]
    wheels = [f for f in files if f["filename"].endswith(".whl")]
    # 优先 win_amd64 的平台轮子，其次纯 Python 轮子
    wheels.sort(key=lambda f: (
        0 if "win_amd64" in f["filename"] else 1,
        0 if "py3-none-any" in f["filename"] else 1,
    ))
    best = wheels[0]
    return best["filename"], best["url"]


def main(argv: list[str]) -> int:
    global PROXY
    args = list(argv)
    if "--proxy" in args:
        idx = args.index("--proxy")
        host, _, port = args[idx + 1].partition(":")
        PROXY = (host, int(port))
        del args[idx:idx + 2]
    if not args:
        args = ["pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "packaging",
                "setuptools", "pefile", "pywin32-ctypes"]
    os.makedirs(OUT_DIR, exist_ok=True)
    for pkg in args:
        name, url = pick(pkg)
        path = os.path.join(OUT_DIR, name)
        if os.path.exists(path) and os.path.getsize(path) > 1024:
            print(f"skip  {name}")
            continue
        data = http_get(url, timeout=120)
        with open(path, "wb") as fh:
            fh.write(data)
        print(f"down  {name}  {len(data) / 1024:.0f} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
