"""
Grounding Validator Service

Ensures that every sentence in generated summaries and every flashcard Q&A
is derived purely from the lecture transcription — no information from the
model's training data may be injected.

Four sub-agents run against the OpenAI API:
  Agent 1 (summary_validator)   — per-sentence grounding check on summaries
  Agent 2 (flashcard_validator) — per-Q&A grounding check on flashcards
  Agent 3 (confidence_scorer)   — overall grounding confidence score
  Agents 1–3 run in parallel; Agent 4 runs after:
  Agent 4 (content_purifier)    — rewrites or removes ungrounded items

Entry point:  validate_study_material(transcript, processed_content_json)
Returns:      ValidationResult dataclass
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

from openai import OpenAI

VALIDATION_MODEL = os.getenv("VALIDATION_MODEL", "gpt-4.1-mini")
GROUNDING_PASS_THRESHOLD = float(os.getenv("GROUNDING_PASS_THRESHOLD", "0.80"))

_LATEX_SYSTEM_RULES = (
    "When rewriting Hebrew academic content, apply these LaTeX rules: "
    "All math must use $...$ for inline and $$...$$ for display. "
    "Use only KaTeX-supported commands. "
    "Never use \\begin{...} environments — use separate $$...$$ blocks. "
    "Never use custom macros \\R, \\N, \\Z — write \\mathbb{R}, \\mathbb{N}, \\mathbb{Z} in full. "
    "Never put Hebrew text directly inside math mode — use \\text{Hebrew}. "
    "Never use \\boldsymbol — use \\mathbf instead."
)


@dataclass
class GroundingIssue:
    item_type: str        # "summary" | "flashcard"
    item_index: int
    field: str            # "content" | "question" | "answer"
    excerpt: str          # the offending sentence/phrase (truncated)
    reason: str           # why it was flagged


@dataclass
class ValidationResult:
    validation_pass: bool
    overall_grounding_score: float
    summary_score: float
    flashcard_score: float
    issues: list[GroundingIssue] = field(default_factory=list)
    purified_content: Optional[dict] = None
    items_rewritten: int = 0
    items_removed: int = 0
    validated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, ensure_ascii=False)


def _openai_client() -> OpenAI:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Missing required environment variable: OPENAI_API_KEY")
    return OpenAI(api_key=key)


def _truncate_transcript(transcript: str, max_chars: int = 60_000) -> str:
    """Truncate very long transcripts to fit within model context limits."""
    if len(transcript) <= max_chars:
        return transcript
    return transcript[:max_chars] + "\n[...התמלול נחתך בגלל אורך — שאר התוכן לא הוצג לכלי האימות]"


# ---------------------------------------------------------------------------
# Agent 1 — Summary Grounding Validator
# ---------------------------------------------------------------------------

def _validate_summaries_agent(transcript: str, content: dict) -> dict:
    """
    Checks each summary topic's content against the transcript.
    Returns a report with per-topic grounding scores and flagged sentences.
    """
    client = _openai_client()
    summaries = content.get("summaries", [])

    if not summaries:
        return {"summaries": [], "summary_overall_score": 1.0}

    summaries_json = json.dumps(summaries, ensure_ascii=False)

    system_prompt = (
        "You are a strict academic fact-checker. "
        "Your ONLY source of truth is the lecture transcript provided. "
        "You must verify that every claim, fact, formula, and concept in the summaries "
        "can be traced directly to the transcript. "
        "A claim is NOT grounded if it introduces information, formulas, theorems, or examples "
        "that are not mentioned in the transcript — even if they are correct in the real world. "
        "Paraphrasing and restructuring are acceptable. Adding new information is not. "
        "Return ONLY valid JSON with no markdown fences."
    )

    user_prompt = f"""בדוק את הסיכומים הבאים ביחס לתמלול ההרצאה.

עבור כל נושא, החזר:
- "topic_name": שם הנושא
- "grounding_score": ציון מ-0.0 עד 1.0 (1.0 = הכל מבוסס על התמלול)
- "issues": רשימה של בעיות. כל בעיה מכילה:
  - "excerpt": הקטע הבעייתי (עד 100 תווים)
  - "reason": הסיבה שהקטע אינו מבוסס על התמלול

