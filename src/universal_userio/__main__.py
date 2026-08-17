from __future__ import annotations

from .mcp_surface import UserIOMcpSurface
from .mcp_transport import StdioJsonRpcTransport
from .runtime import build_service


def main() -> None:
    import sys
    service = build_service()
    StdioJsonRpcTransport(UserIOMcpSurface(service._store, service), sys.stdin, sys.stdout).serve()


if __name__ == "__main__":
    main()
