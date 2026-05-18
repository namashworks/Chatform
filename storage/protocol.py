"""Storage interfaces. Backends in ``storage/local.py`` and ``storage/cloud.py``
must implement these protocols so the rest of the app stays backend-agnostic.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


# ---------- Tenants (small structured records) ----------

@runtime_checkable
class TenantStore(Protocol):
    """One record per (form_url, api_key, model) triple from the generator."""

    def save_tenant(
        self,
        *,
        form_url: str,
        google_api_key: str,
        gemini_model: str,
        form_title: str = "",
        question_count: int = 0,
        tenant_id: str | None = None,
    ) -> str:
        """Persist a tenant record. Returns its id (new or recycled)."""
        ...

    def load_tenant(self, tenant_id: str) -> dict[str, Any]:
        """Return the saved record. Raises ``FileNotFoundError`` if missing."""
        ...

    def tenant_exists(self, tenant_id: str) -> bool:
        ...


# ---------- Artifacts (session JSON, PDFs, response HTML) ----------

@runtime_checkable
class ArtifactStore(Protocol):
    """Per-session blobs. Paths are *logical* (e.g. ``incomplete/SID.json``);
    each backend maps them to its own physical layout.
    """

    def write_bytes(self, path: str, data: bytes) -> None:
        ...

    def write_text(self, path: str, text: str) -> None:
        ...

    def exists(self, path: str) -> bool:
        ...

    def delete(self, path: str) -> None:
        """No-op if the path doesn't exist."""
        ...

    def move(self, src_path: str, dst_path: str) -> None:
        """Move an artifact within the store. Overwrites the destination."""
        ...

    def display_path(self, path: str) -> str:
        """Human-readable location to show in the UI (relative path or URI)."""
        ...