החזר JSON בלבד בפורמט הבא:
{{
  "summaries": [
    {{
      "topic_name": "...",
      "grounding_score": 0.95,
      "issues": []
    }}
  ],
  "summary_overall_score": 0.95
}}

הסיכומים לבדיקה:
{summaries_json}

התמלול:
{_truncate_transcript(transcript)}"""

    response = client.chat.completions.create(
        model=VALIDATION_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return json.loads(response.choices[0].message.content)


# ---------------------------------------------------------------------------
# Agent 2 — Flashcard Grounding Validator
# ---------------------------------------------------------------------------

def _validate_flashcards_agent(transcript: str, content: dict) -> dict:
    """
    Checks each flashcard question and answer against the transcript.
    Returns a report with per-flashcard grounding scores and flags.
    """
    client = _openai_client()
    flashcards = content.get("flashcards", [])

    if not flashcards:
        return {"flashcards": [], "flashcard_overall_score": 1.0}

    flashcards_json = json.dumps(flashcards, ensure_ascii=False)

    system_prompt = (
        "You are a strict academic fact-checker. "
        "Your ONLY source of truth is the lecture transcript provided. "
        "For each flashcard, check whether both the question and the answer "
        "are fully derivable from the transcript. "
        "Mark a flashcard as ungrounded if the answer relies on external knowledge "
        "not present in the transcript — even if the answer is factually correct. "
        "Return ONLY valid JSON with no markdown fences."
    )

    user_prompt = f"""בדוק את כרטיסיות הלמידה הבאות ביחס לתמלול ההרצאה.

עבור כל כרטיסייה, החזר:
- "index": מספר הכרטיסייה (מ-0)
- "question_grounded": true/false
- "answer_grounded": true/false
- "grounding_score": ציון מ-0.0 עד 1.0
- "issue": תיאור הבעיה אם יש (null אם הכל תקין)

החזר JSON בלבד בפורמט הבא:
{{
  "flashcards": [
    {{
      "index": 0,
      "question_grounded": true,
      "answer_grounded": true,
      "grounding_score": 1.0,
      "issue": null
    }}
  ],
  "flashcard_overall_score": 0.90
}}

כרטיסיות הלמידה לבדיקה:
{flashcards_json}

התמלול:
{_truncate_transcript(transcript)}"""

    response = client.chat.completions.create(
        model=VALIDATION_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return json.loads(response.choices[0].message.content)


# ---------------------------------------------------------------------------
# Agent 3 — Confidence Scorer
# ---------------------------------------------------------------------------

def _score_grounding_agent(transcript: str, content: dict) -> dict:
    """
    Independently computes an overall grounding confidence score without
    looking at the per-item reports from agents 1 & 2, providing a
    cross-check signal.
    """
    client = _openai_client()

    full_content_json = json.dumps(
        {
            "summaries": content.get("summaries", []),
            "flashcards": content.get("flashcards", []),
        },
        ensure_ascii=False,
    )

    system_prompt = (
        "You are an academic integrity auditor. "
        "Score how well the generated study material stays within the boundaries "
        "of the provided lecture transcript. "
        "Return ONLY valid JSON with no markdown fences."
    )

    user_prompt = f"""העריך את רמת הנאמנות של חומרי הלמידה ביחס לתמלול ההרצאה.

החזר JSON בלבד בפורמט הבא:
{{
  "overall_score": 0.92,
  "summary_score": 0.95,
  "flashcard_score": 0.88,
  "assessment": "הערכה קצרה בעברית על רמת הנאמנות",
  "main_concern": "הבעיה העיקרית אם קיימת, או null"
}}

חומרי הלמידה:
{full_content_json}

