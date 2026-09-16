"""打印本机对外 IP（局域网地址），供启动脚本展示访问地址用。

UDP `connect` 只做路由选路、**不实际发包**，因此不需要外网可达；
取不到时以退出码 1 结束（调用方据此降级为只显示 127.0.0.1）。
"""

from __future__ import annotations

import socket
import sys


def lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


if __name__ == "__main__":
    ip = lan_ip()
    if not ip:
        sys.exit(1)
    print(ip)
