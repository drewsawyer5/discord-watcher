"""Ingest quality review — detect and attribute likely-bad ingests.

A bad ingest can come from either side: the Python extraction (trafilatura
grabbed the wrong / cut-off content) or the LLM (good input, bad output). This
module provides the objective Python-side checks plus a combiner that merges
them with the LLM's own judgment into a single, attributed review decision.

See vault project "pa-bot Ingest Unification" → Output review & flagging.
"""

from __future__ import annotations

MIN_CHARS = 400

# Lowercased substrings that betray a block/paywall/anti-bot shell rather than
# the actual article body.
_PAYWALL_MARKERS = (
    "enable javascript",
    "are you a robot",
    "are you human",
    "verify you are human",
    "subscribe to continue",
    "subscribe to read",
    "access denied",
    "403 forbidden",
    "please enable cookies",
)


def check_extraction(
    content: str,
    *,
    used_fallback: bool = False,
    truncated: bool = False,
    min_chars: int = MIN_CHARS,
) -> list[str]:
    """Score trafilatura output for objective extraction problems.

    Args:
        content: The extracted article text handed to the LLM.
        used_fallback: True if trafilatura returned nothing and the crude regex
            strip fallback was used (low-confidence extraction).
        truncated: True if the content was truncated before the LLM call.
        min_chars: Below this length the content is treated as suspiciously short.

    Returns:
        A list of human-readable flags. Empty list means extraction looks fine.
    """
    flags: list[str] = []
    text = (content or "").strip()

    if len(text) < min_chars:
        flags.append(
            f"content very short ({len(text)} chars) — possible paywall/cookie shell"
        )
    if used_fallback:
        flags.append(
            "trafilatura returned empty; used crude regex fallback (low-confidence extraction)"
        )
    if truncated:
        flags.append("content was truncated before the LLM")

    lowered = text.lower()
    hits = [marker for marker in _PAYWALL_MARKERS if marker in lowered]
    if hits:
        flags.append(f"looks like a block/paywall page (matched: {', '.join(hits)})")

    return flags


def summarize_review(extraction_flags: list[str], llm_review: dict | None) -> dict:
    """Combine the three detectors into one attributed review decision.

    Detector sources:
        * ``extraction_flags`` — Python objective checks (extraction side).
        * ``llm_review["extraction_clean"]`` — LLM judged whether the provided
          content looks cleanly extracted (coherent, the actual article, not
          garbled/cut-off/nav junk) (extraction side).
        * ``llm_review["output_consistent"]`` — LLM cross-checked its own
          title/type/summary against the content (LLM-output side).

    Args:
        extraction_flags: Output of ``check_extraction``.
        llm_review: The LLM's ``review`` object, or None if absent. Missing keys
            default to "no problem".

    Returns:
        ``{"flagged": bool, "side": "extraction"|"llm"|"both"|"", "reasons": [...]}``.
    """
    review = llm_review or {}
    extraction_clean = review.get("extraction_clean", True)
    output_consistent = review.get("output_consistent", True)
    issues = (review.get("issues") or "").strip()
    suffix = f": {issues}" if issues else ""

    reasons: list[str] = list(extraction_flags)
    if extraction_clean is False:
        reasons.append(f"LLM judged the extracted content not cleanly extracted{suffix}")
    if output_consistent is False:
        reasons.append(f"LLM output may not match the content{suffix}")

    if not reasons:
        return {"flagged": False, "side": "", "reasons": []}

    extraction_side = bool(extraction_flags) or extraction_clean is False
    llm_side = output_consistent is False
    if extraction_side and llm_side:
        side = "both"
    elif extraction_side:
        side = "extraction"
    else:
        side = "llm"

    return {"flagged": True, "side": side, "reasons": reasons}