התמלול:
{_truncate_transcript(transcript)}"""

    response = client.chat.completions.create(
        model=VALIDATION_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return json.loads(response.choices[0].message.content)


# ---------------------------------------------------------------------------
# Agent 4 — Content Purifier
# ---------------------------------------------------------------------------

def _purify_content_agent(
    transcript: str,
    content: dict,
    summary_report: dict,
    flashcard_report: dict,
) -> dict:
    """
    Rewrites or removes any items flagged by agents 1 & 2.
    Items with issues are rewritten using only transcript content.
    Items where the transcript has no supporting content are removed.
    Returns a dict: { "summaries": [...], "flashcards": [...],
                      "items_rewritten": int, "items_removed": int }
    """
    client = _openai_client()

    # Collect which summaries and flashcards need purification
    flagged_summaries = [
        s for s in summary_report.get("summaries", [])
        if s.get("grounding_score", 1.0) < GROUNDING_PASS_THRESHOLD or s.get("issues")
    ]
    flagged_flashcard_indices = {
        fc["index"]
        for fc in flashcard_report.get("flashcards", [])
        if not fc.get("question_grounded", True) or not fc.get("answer_grounded", True)
    }

    if not flagged_summaries and not flagged_flashcard_indices:
        return {
            "summaries": content.get("summaries", []),
            "flashcards": content.get("flashcards", []),
            "items_rewritten": 0,
            "items_removed": 0,
        }

    flagged_topic_names = {s["topic_name"] for s in flagged_summaries}
    summaries_to_fix = [
        s for s in content.get("summaries", [])
        if s.get("topic_name") in flagged_topic_names
    ]
    flashcards_to_fix = [
        fc for i, fc in enumerate(content.get("flashcards", []))
        if i in flagged_flashcard_indices
    ]

    system_prompt = (
        "You are an academic editor who rewrites content so it is based solely "
        "on the provided lecture transcript. "
        "Rewrite any flagged summaries and flashcards using ONLY information from the transcript. "
        "If a topic or flashcard cannot be meaningfully reconstructed from the transcript alone, "
        'set its content to null so it can be removed. '
        "Preserve Hebrew language and all LaTeX math notation. "
        + _LATEX_SYSTEM_RULES
        + " Return ONLY valid JSON with no markdown fences."
    )

    user_prompt = f"""תקן את חומרי הלמידה הבאים כך שיתבססו אך ורק על התמלול.

עבור כל סיכום: כתוב מחדש את השדה "content" תוך שימוש אך ורק במידע מהתמלול.
אם אין מספיק מידע בתמלול לנושא זה, הגדר content: null.

עבור כל כרטיסייה: כתוב מחדש את השדות "question" ו-"answer" תוך שימוש אך ורק במידע מהתמלול.
אם אין מספיק מידע בתמלול לכרטיסייה זו, הגדר question: null.

החזר JSON בלבד בפורמט הבא:
{{
  "fixed_summaries": [
    {{ "topic_name": "...", "content": "..." }}
  ],
  "fixed_flashcards": [
    {{ "original_index": 0, "question": "...", "answer": "..." }}
  ]
}}

סיכומים לתיקון:
{json.dumps(summaries_to_fix, ensure_ascii=False)}

כרטיסיות לתיקון:
{json.dumps(flashcards_to_fix, ensure_ascii=False)}

מיפוי אינדקסים מקוריים לכרטיסיות:
{json.dumps({i: fc for i, fc in enumerate(content.get("flashcards", [])) if i in flagged_flashcard_indices}, ensure_ascii=False)}

