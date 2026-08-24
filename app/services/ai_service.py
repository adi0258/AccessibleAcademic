import os
import re
import time
from typing import Optional

import requests
from openai import BadRequestError, OpenAI

from app.core.config import TRANSCRIPTION_TIMEOUT_MINUTES


def _fix_json_latex_escapes(s: str) -> str:
    """
    Restore LaTeX commands that were corrupted by JSON escape-sequence parsing.

    The AI sometimes outputs LaTeX backslash commands (\\boldsymbol, \\frac,
    \\rightarrow …) with a single backslash inside a JSON string.  The JSON
    parser then converts \\b → U+0008 (backspace), \\f → U+000C (form feed),
    \\r → U+000D (CR), \\t → U+0009 (tab), \\n → U+000A (LF).

    This function reverses that, so the stored string contains proper LaTeX.
    Called on the raw AI content string before it is saved to the database.
    """
    s = re.sub(r'\x08([a-zA-Z])', r'\\b\1', s)   # backspace + letter  → \\b...
    s = re.sub(r'\x0c([a-zA-Z])', r'\\f\1', s)   # form feed + letter  → \\f...
    s = re.sub(r'\x0d([a-zA-Z])', r'\\r\1', s)   # CR + letter         → \\r...
    s = re.sub(r'\x09([a-zA-Z])', r'\\t\1', s)   # tab + letter        → \\t...
    s = re.sub(r'\x0a([a-zA-Z])', r'\\n\1', s)   # LF + letter         → \\n...
    return s


ASSEMBLY_API_KEY = os.getenv("ASSEMBLY_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODELS = [
    m.strip()
    for m in os.getenv("OPENAI_MODELS", "gpt-4.1-mini,gpt-4o-mini,gpt-4.1").split(",")
    if m.strip()
]


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _assembly_headers() -> dict[str, str]:
    return {"authorization": _require_env("ASSEMBLY_API_KEY")}


def _response_json(response: requests.Response, step: str) -> dict:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = (response.text or "").strip()
        detail = body[:300] if body else "<empty response body>"
        raise RuntimeError(
            f"AssemblyAI {step} failed with status {response.status_code}: {detail}"
        ) from exc

    try:
        return response.json()
    except ValueError as exc:
        body = (response.text or "").strip()
        detail = body[:300] if body else "<empty response body>"
        raise RuntimeError(f"AssemblyAI {step} returned invalid JSON: {detail}") from exc


def transcribe_audio(filename: str, lecture_id: Optional[int] = None, progress_cb=None):
    """
    Transcribe audio from a local file path or a remote URL.
    If filename is a URL it is passed directly to AssemblyAI, skipping the upload step.
    """
    headers = _assembly_headers()

    if filename.startswith("http://") or filename.startswith("https://"):
        audio_url = filename
    else:
        def read_file(fn):
            with open(fn, "rb") as file_obj:
                while chunk := file_obj.read(5242880):
                    yield chunk

        if lecture_id is not None and progress_cb:
            progress_cb(lecture_id, "uploading", 5)

        up_res = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers=headers,
            data=read_file(filename),
            timeout=120,
        )
        upload_payload = _response_json(up_res, "upload")
        audio_url = upload_payload.get("upload_url")
        if not audio_url:
            raise RuntimeError(f"AssemblyAI upload response missing upload_url: {upload_payload}")

    tx_res = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        json={"audio_url": audio_url, "language_code": "he"},
        headers=headers,
        timeout=30,
    )
    transcript_payload = _response_json(tx_res, "transcript submission")
    tx_id = transcript_payload.get("id")
    if not tx_id:
        raise RuntimeError(f"AssemblyAI transcript response missing id: {transcript_payload}")

    if lecture_id is not None and progress_cb:
        progress_cb(lecture_id, "transcribing", 15, assemblyai_transcript_id=tx_id)

    deadline = time.monotonic() + TRANSCRIPTION_TIMEOUT_MINUTES * 60
    consecutive_poll_errors = 0
    last_progress_report = 0.0
    status = "unknown"

    while True:
        if time.monotonic() > deadline:
            # Without a ceiling this loop is unbounded: a transcript stuck in
            # "processing" would be polled until the whole invocation is
            # killed, leaving the lecture frozen mid-pipeline with nothing
            # recorded. Failing explicitly makes it a retryable error instead.
            raise RuntimeError(
                f"AssemblyAI did not finish within {TRANSCRIPTION_TIMEOUT_MINUTES} minutes "
                f"(transcript {tx_id}, last status '{status}')"
            )

        try:
            poll_res = requests.get(
                f"https://api.assemblyai.com/v2/transcript/{tx_id}",
                headers=headers,
                timeout=30,
            )
            res = _response_json(poll_res, "status polling")
            consecutive_poll_errors = 0
        except (requests.RequestException, RuntimeError) as e:
            # A blip while polling shouldn't throw away a transcription that
            # is already running and already paid for — the job is server-side
            # and keeps going, so back off and ask again.
            consecutive_poll_errors += 1
            if consecutive_poll_errors >= 5:
                raise RuntimeError(
                    f"AssemblyAI polling failed {consecutive_poll_errors} times in a row: {e}"
                ) from e
            time.sleep(min(30, 3 * consecutive_poll_errors))
            continue

        status = res.get("status", "")
        # Report at most every 30s. The status is only interesting when it
        # changes, and this loop ticks every 3 seconds — writing to the
        # database each time would be hundreds of pointless writes per
        # lecture, all saying the same thing.
        now = time.monotonic()
        if lecture_id is not None and progress_cb and now - last_progress_report >= 30:
            if status == "queued":
                progress_cb(lecture_id, "transcribing", 20)
                last_progress_report = now
            elif status == "processing":
                progress_cb(lecture_id, "transcribing", 50)
                last_progress_report = now

        if status == "completed":
            return {"text": res.get("text") or "", "words": res.get("words", [])}
        if status == "error":
            raise RuntimeError(res.get("error", "Transcription failed"))
        time.sleep(3)


