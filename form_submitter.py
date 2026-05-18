"""Submit collected answers to a public Google Form via HTTP POST.

The official Forms API does not support response submission; the documented
workaround is to POST form-encoded ``entry.XXXX`` fields to the form's
``/formResponse`` URL. This module formats each answer per its question type
and performs the submission.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Any
from urllib.parse import urlparse

import requests

from form_parser import Form, Question, QuestionType, USER_AGENT


@dataclass
class SubmissionResult:
    ok: bool
    status_code: int
    detail: str
    response_html: str = ""   # captured for debugging; written to disk by the caller


def build_payload(form: Form, answers: dict[str, Any]) -> list[tuple[str, str]]:
    """Convert ``{entry_id: value}`` into the form-encoded list Google expects."""
    payload: list[tuple[str, str]] = []
    for q in form.questions:
        value = answers.get(q.entry_id)
        if value is None or value == "" or value == []:
            continue
        payload.extend(_encode_answer(q, value))
    return payload


def _encode_answer(q: Question, value: Any) -> list[tuple[str, str]]:
    eid = q.entry_id
    if q.type == QuestionType.CHECKBOXES:
        values = value if isinstance(value, list) else [value]
        return [(eid, str(v)) for v in values]

    if q.type == QuestionType.DATE:
        if isinstance(value, date):
            return [
                (f"{eid}_year", f"{value.year:04d}"),
                (f"{eid}_month", f"{value.month:02d}"),
                (f"{eid}_day", f"{value.day:02d}"),
            ]
        # Accept ISO string YYYY-MM-DD
        parts = str(value).split("-")
        if len(parts) == 3:
            y, m, d = parts
            return [(f"{eid}_year", y), (f"{eid}_month", m), (f"{eid}_day", d)]
        return [(eid, str(value))]

    if q.type == QuestionType.TIME:
        if isinstance(value, time):
            return [(f"{eid}_hour", f"{value.hour:02d}"), (f"{eid}_minute", f"{value.minute:02d}")]
        parts = str(value).split(":")
        if len(parts) >= 2:
            return [(f"{eid}_hour", parts[0]), (f"{eid}_minute", parts[1])]
        return [(eid, str(value))]

    if q.type == QuestionType.FILE_UPLOAD:
        # Anonymous submission cannot upload files; respondent must sign in to Google.
        # We skip the field here and let the caller flag the limitation.
        return []

    return [(eid, str(value))]


def submit(
    form: Form,
    answers: dict[str, Any],
    *,
    session: requests.Session | None = None,
    verify: bool = True,
) -> SubmissionResult:
    """POST the answers to the form's response endpoint."""
    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", USER_AGENT)

    payload = build_payload(form, answers)
    # Mirror the fields the real browser submission sends. ``fbzx`` is the
    # form-session token Google requires; without it the POST is accepted
    # with 200 but the response is silently discarded (never appears in the
    # form's Responses tab). ``fvv``, ``pageHistory`` and ``submit`` round
    # out the browser-like request.
    if form.fbzx:
        payload.append(("fbzx", form.fbzx))
    payload.extend([("fvv", "1"), ("draftResponse", "[]"), ("pageHistory", "0"), ("submit", "Submit")])

    resp = sess.post(form.submit_url, data=payload, timeout=20, allow_redirects=True, verify=verify)
    body = resp.text

    # A successful submission returns the confirmation page (no form inputs).
    # A failed submission re-renders the form with the user's answers + an
    # error banner — the give-away is the form's <input name="entry.XXX">
    # tags reappearing in the body.
    form_rerendered = any(f'name="{q.entry_id}"' in body for q in form.questions[:5])
    has_legacy_marker = (
        "Your response has been recorded" in body
        or "freebirdFormviewerViewResponseConfirmationMessage" in body
    )
    on_response_path = urlparse(resp.url).path.endswith("/formResponse")
    ok = resp.status_code == 200 and not form_rerendered and (has_legacy_marker or on_response_path)

    if ok:
        detail = "Submitted."
    elif form_rerendered:
        detail = "Google re-rendered the form (likely a required field is missing or invalid)."
    else:
        detail = f"Submission not confirmed (HTTP {resp.status_code})."

    return SubmissionResult(
        ok=ok, status_code=resp.status_code, detail=detail, response_html=body
    )
