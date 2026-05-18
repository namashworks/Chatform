"""Storage backends for tenant configs and session artifacts.

The app has two persistence needs:

  * **TenantStore** — durable record of each ``(form_url, api_key, model)``
    triple registered through the generator UI. Lookups happen on every
    chatbot page load, so this is small + read-heavy.
  * **ArtifactStore** — per-session JSON / PDF / HTML blobs. Written every
    turn, occasionally listed, rarely read back.

Two backends ship with the project:

  * ``local`` — files on disk in ``tenants/`` and ``sessions/``. Zero deps,
    perfect for development. Default.
  * ``cloud`` — Firestore for tenants, Cloud Storage for artifacts.
    Production-ready for Cloud Run / GKE / any environment with GCP creds.

The backend is selected by the ``STORAGE_BACKEND`` env var. The factory
caches the chosen instance so we only construct one per process.
"""
from __future__ import annotations

import os
from functools import lru_cache

from .protocol import ArtifactStore, TenantStore


@lru_cache(maxsize=1)
def get_tenant_store() -> TenantStore:
    backend = (os.environ.get("STORAGE_BACKEND") or "local").strip().lower()
    if backend == "cloud":
        from .cloud import FirestoreTenantStore

        return FirestoreTenantStore()
    from .local import LocalTenantStore

    return LocalTenantStore()


@lru_cache(maxsize=1)
def get_artifact_store() -> ArtifactStore:
    backend = (os.environ.get("STORAGE_BACKEND") or "local").strip().lower()
    if backend == "cloud":
        from .cloud import GCSArtifactStore

        return GCSArtifactStore()
    from .local import LocalArtifactStore

    return LocalArtifactStore()


__all__ = [
    "ArtifactStore",
    "TenantStore",
    "get_tenant_store",
    "get_artifact_store",
]