def generate_study_material(text: str):
    client = OpenAI(api_key=_require_env("OPENAI_API_KEY"))

    # Raw f-string: the LaTeX examples below are full of backslash sequences,
    # and in a normal string Python eats them. "\to" in the limit example was
    # being turned into an actual tab character, so the one worked example of
    # a limit that the model is asked to imitate reached it malformed.
    prompt = rf"""
    נתח את תמלול ההרצאה האקדמית הבא בעברית והחזר JSON בלבד.
    הסיכומים חייבים להיות מקצועיים, אקדמיים ומפורטים מאוד.

    חוק חובה — סימון LaTeX: כל ביטוי מתמטי ללא יוצא מן הכלל חייב להיכתב בסימון LaTeX.
    אסור לכתוב מתמטיקה במילים. דוגמאות לסגנון הנדרש:
    - במקום "הנגזרת של f בנקודה x0" → כתוב: $f'(x_0)$
    - במקום "f הרכבה עם g" → כתוב: $(f \circ g)(x)$
    - כלל השרשרת: $(f \circ g)'(x_0) = g'(f(x_0)) \cdot f'(x_0)$
    - גבול: $\lim_{{x \to x_0}} f(x) = L$
    - אינטגרל: $\int_a^b f(x)\,dx$
    - השתמש ב-$...$ לנוסחה בשורה, ו-$$...$$ לנוסחה בשורה נפרדת.

    על ה-JSON להכיל:
    1. "topics": רשימה של כותרות הנושאים המרכזיים בקצרה.
    2. "summaries": רשימה של אובייקטים. כל אובייקט מכיל:
       - "topic_name": שם הנושא.
       - "content": סיכום מעמיק ומפורט (לפחות 4-6 משפטים) על הנושא הספציפי מתוך ההרצאה, עם נוסחאות LaTeX.
    3. "flashcards": רשימה של 5 אובייקטים עם "question" ו-"answer", עם נוסחאות LaTeX לכל ביטוי מתמטי.

    התמלול:
    {text}
    """

    last_err = None
    for model_name in OPENAI_MODELS:
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert academic professor who writes detailed, structured summaries in Hebrew. "
                            "CRITICAL RULE: Every mathematical expression — functions, derivatives, integrals, limits, "
                            "vectors, matrices, equations, inequalities, Greek letters, exponents, subscripts — MUST be "
                            "written in LaTeX notation. Use $...$ for inline math and $$...$$ for display math. "
                            "NEVER write math in plain words or plain text. For example, write $f'(x_0)$ not 'הנגזרת של f בנקודה x0'. "
                            "IMPORTANT LaTeX restrictions: only use standard KaTeX-supported commands. "
                            "NEVER use \\begin{align} or any \\begin{...} environments — use separate $$...$$ blocks instead. "
                            "NEVER use custom macros like \\R, \\N, \\Z — write \\mathbb{R}, \\mathbb{N}, \\mathbb{Z} in full. "
                            "NEVER put Hebrew text directly inside math mode — use \\text{Hebrew} for Hebrew inside math. "
                            "NEVER use \\boldsymbol — use \\mathbf instead for bold math symbols."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            # Sanitize: restore LaTeX commands corrupted by JSON escape parsing
            # (e.g. \boldsymbol → U+0008 + oldsymbol due to JSON \b escape)
            return _fix_json_latex_escapes(content)
        except BadRequestError as e:
            err_body = getattr(e, "body", {}) or {}
            err_code = ((err_body.get("error") or {}).get("code")) if isinstance(err_body, dict) else None
            if err_code in {"model_not_found", "unsupported_model"}:
                last_err = e
                continue
            raise
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(
        "Failed to generate study material with configured OpenAI models: " + ", ".join(OPENAI_MODELS)
    ) from last_err
