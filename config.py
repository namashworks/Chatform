"""Centralized configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent

# Session files live in two sibling folders so it's obvious at a glance which
# conversations finished and which were abandoned mid-way:
#   sessions/incomplete/  — auto-saved after every turn; user left or hasn't
#                            successfully submitted yet
#   sessions/complete/    — moved here only after Google confirms submission
SESSIONS_DIR = PROJECT_ROOT / "sessions"
INCOMPLETE_DIR = SESSIONS_DIR / "incomplete"
COMPLETE_DIR = SESSIONS_DIR / "complete"

# Each (form_url, api_key, model) triple registered via the generator UI gets
# its own JSON in tenants/. The shareable chatbot URL embeds the tenant ID.
TENANTS_DIR = PROJECT_ROOT / "tenants"

for _d in (SESSIONS_DIR, INCOMPLETE_DIR, COMPLETE_DIR, TENANTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DEFAULT_PUBLIC_BASE_URL = "http://localhost:8501"


def public_base_url() -> str:
    """The externally reachable URL of this Streamlit app.

    Used to build shareable chatbot links + HTML embed snippets in the
    generator UI. Set ``PUBLIC_BASE_URL`` in the environment when deploying
    behind a real hostname (e.g. ``https://forms-bot.example.com``).
    """
    raw = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    return raw or DEFAULT_PUBLIC_BASE_URL


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    google_api_key: str
    form_url: str
    gemini_model: str
    use_system_certs: bool
    ssl_verify: bool

    @classmethod
    def load(cls) -> "Settings":
        """Load from environment variables (the original single-form mode)."""
        api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        form_url = os.environ.get("FORM_URL", "").strip()

        missing = [
            name for name, val in [("GOOGLE_API_KEY", api_key), ("FORM_URL", form_url)] if not val
        ]
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}. "
                "Copy .env.example to .env and fill them in."
            )
        return cls.from_values(google_api_key=api_key, form_url=form_url)

    @classmethod
    def from_values(
        cls,
        *,
        google_api_key: str,
        form_url: str,
        gemini_model: str | None = None,
    ) -> "Settings":
        """Build from explicit per-tenant values; TLS posture still comes from env."""
        model = (gemini_model or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash").strip()
        return cls(
            google_api_key=google_api_key.strip(),
            form_url=form_url.strip(),
            gemini_model=model,
            use_system_certs=_as_bool(os.environ.get("USE_SYSTEM_CERTS"), default=True),
            ssl_verify=_as_bool(os.environ.get("SSL_VERIFY"), default=True),
        )


_ssl_initialized = False


def init_ssl(settings: Settings | None = None) -> None:
    """Apply the configured TLS posture exactly once per process.

    Pass a ``Settings`` to use its flags, or omit it to read directly from
    the environment (handy in the generator UI before any settings exist).

    - ``USE_SYSTEM_CERTS=true`` injects the OS trust store via ``truststore``.
      This is the safe way to make Python honor a corporate root CA that IT
      has installed in Windows.
    - ``SSL_VERIFY=false`` silences urllib3 warnings; the actual ``verify=False``
      flag is passed to requests calls by the parser/submitter.
    """
    global _ssl_initialized
    if _ssl_initialized:
        return
    _ssl_initialized = True

    if settings is not None:
        use_system_certs = settings.use_system_certs
        ssl_verify = settings.ssl_verify
    else:
        use_system_certs = _as_bool(os.environ.get("USE_SYSTEM_CERTS"), default=True)
        ssl_verify = _as_bool(os.environ.get("SSL_VERIFY"), default=True)

    if use_system_certs:
        try:
            import truststore

            truststore.inject_into_ssl()
        except Exception:  # noqa: BLE001
            # truststore is optional — fall back to certifi if it's missing/broken.
            pass

    if not ssl_verify:
        try:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:  # noqa: BLE001
            pass