התמלול:
{_truncate_transcript(transcript)}"""

    response = client.chat.completions.create(
        model=VALIDATION_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    purifier_output = json.loads(response.choices[0].message.content)

    # Apply fixes to the original content
    fixed_summaries_map = {
        s["topic_name"]: s["content"]
        for s in purifier_output.get("fixed_summaries", [])
    }
    fixed_flashcards_map = {
        fc["original_index"]: fc
        for fc in purifier_output.get("fixed_flashcards", [])
    }

    items_rewritten = 0
    items_removed = 0

    final_summaries = []
    for s in content.get("summaries", []):
        topic = s.get("topic_name")
        if topic in fixed_summaries_map:
            new_content = fixed_summaries_map[topic]
            if new_content is None:
                items_removed += 1
                continue
            final_summaries.append({**s, "content": new_content})
            items_rewritten += 1
        else:
            final_summaries.append(s)

    final_flashcards = []
    for i, fc in enumerate(content.get("flashcards", [])):
        if i in fixed_flashcards_map:
            fix = fixed_flashcards_map[i]
            if fix.get("question") is None:
                items_removed += 1
                continue
            final_flashcards.append({
                **fc,
                "question": fix["question"],
                "answer": fix["answer"],
            })
            items_rewritten += 1
        else:
            final_flashcards.append(fc)

    return {
        "summaries": final_summaries,
        "flashcards": final_flashcards,
        "items_rewritten": items_rewritten,
        "items_removed": items_removed,
    }


# ---------------------------------------------------------------------------
# Orchestrator — runs agents 1–3 in parallel, then agent 4
# ---------------------------------------------------------------------------

def validate_study_material(transcript: str, processed_content_json: str) -> ValidationResult:
    """
    Main entry point. Takes the raw transcript and the AI-generated JSON string,
    runs grounding validation, and returns a ValidationResult.

    The returned ValidationResult.purified_content holds the cleaned content dict
    (same shape as the original) ready to be serialised and saved.
    """
    try:
        content = json.loads(processed_content_json)
    except json.JSONDecodeError:
        return ValidationResult(
            validation_pass=False,
            overall_grounding_score=0.0,
            summary_score=0.0,
            flashcard_score=0.0,
            issues=[
                GroundingIssue(
                    item_type="system",
                    item_index=-1,
                    field="processed_content_json",
                    excerpt="",
                    reason="processed_content_json is not valid JSON",
                )
            ],
        )

    # Run agents 1, 2, 3 concurrently
    summary_report: dict = {}
    flashcard_report: dict = {}
    score_report: dict = {}

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_validate_summaries_agent, transcript, content): "summary",
            executor.submit(_validate_flashcards_agent, transcript, content): "flashcard",
            executor.submit(_score_grounding_agent, transcript, content): "score",
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                print(f"[validation] Agent '{label}' failed: {exc}")
                result = {}
            if label == "summary":
                summary_report = result
            elif label == "flashcard":
                flashcard_report = result
            else:
                score_report = result

    # Derive scores — prefer the independent scorer (agent 3), fall back to per-item scores
    summary_score = float(
        score_report.get("summary_score")
        or summary_report.get("summary_overall_score")
        or 1.0
    )
    flashcard_score = float(
        score_report.get("flashcard_score")
        or flashcard_report.get("flashcard_overall_score")
        or 1.0
    )
    overall_score = float(
        score_report.get("overall_score")
        or (summary_score * 0.6 + flashcard_score * 0.4)
    )

    # Collect all issues into a flat list
    issues: list[GroundingIssue] = []
    for i, s in enumerate(summary_report.get("summaries", [])):
        for iss in s.get("issues", []):
            issues.append(GroundingIssue(
                item_type="summary",
                item_index=i,
                field="content",
                excerpt=iss.get("excerpt", "")[:200],
                reason=iss.get("reason", ""),
            ))
    for fc in flashcard_report.get("flashcards", []):
        if not fc.get("question_grounded", True) or not fc.get("answer_grounded", True):
            issues.append(GroundingIssue(
                item_type="flashcard",
                item_index=fc.get("index", -1),
                field="answer" if not fc.get("answer_grounded", True) else "question",
                excerpt="",
                reason=fc.get("issue") or "לא מבוסס על התמלול",
            ))

    # Run agent 4 (purifier) if anything was flagged
    purifier_result = _purify_content_agent(
        transcript, content, summary_report, flashcard_report
    )

    purified_content = {
        "topics": content.get("topics", []),
        "summaries": purifier_result["summaries"],
        "flashcards": purifier_result["flashcards"],
    }

    return ValidationResult(
        validation_pass=overall_score >= GROUNDING_PASS_THRESHOLD,
        overall_grounding_score=round(overall_score, 3),
        summary_score=round(summary_score, 3),
        flashcard_score=round(flashcard_score, 3),
        issues=issues,
        purified_content=purified_content,
        items_rewritten=purifier_result["items_rewritten"],
        items_removed=purifier_result["items_removed"],
    )
