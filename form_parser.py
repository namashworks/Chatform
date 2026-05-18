"""Parse a public Google Form by scraping its HTML.

Google Forms embeds the full form definition in a JS variable named
``FB_PUBLIC_LOAD_DATA_`` on the viewform page. This module extracts that JSON
and converts it into structured ``Question`` objects the chatbot can iterate
over. Submission uses the ``entry.XXXX`` IDs surfaced here.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any
from urllib.parse import urlparse

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

_FB_DATA_RE = re.compile(r"var\s+FB_PUBLIC_LOAD_DATA_\s*=\s*(\[.*?\]);", re.DOTALL)
_FORM_ID_RE = re.compile(r"/forms/d/e/([a-zA-Z0-9_-]+)")
# The form's submission session token. Required in the POST or Google
# silently discards the response (returns 200 but doesn't record it).
_FBZX_RE = re.compile(r'name="fbzx"\s+value="([^"]+)"')


class FormAccessError(RuntimeError):
    """Raised when the form URL is reachable but Google won't serve it anonymously."""


class QuestionType(IntEnum):
    SHORT_ANSWER = 0
    PARAGRAPH = 1
    MULTIPLE_CHOICE = 2
    DROPDOWN = 3
    CHECKBOXES = 4
    LINEAR_SCALE = 5
    TITLE = 6           # display-only, no input
    GRID = 7
    SECTION = 8         # display-only
    DATE = 9
    TIME = 10
    IMAGE = 11          # display-only
    FILE_UPLOAD = 13
    RATING = 18

    @property
    def is_input(self) -> bool:
        return self not in {QuestionType.TITLE, QuestionType.SECTION, QuestionType.IMAGE}


@dataclass
class Question:
    entry_id: str                          # "entry.123456789" — used in submission
    title: str                             # question text shown to user
    description: str                       # extra help text (may be empty)
    type: QuestionType
    required: bool
    options: list[str] = field(default_factory=list)   # for MC/dropdown/checkbox
    scale_min: int | None = None
    scale_max: int | None = None
    scale_labels: tuple[str, str] = ("", "")
    has_other: bool = False                # MC/checkbox with "Other" option
    raw: Any = None                        # original parsed array for debugging


@dataclass
class Form:
    form_id: str
    title: str
    description: str
    submit_url: str
    questions: list[Question]
    fbzx: str | None = None      # submission session token; required in POST
    raw: Any = None


def parse_form(
    form_url: str,
    *,
    session: requests.Session | None = None,
    verify: bool = True,
) -> Form:
    """Fetch a public Google Form URL and return its parsed structure.

    ``verify`` is forwarded to ``requests.get``. Pass ``False`` only as an
    emergency unblock on TLS-inspected networks; prefer fixing the trust
    store via ``USE_SYSTEM_CERTS=true``.
    """
    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", USER_AGENT)

    resp = sess.get(form_url, timeout=20, verify=verify)

    if resp.status_code in (401, 403):
        raise FormAccessError(
            f"Google returned HTTP {resp.status_code} for this form - it requires "
            "sign-in, so the anonymous chatbot can't read it.\n\n"
            "To fix it in the form editor:\n"
            "  - Settings -> Responses -> turn OFF 'Collect email addresses'\n"
            "  - Settings -> Responses -> turn OFF 'Limit to 1 response'\n"
            "  - Settings -> Responses -> turn OFF 'Restrict to users in <domain>'\n"
            "Then open the form's share link in a private/incognito window. "
            "If it loads without a Google sign-in prompt, paste that URL back in."
        )
    if resp.status_code == 404:
        raise FormAccessError(
            "Google returned HTTP 404 - the form ID in this URL doesn't exist "
            "(or was deleted). Double-check the URL."
        )
    resp.raise_for_status()
    html = resp.text

    # Some sign-in walls return 200 but redirect to an accounts.google.com login page.
    # Detect that case by looking for the login page markers before we try to parse.
    if "accounts.google.com/ServiceLogin" in html or "<title>Sign in" in html[:2000]:
        raise FormAccessError(
            "The form URL redirected to a Google sign-in page. Make the form "
            "accessible to anyone with the link (see the editor's Settings → Responses)."
        )

    match = _FB_DATA_RE.search(html)
    if not match:
        raise FormAccessError(
            "Could not find the form definition in the page HTML. "
            "This usually means the URL isn't a public Google Form viewform link, "
            "or Google has changed the form page structure. "
            "Verify the URL opens directly to a form (no Google login) in an incognito window."
        )
    data = json.loads(match.group(1))

    form_id = _extract_form_id(form_url, html)
    submit_url = f"https://docs.google.com/forms/d/e/{form_id}/formResponse"

    # data[1] = [description, questions[], ..., title at index 8, ...]
    form_section = data[1] if len(data) > 1 and data[1] else []
    description = _safe_get(form_section, 0) or ""
    raw_questions = _safe_get(form_section, 1) or []
    title = _safe_get(form_section, 8) or "Untitled form"

    questions = [_parse_question(q) for q in raw_questions]
    questions = [q for q in questions if q is not None]

    fbzx_match = _FBZX_RE.search(html)
    fbzx = fbzx_match.group(1) if fbzx_match else None

    return Form(
        form_id=form_id,
        title=title,
        description=description,
        submit_url=submit_url,
        questions=questions,
        fbzx=fbzx,
        raw=data,
    )


