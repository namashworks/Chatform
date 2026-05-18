"""Backwards-compatible facade over the storage abstraction.

Existing imports (``from tenant_store import save_tenant, load_tenant, ...``)
keep working — they now delegate to whatever backend ``STORAGE_BACKEND`` picks
(``local`` by default, ``cloud`` for Firestore).
"""
from __future__ import annotations

import secrets
from typing import Any

from storage import get_tenant_store


def new_tenant_id() -> str:
    """Short URL-safe identifier — random, hard to guess."""
    return secrets.token_urlsafe(8)


def save_tenant(
    *,
    form_url: str,
    google_api_key: str,
    gemini_model: str,
    form_title: str = "",
    question_count: int = 0,
    tenant_id: str | None = None,
) -> str:
    return get_tenant_store().save_tenant(
        form_url=form_url,
        google_api_key=google_api_key,
        gemini_model=gemini_model,
        form_title=form_title,
        question_count=question_count,
        tenant_id=tenant_id,
    )


def load_tenant(tenant_id: str) -> dict[str, Any]:
    return get_tenant_store().load_tenant(tenant_id)


def tenant_exists(tenant_id: str) -> bool:
    return get_tenant_store().tenant_exists(tenant_id)
