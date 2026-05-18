"""Backwards-compat shim — use `python -m soccer_server` instead."""

from soccer_server.transport_stdio import main

if __name__ == "__main__":
    main()
