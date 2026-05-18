"""Generate PDF artifacts that accompany each session JSON.

Two PDFs per session:
  * ``<id>_conversation.pdf`` — the full chat transcript, role-tagged.
  * ``<id>_filled_form.pdf``  — a clean form-style readout of question + answer.

Both live in the same folder as the session JSON (``incomplete/`` while the
conversation is ongoing or unsubmitted, ``complete/`` after a confirmed
submission). They are regenerated whenever the conversation reaches a
meaningful end state.
"""
from __future__ import annotations

import io
from typing import Any, Iterable

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from form_parser import Form, QuestionType


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontSize=18, spaceAfter=6, textColor=colors.HexColor("#202124")
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontSize=10, textColor=colors.HexColor("#5f6368"), spaceAfter=18
        ),
        "section": ParagraphStyle(
            "section",
            parent=base["Heading2"],
            fontSize=12,
            spaceBefore=14,
            spaceAfter=4,
            textColor=colors.HexColor("#1a73e8"),
        ),
        "qlabel": ParagraphStyle(
            "qlabel",
            parent=base["Normal"],
            fontSize=11,
            leading=14,
            spaceAfter=2,
            textColor=colors.HexColor("#202124"),
        ),
        "qmeta": ParagraphStyle(
            "qmeta",
            parent=base["Normal"],
            fontSize=8,
            textColor=colors.HexColor("#80868b"),
            spaceAfter=4,
        ),
        "answer": ParagraphStyle(
            "answer",
            parent=base["Normal"],
            fontSize=11,
            leading=14,
            leftIndent=10,
            textColor=colors.HexColor("#202124"),
            spaceAfter=10,
        ),
        "skipped": ParagraphStyle(
            "skipped",
            parent=base["Italic"],
            fontSize=10,
            leftIndent=10,
            textColor=colors.HexColor("#9aa0a6"),
            spaceAfter=10,
        ),
        "user_msg": ParagraphStyle(
            "user_msg",
            parent=base["Normal"],
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#1a73e8"),
        ),
        "assistant_msg": ParagraphStyle(
            "assistant_msg",
            parent=base["Normal"],
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#202124"),
        ),
    }


def _escape(text: Any) -> str:
    """Escape characters that reportlab's Paragraph mini-HTML treats specially."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _format_value(value: Any) -> str:
    if value is None or value == "" or value == []:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def write_conversation_pdf(
    form: Form,
    conversation: Iterable[dict[str, str]],
    *,
    session_id: str,
    started_at: str,
    updated_at: str,
    status: str,
) -> bytes:
    """Render the chat transcript as a PDF and return the bytes.

    Decoupled from storage so the caller can write to local disk, GCS, or
    anywhere else.
    """
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=f"Conversation — {form.title}",
    )
    story: list[Any] = [
        Paragraph(f"Conversation: {_escape(form.title)}", styles["title"]),
        Paragraph(
            f"Session {_escape(session_id)} &middot; started {_escape(started_at)} "
            f"&middot; updated {_escape(updated_at)} &middot; status: <b>{_escape(status)}</b>",
            styles["subtitle"],
        ),
    ]

    rows: list[list[Any]] = [["Speaker", "Message"]]
    for turn in conversation:
        role = (turn.get("role") or "").capitalize() or "?"
        content = _escape(turn.get("content", ""))
        style = styles["user_msg"] if turn.get("role") == "user" else styles["assistant_msg"]
        rows.append([role, Paragraph(content, style)])

    table = Table(rows, colWidths=[0.9 * inch, 5.6 * inch], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f3f4")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#202124")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.HexColor("#dadce0")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfc")]),
                ("FONTSIZE", (0, 1), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 1), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    return buf.getvalue()


def write_filled_form_pdf(
    form: Form,
    answers: dict[str, Any],
    *,
    session_id: str,
    updated_at: str,
    status: str,
    submission_ok: bool | None,
) -> bytes:
    """Render the form questions with the user's answers and return PDF bytes."""
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=f"Filled form — {form.title}",
    )

    if submission_ok is True:
        status_label = "Submitted to Google Forms"
    elif submission_ok is False:
        status_label = "Submission failed"
    else:
        status_label = f"Status: {status}"

    story: list[Any] = [
        Paragraph(_escape(form.title), styles["title"]),
        Paragraph(
            f"Session {_escape(session_id)} &middot; updated {_escape(updated_at)} "
            f"&middot; <b>{_escape(status_label)}</b>",
            styles["subtitle"],
        ),
    ]
    if form.description:
        story.append(Paragraph(_escape(form.description), styles["qmeta"]))
        story.append(Spacer(1, 8))

    for i, q in enumerate(form.questions, 1):
        required_tag = "*required" if q.required else "optional"
        story.append(
            Paragraph(f"{i}. {_escape(q.title)}", styles["qlabel"])
        )
        story.append(
            Paragraph(
                f"{q.type.name.replace('_', ' ').title()} &middot; {required_tag}",
                styles["qmeta"],
            )
        )
        if q.description:
            story.append(Paragraph(_escape(q.description), styles["qmeta"]))

        value = answers.get(q.entry_id)
        text = _format_value(value)
        if not text:
            story.append(Paragraph("— skipped —", styles["skipped"]))
        else:
            story.append(Paragraph(_escape(text), styles["answer"]))

    doc.build(story)
    return buf.getvalue()
