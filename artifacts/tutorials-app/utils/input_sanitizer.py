"""
Input sanitization utilities for TutorialGen.

All user-supplied text passes through these functions before being stored in
the database, sent to agents, or used to generate filenames.  The goal is to
guarantee well-formed, bounded UTF-8 strings throughout the pipeline.
"""

from __future__ import annotations

import re
import unicodedata


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _strip_control_chars(text: str) -> str:
    """Remove C0/C1 control characters while preserving \\n \\r \\t."""
    return "".join(
        c for c in text
        if unicodedata.category(c) not in ("Cc", "Cf") or c in "\n\r\t "
    )


def sanitize_text(
    text: str,
    max_length: int = 10_000,
    strip_control: bool = True,
) -> str:
    """
    General-purpose text sanitizer.

    Steps:
      1. Coerce to str.
      2. Ensure valid UTF-8 by round-tripping through bytes.
      3. Optionally strip control characters (preserves \\n, \\r, \\t).
      4. Strip leading/trailing whitespace.
      5. Truncate to max_length characters.

    Returns an empty string if the input is None or entirely whitespace.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = validate_encoding(text)
    if strip_control:
        text = _strip_control_chars(text)
    text = text.strip()
    if max_length and len(text) > max_length:
        text = text[:max_length]
    return text


def validate_encoding(text: str) -> str:
    """
    Ensure *text* is valid UTF-8 by round-tripping through bytes.

    Malformed code points are replaced with U+FFFD.  Returns an empty
    string if the input is not a str.
    """
    if not isinstance(text, str):
        return ""
    try:
        return text.encode("utf-8", errors="replace").decode("utf-8")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Domain-specific sanitizers
# ---------------------------------------------------------------------------

def sanitize_title(title: str, max_length: int = 200) -> str:
    """
    Sanitize a tutorial title.

    Collapses internal whitespace runs to a single space and enforces a
    maximum length of *max_length* characters (default 200).
    """
    cleaned = sanitize_text(title, max_length=max_length, strip_control=True)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def sanitize_technology(tech: str, max_length: int = 100) -> str:
    """
    Sanitize a technology / tool name.

    Enforces max_length (default 100).  Does not force casing — the user
    may prefer "FastAPI" over "fastapi".
    """
    return sanitize_text(tech, max_length=max_length, strip_control=True)


def sanitize_tags(tags: str, max_length: int = 500) -> str:
    """
    Normalize a comma-separated tag string.

    Steps:
      1. Sanitize raw text.
      2. Split on commas.
      3. Strip each tag, lowercase, remove non-alphanumeric (except space/-).
      4. Remove empty tokens and duplicates (order-preserving).
      5. Rejoin as ``"tag1, tag2, tag3"``.
    """
    cleaned = sanitize_text(tags, max_length=max_length, strip_control=True)
    if not cleaned:
        return ""
    tag_list = [
        re.sub(r"[^\w\s\-]", "", t.strip().lower()).strip()
        for t in cleaned.split(",")
        if t.strip()
    ]
    seen: set[str] = set()
    unique: list[str] = []
    for t in tag_list:
        if t and t not in seen:
            seen.add(t)
            unique.append(t)
    return ", ".join(unique)


def sanitize_question(question: str, max_length: int = 2_000) -> str:
    """
    Sanitize a chat question typed by the user.

    Preserves newlines (to support multi-line questions) but caps at
    *max_length* characters (default 2 000).
    """
    return sanitize_text(question, max_length=max_length, strip_control=False)


def sanitize_markdown_content(content: str, max_length: int = 500_000) -> str:
    """
    Light sanitization for Markdown content (tutorial body).

    Preserves all formatting characters.  Only enforces encoding and max_length.
    """
    if not content:
        return ""
    content = validate_encoding(content)
    if len(content) > max_length:
        content = content[:max_length]
    return content


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def is_safe_content(content: str, min_length: int = 50) -> bool:
    """
    Return True if *content* is non-empty and meets a minimum character count.

    Use this as a guard before passing content to exporters or agents.
    """
    return bool(content and len(content.strip()) >= min_length)


def ensure_non_empty(value: str, field_name: str = "field") -> str:
    """
    Return *value* if non-empty, otherwise raise ValueError.

    Convenience wrapper for early validation in service functions.
    """
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty.")
    return value.strip()
