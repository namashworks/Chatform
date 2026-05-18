"""Gemini-powered conversation logic for filling a Google Form.

The chatbot has two responsibilities the LLM helps with:
    1. Open each question with a friendly, natural phrasing.
    2. Interpret each user reply: either accept it as an answer (mapping
       free-form text to the canonical option/value the form expects) or
       provide clarification when the user is confused.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types as genai_types

from form_parser import Question, QuestionType


# A curated set of languages the chatbot can converse in. Values are the
# language label we tell Gemini to respond in; the model handles the actual
# rendering. ``"English"`` is the canonical default and matches the form's
# native language for most users.
SUPPORTED_LANGUAGES: dict[str, str] = {
    "English": "English",
    "Español (Spanish)": "Spanish",
    "Français (French)": "French",
    "Deutsch (German)": "German",
    "Português (Portuguese)": "Portuguese",
    "Italiano (Italian)": "Italian",
    "中文 (Chinese - Simplified)": "Simplified Chinese",
    "日本語 (Japanese)": "Japanese",
    "한국어 (Korean)": "Korean",
    "العربية (Arabic)": "Arabic",
    "हिन्दी (Hindi)": "Hindi",
    "Русский (Russian)": "Russian",
}
DEFAULT_LANGUAGE = "English"


SYSTEM_INSTRUCTION = """You are a warm, patient assistant guiding a user through a Google Form one question at a time.

For every turn you receive:
- The form question (text, type, options, validation hints, required flag).
- The conversation so far for THIS question.
- The user's latest reply.

You will always do ONE of three things:

==============================================================================
A. ACCEPT  — return the CANONICAL value the form expects, never the user's
             verbose reply verbatim. Strip conversational filler and
             extract only the data the form actually needs.
==============================================================================

Examples (short_answer):
  Question "Name"            user "My name is Tasnin Goran"
                             -> answer: "Tasnin Goran"
  Question "Age"             user "I'm 30 years old"
                             -> answer: "30"
  Question "Address"         user "ohh my address is ummm 123 Main St, Perth WA 6101"
                             -> answer: "123 Main St, Perth WA 6101"
  Question "Email"           user "sure, it's tasnin.123@gmail.com"
                             -> answer: "tasnin.123@gmail.com"
  Question "Phone number"    user "yeah it's +61 412 345 678"
                             -> answer: "+61 412 345 678"

Paragraph fields are a bit different — keep most of what the user said,
only strip leading meta-fluff ("okay here goes:", "umm let me think:").
The user's actual prose IS the answer.

Choice-based mapping:
  multiple_choice / dropdown -> pick exactly one option from the list
                                (the answer string MUST equal one option exactly).
  checkboxes                 -> list of 1+ option strings, each exactly matching.
  linear_scale               -> integer within [scale_min, scale_max].
  date                       -> "YYYY-MM-DD".
  time                       -> "HH:MM" (24-hour).
  file_upload                -> "uploaded" once the user has used the file widget.

==============================================================================
B. ACCEPT AS SKIP — when the user shows skip intent on an OPTIONAL question.
==============================================================================

Skip intent looks like: "I can't share", "I'd rather not", "skip", "pass",
"no thanks", "rather not say", "prefer not to answer", "don't want to share".

  Optional + skip intent  -> action: "accept", answer: null,
                             message: warm one-liner acknowledging the skip.
  Required + skip intent  -> action: "clarify", explain (briefly, warmly) that
                             this one's needed to continue. Do NOT pretend
                             it's optional. Do NOT invent a value.

IMPORTANT: distinguish "skip intent" from a literal "no" answer:
  Question "Do you have any comments?"  user "no"            -> answer: "no" (real answer)
  Question "Phone number"               user "no, can't share" -> SKIP intent

==============================================================================
C. CLARIFY — when the user is confused, off-topic, or gave an ambiguous reply.
==============================================================================

Re-explain in simpler language, give a concrete example, list the options.
Be brief and warm. Never lecture.

