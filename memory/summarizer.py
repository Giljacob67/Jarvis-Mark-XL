"""
MARK XL — Conversation Summarizer.

Auto-summarizes long conversations to maintain context within token limits.
Extracts key facts, decisions, and preferences from conversation history.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from core.logger import get_logger
from core.paths import BASE_DIR

log = get_logger("summarizer")

SUMMARIES_PATH = BASE_DIR / "memory" / "conversation_summaries.json"


def _load_summaries() -> list[dict]:
    try:
        return json.loads(SUMMARIES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_summaries(summaries: list[dict]) -> None:
    SUMMARIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARIES_PATH.write_text(json.dumps(summaries, indent=2, ensure_ascii=False), encoding="utf-8")


def summarize_conversation(messages: list[dict], max_length: int = 200) -> str:
    """
    Create a summary of a conversation using the LLM.

    Args:
        messages: List of message dicts with 'role' and 'content'
        max_length: Maximum summary length in words

    Returns:
        Summary string
    """
    if not messages:
        return "Empty conversation."

    # Build conversation text
    conv_text = ""
    for msg in messages[-20:]:  # Last 20 messages
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str):
            conv_text += f"{role}: {content[:500]}\n"
        elif isinstance(content, list):
            # Handle tool call messages
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    conv_text += f"{role}: {item.get('text', '')[:500]}\n"

    if not conv_text.strip():
        return "No content to summarize."

    # Try LLM summarization
    try:
        import requests
        cfg_path = BASE_DIR / "config" / "api_keys.json"
        config = json.loads(cfg_path.read_text(encoding="utf-8"))
        url = config.get("llm_url", "http://localhost:11434") + "/api/generate"

        prompt = (
            f"Summarize this conversation in {max_length} words or fewer. "
            f"Focus on key facts, decisions, preferences, and action items.\n\n"
            f"{conv_text}"
        )

        resp = requests.post(url, json={
            "model": config.get("llm_model", "qwen2.5:7b"),
            "prompt": prompt,
            "stream": False,
        }, timeout=60)
        resp.raise_for_status()
        return resp.json().get("response", "Summary generation failed.")
    except Exception as e:
        log.warning("LLM summarization failed: %s", e)

    # Fallback: extract key lines
    lines = []
    for msg in messages[-10:]:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, str) and content.strip():
            prefix = "User" if role == "user" else "JARVIS" if role == "assistant" else role
            lines.append(f"{prefix}: {content[:100]}")
    return "\n".join(lines[-5:]) if lines else "No summary available."


def save_conversation_summary(conv_id: int, summary: str, messages: list[dict]) -> None:
    """Save a conversation summary for future reference."""
    summaries = _load_summaries()
    summaries.append({
        "conv_id": conv_id,
        "summary": summary,
        "message_count": len(messages),
        "created_at": datetime.now().isoformat(),
    })
    # Keep last 100 summaries
    if len(summaries) > 100:
        summaries = summaries[-100:]
    _save_summaries(summaries)


def get_relevant_summaries(query: str, limit: int = 3) -> list[dict]:
    """Find conversation summaries relevant to a query (simple keyword match)."""
    summaries = _load_summaries()
    query_words = set(query.lower().split())

    scored = []
    for s in summaries:
        summary_text = s.get("summary", "").lower()
        score = sum(1 for w in query_words if w in summary_text)
        if score > 0:
            scored.append((score, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:limit]]


def format_summaries_for_prompt(query: str, limit: int = 2) -> str:
    """Format relevant summaries for inclusion in the system prompt."""
    relevant = get_relevant_summaries(query, limit)
    if not relevant:
        return ""
    parts = ["[PAST CONVERSATION SUMMARIES]"]
    for s in relevant:
        parts.append(f"- {s.get('summary', '')[:300]}")
    return "\n".join(parts)
