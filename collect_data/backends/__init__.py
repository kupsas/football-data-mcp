"""Storage backend implementations (local disk, Cloudflare R2)."""

from __future__ import annotations

from collect_data.backends.local import LocalBackend

__all__ = ["LocalBackend"]