==============================================================================
Language handling
==============================================================================
You may be told to converse in a specific language (e.g. "Spanish",
"Hindi"). When that's set:

- Write the "message" field entirely in that language. Be natural, not stilted.
- For CHOICE-BASED questions (multiple_choice / dropdown / checkboxes),
  the "answer" MUST still match one of the form's option strings EXACTLY,
  even if those options are in English. Map the user's translated reply to
  the canonical English option. Example: form option list is
  ["Small", "Medium", "Large"]; user (in Spanish) says "mediano" -> answer: "Medium".
- For TEXT-BASED questions (short_answer / paragraph), the "answer" can
  stay in the user's language — that's what they want submitted to the form.

==============================================================================
General rules
==============================================================================
- Never invent an answer. If you're unsure what they meant, CLARIFY.
- Respect the user's tone. Be conversational, not robotic.
- The "message" is what we'll show the user. The "answer" is what gets
  submitted to Google Forms — so the "answer" must be clean and canonical.

Respond with JSON only, no prose:
{
  "action": "accept" | "clarify",
  "message": "<what to say back to the user>",
  "answer": <canonical value when action=accept, else null>
}
"""


@dataclass
class TurnResult:
    action: str          # "accept" or "clarify"
    message: str         # text to show the user
    answer: Any          # canonical answer value when accepted, else None


class Chatbot:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def ping(self) -> None:
        """Make a minimal generation call. Raises on auth / model errors.

        Used by the generator UI to fail fast on a bad API key + model combo
        before the tenant is saved.
        """
        self._client.models.generate_content(
            model=self._model,
            contents="ping",
            config=genai_types.GenerateContentConfig(max_output_tokens=8, temperature=0.0),
        )

    def phrase_question(
        self,
        q: Question,
        *,
        is_first: bool = False,
        language: str = DEFAULT_LANGUAGE,
    ) -> str:
        """Return a friendly opening line for the question, in ``language``."""
        prompt = self._compose_phrase_prompt(q, is_first=is_first, language=language)
        sys_inst = (
            "You are a warm, concise assistant who asks one form question at a time. "
            f"Always write your reply in {language}."
        )
        resp = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=sys_inst,
                temperature=0.6,
                # Hard ceiling so slower models (e.g. 2.5-pro) don't ramble.
                # An opening is at most 1-2 sentences plus an options list.
                max_output_tokens=300,
            ),
        )
        return (resp.text or "").strip() or self._fallback_phrasing(q)

    def process_reply(
        self,
        q: Question,
        turn_history: list[dict[str, str]],
        user_reply: str,
        *,
        language: str = DEFAULT_LANGUAGE,
    ) -> TurnResult:
        """Decide whether ``user_reply`` answers ``q`` or needs clarification."""
        context = self._compose_process_prompt(q, turn_history, user_reply, language=language)
        sys_inst = SYSTEM_INSTRUCTION + f"\n\nCONVERSATION LANGUAGE: {language}"
        resp = self._client.models.generate_content(
            model=self._model,
            contents=context,
            config=genai_types.GenerateContentConfig(
                system_instruction=sys_inst,
                temperature=0.2,
                response_mime_type="application/json",
                # Big enough to echo a long paragraph answer back as canonical
                # data, small enough to stop a runaway generation.
                max_output_tokens=1024,
            ),
        )
        raw = (resp.text or "").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return TurnResult(
                action="clarify",
                message="Sorry — I didn't catch that. Could you say it another way?",
                answer=None,
            )
        action = parsed.get("action", "clarify")
        message = parsed.get("message") or ""
        answer = parsed.get("answer") if action == "accept" else None
        if action == "accept" and not self._validate(q, answer):
            return TurnResult(
                action="clarify",
                message=message or "I want to make sure I got that right — could you confirm?",
                answer=None,
            )
        return TurnResult(action=action, message=message, answer=answer)

    # ---------- helpers ----------

    def _compose_phrase_prompt(self, q: Question, *, is_first: bool, language: str) -> str:
        lead = (
            "This is the first question — greet the user briefly, then ask:"
            if is_first
            else "Ask the user:"
        )
        lang_note = (
            f"\n\nIMPORTANT: write your reply entirely in {language}."
            if language and language != DEFAULT_LANGUAGE
            else ""
        )
        return (
            f"{lead}\n\n"
            f"Question: {q.title}\n"
            f"Description: {q.description or '(none)'}\n"
            f"Type: {q.type.name}\n"
            f"Required: {q.required}\n"
            f"{self._format_constraints(q)}\n\n"
            "Phrase it conversationally in one or two short sentences. "
            "If there are options, list them clearly (e.g. as a short bulleted list)."
            f"{lang_note}"
        )

    def _compose_process_prompt(
        self,
        q: Question,
        history: list[dict[str, str]],
        user_reply: str,
        *,
        language: str,
    ) -> str:
        history_lines = "\n".join(f"{turn['role']}: {turn['content']}" for turn in history) or "(none yet)"
        return (
            f"QUESTION:\n"
            f"  title: {q.title}\n"
            f"  description: {q.description or '(none)'}\n"
            f"  type: {q.type.name}\n"
            f"  required: {q.required}\n"
            f"{self._format_constraints(q)}\n\n"
            f"CONVERSATION LANGUAGE: {language}\n\n"
            f"CONVERSATION SO FAR FOR THIS QUESTION:\n{history_lines}\n\n"
            f"USER'S LATEST REPLY:\n{user_reply}\n\n"
            "Decide: accept or clarify. Respond with the JSON object only."
        )

    def _format_constraints(self, q: Question) -> str:
        if q.type in {QuestionType.MULTIPLE_CHOICE, QuestionType.DROPDOWN}:
            opts = ", ".join(f'"{o}"' for o in q.options)
            other = " (also allows free-text Other)" if q.has_other else ""
            return f"  options (pick one): [{opts}]{other}"
        if q.type == QuestionType.CHECKBOXES:
            opts = ", ".join(f'"{o}"' for o in q.options)
            other = " (also allows free-text Other)" if q.has_other else ""
            return f"  options (pick one or more): [{opts}]{other}"
        if q.type == QuestionType.LINEAR_SCALE:
            return f"  scale: integer from {q.scale_min} to {q.scale_max} (low={q.scale_labels[0]!r}, high={q.scale_labels[1]!r})"
        if q.type == QuestionType.DATE:
            return "  format: ISO date YYYY-MM-DD"
        if q.type == QuestionType.TIME:
            return "  format: 24-hour HH:MM"
        return ""

    def _fallback_phrasing(self, q: Question) -> str:
        base = q.title.strip()
        if q.type in {QuestionType.MULTIPLE_CHOICE, QuestionType.DROPDOWN}:
            return base + "\n\nOptions: " + ", ".join(q.options)
        if q.type == QuestionType.CHECKBOXES:
            return base + "\n\nPick any: " + ", ".join(q.options)
        return base

    def _validate(self, q: Question, value: Any) -> bool:
        if value is None:
            return not q.required
        if q.type in {QuestionType.MULTIPLE_CHOICE, QuestionType.DROPDOWN}:
            return isinstance(value, str) and (value in q.options or q.has_other)
        if q.type == QuestionType.CHECKBOXES:
            if not isinstance(value, list) or not value:
                return False
            return all(isinstance(v, str) and (v in q.options or q.has_other) for v in value)
        if q.type == QuestionType.LINEAR_SCALE:
            try:
                ivalue = int(value)
            except (TypeError, ValueError):
                return False
            lo = q.scale_min if q.scale_min is not None else ivalue
            hi = q.scale_max if q.scale_max is not None else ivalue
            return lo <= ivalue <= hi
        if q.type == QuestionType.DATE:
            return isinstance(value, str) and len(value.split("-")) == 3
        if q.type == QuestionType.TIME:
            return isinstance(value, str) and ":" in value
        if q.type in {QuestionType.SHORT_ANSWER, QuestionType.PARAGRAPH}:
            return isinstance(value, str) and bool(value.strip())
        return True