def _extract_form_id(url: str, html: str) -> str:
    m = _FORM_ID_RE.search(url) or _FORM_ID_RE.search(html)
    if not m:
        # Fall back to last path segment of the URL
        path = urlparse(url).path.strip("/").split("/")
        if path:
            return path[-2] if path[-1] in {"viewform", "formResponse"} else path[-1]
        raise ValueError("Could not extract form ID from URL.")
    return m.group(1)


def _safe_get(seq: Any, idx: int, default: Any = None) -> Any:
    if isinstance(seq, list) and 0 <= idx < len(seq):
        return seq[idx]
    return default


def _parse_question(q: Any) -> Question | None:
    """Convert one raw question array into a ``Question`` (or ``None`` to skip)."""
    if not isinstance(q, list) or len(q) < 4:
        return None

    title = _safe_get(q, 1) or ""
    description = _safe_get(q, 2) or ""
    type_code = _safe_get(q, 3)
    if type_code is None:
        return None

    try:
        qtype = QuestionType(type_code)
    except ValueError:
        # Unknown type — surface as short-answer fallback if it has entries
        qtype = QuestionType.SHORT_ANSWER

    if not qtype.is_input:
        return None  # Title/section/image dividers — nothing to ask

    entries = _safe_get(q, 4) or []
    if not entries:
        return None
    entry = entries[0]  # We handle the first sub-entry; grids would need expansion

    entry_id = f"entry.{entry[0]}" if _safe_get(entry, 0) is not None else ""
    if not entry_id:
        return None

    required = bool(_safe_get(entry, 2, 0))
    raw_options = _safe_get(entry, 1) or []

    options: list[str] = []
    has_other = False
    if qtype in {QuestionType.MULTIPLE_CHOICE, QuestionType.DROPDOWN, QuestionType.CHECKBOXES}:
        for opt in raw_options:
            label = _safe_get(opt, 0)
            is_other = bool(_safe_get(opt, 4, 0))
            if is_other:
                has_other = True
                continue
            if label is not None:
                options.append(str(label))

    scale_min = scale_max = None
    scale_labels: tuple[str, str] = ("", "")
    if qtype == QuestionType.LINEAR_SCALE and raw_options:
        # Linear scale options look like [["1"], ["2"], ..., ["5"]] plus low/high labels in entry[3]
        try:
            scale_values = [int(_safe_get(o, 0)) for o in raw_options if _safe_get(o, 0) is not None]
            if scale_values:
                scale_min, scale_max = min(scale_values), max(scale_values)
        except (TypeError, ValueError):
            pass
        labels = _safe_get(entry, 3) or []
        if isinstance(labels, list) and len(labels) >= 2:
            scale_labels = (str(labels[0] or ""), str(labels[1] or ""))

    return Question(
        entry_id=entry_id,
        title=str(title),
        description=str(description),
        type=qtype,
        required=required,
        options=options,
        scale_min=scale_min,
        scale_max=scale_max,
        scale_labels=scale_labels,
        has_other=has_other,
        raw=q,
    )
