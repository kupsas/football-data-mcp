#!/usr/bin/env python3
"""
Compatibility entry point — forwards to ``python -m collect_data``.

Prefer running::

    python -m collect_data

so logging and imports match the package layout.
"""
from __future__ import annotations

import runpy

if __name__ == "__main__":
    runpy.run_module("collect_data", run_name="__main__")
