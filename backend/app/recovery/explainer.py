"""
Recovery explainer.

Generates a merchant-readable explanation for a recovery decision.

Uses Groq (qwen/qwen3.6-27b) if GROQ_API_KEY is set.
Falls back to a deterministic template if:
  - GROQ_API_KEY is not set
  - API call fails for any reason
  - Response is empty or malformed

Rule 9: LLM failure must not break core recovery processing.
The LLM receives only structured facts. It cannot change the selected action.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

_GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
_GROQ_MODEL = "qwen/qwen3.6-27b"

# ---------------------------------------------------------------------------
# Deterministic templates — always available
# ---------------------------------------------------------------------------
_TEMPLATES = {
    "RETRY_LATER": (
        "A retry has been scheduled because the failure appears to be temporary. "
        "The selected timing has the highest predicted recovery probability for this failure type."
    ),
    "ALTERNATIVE_PAYMENT": (
        "An alternative payment method has been suggested because the original payment route "
        "is unlikely to succeed. This option has the highest predicted recovery probability."
    ),
    "CUSTOMER_MESSAGE": (
        "A recovery message has been queued for the customer. "
        "Direct outreach is the most appropriate intervention for this failure type."
    ),
    "STOP": (
        "Recovery has been halted. Further intervention is unlikely to succeed "
        "and would risk unnecessary friction for the customer."
    ),
    "ESCALATE": (
        "This case has been escalated for manual review. "
        "Automated recovery options have been exhausted."
    ),
}


def _template(action_type: str) -> str:
    return _TEMPLATES.get(action_type, "A recovery action has been selected based on the failure context.")


# ---------------------------------------------------------------------------
# Outcome templates — always available
# ---------------------------------------------------------------------------
_OUTCOME_TEMPLATE = (
    "The initial payment failed but was successfully recovered on a subsequent attempt."
)


# ---------------------------------------------------------------------------
# LLM explanation via Groq
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "You are a payment recovery explanation generator.\n\n"
    "Your ONLY task is to write one short merchant-facing summary "
    "explaining why the already-selected recovery action was chosen.\n\n"
    "Do NOT make or change the decision.\n"
    "Do NOT provide analysis.\n"
    "Do NOT provide reasoning steps.\n"
    "Do NOT use bullet points.\n"
    "Do NOT mention probabilities unless necessary.\n"
    "Do NOT output <think>, <analysis>, or any other tags.\n\n"
    "Return ONLY the final explanation.\n\n"
    "Maximum 25 words."
)


def _build_prompt(
    root_cause: str,
    action_type: str,
    scores: dict[str, float],
    permitted: list[str],
) -> str:
    score_lines = "\n".join(f"  {k}: {v:.3f}" for k, v in sorted(scores.items(), key=lambda x: -x[1]))
    return (
        f"Root cause: {root_cause}\n"
        f"Selected action: {action_type}\n"
        f"Candidate scores:\n{score_lines}\n"
        f"Permitted actions: {', '.join(permitted)}\n\n"
        "Generate one concise merchant-facing explanation."
    )


def explain(
    root_cause: str,
    action_type: str,
    scores: dict[str, float],
    permitted: list[str],
) -> str:
    """
    Returns a merchant-readable explanation string.
    Always returns something — never raises.
    """
    if not _GROQ_API_KEY:
        return _template(action_type)

    try:
        from groq import Groq
        client = Groq(api_key=_GROQ_API_KEY)

        response = client.chat.completions.create(
            model=_GROQ_MODEL,
            reasoning_effort="none",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _build_prompt(
                        root_cause,
                        action_type,
                        scores,
                        permitted,
                    ),
                },
            ],
            max_tokens=100,
            temperature=0.3,
        )

        text = response.choices[0].message.content or ""

        #remove <think>...</think> blocks
        text = re.sub(
            r"<think>.*?</think>",
            "",
            text,
            flags=re.DOTALL,
        )

        #unclosed <think> block
        text = re.sub(
            r"<think>.*",
            "",
            text,
            flags=re.DOTALL,
        )

        text = text.strip()

        if not text:
            return _template(action_type)

        if "<think>" in text.lower() or "<analysis>" in text.lower():
            return _template(action_type)

        if len(text) > 300:
            return _template(action_type)

        return text

    except Exception as e:
        logger.warning("Groq explainer failed: %s", e)

    return _template(action_type)


_OUTCOME_SYSTEM_PROMPT = (
    "You are a payment recovery outcome narrator.\n\n"
    "Your ONLY task is to write one plain English sentence describing what happened "
    "to a merchant — what failed, how it was recovered, and what method succeeded.\n\n"
    "Use only the facts provided. Do NOT speculate.\n"
    "Do NOT provide analysis or recommendations.\n"
    "Do NOT output <think>, <analysis>, or any other tags.\n\n"
    "Return ONLY the final sentence.\n\n"
    "Maximum 30 words."
)


def explain_outcome(
    failed_method: str,
    failed_reason: str,
    success_method: str,
    attempt_count: int,
) -> str:
    """
    Returns a merchant-readable outcome narrative.
    Always returns something — never raises.
    """
    fallback = (
        f"The initial {failed_method} attempt failed ({failed_reason}), "
        f"but the payment was successfully completed using {success_method} "
        f"on attempt {attempt_count}."
    )

    if not _GROQ_API_KEY:
        return fallback

    try:
        from groq import Groq
        client = Groq(api_key=_GROQ_API_KEY)

        user_msg = (
            f"Failed method: {failed_method}\n"
            f"Failure reason: {failed_reason}\n"
            f"Successful method: {success_method}\n"
            f"Total attempts: {attempt_count}\n\n"
            "Describe what happened in one sentence."
        )

        response = client.chat.completions.create(
            model=_GROQ_MODEL,
            reasoning_effort="none",
            messages=[
                {"role": "system", "content": _OUTCOME_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=80,
            temperature=0.3,
        )

        text = response.choices[0].message.content or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        text = re.sub(r"<think>.*", "", text, flags=re.DOTALL)
        text = text.strip()

        if not text or "<think>" in text.lower() or len(text) > 400:
            return fallback

        return text

    except Exception as e:
        logger.warning("Groq outcome explainer failed: %s", e)

    return fallback