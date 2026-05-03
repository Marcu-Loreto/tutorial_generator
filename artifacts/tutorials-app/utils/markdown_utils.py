"""
Markdown utility helpers used across the application.
"""

import re


def extract_title(markdown_text: str) -> str:
    """
    Extract the first H1 heading from a Markdown document.

    Returns an empty string if no H1 heading is found.
    """
    for line in markdown_text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def extract_section(markdown_text: str, section_title: str) -> str:
    """
    Extract the content of a specific section by its heading title.

    Args:
        markdown_text: The full Markdown document.
        section_title: The heading text to search for (without the # prefix).

    Returns:
        The section content (up to the next heading of equal or higher level),
        or an empty string if the section is not found.
    """
    lines = markdown_text.splitlines()
    inside = False
    section_level = 0
    result = []

    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)")

    for line in lines:
        match = heading_pattern.match(line)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            if title.lower() == section_title.lower():
                inside = True
                section_level = level
                continue
            elif inside and level <= section_level:
                break
        if inside:
            result.append(line)

    return "\n".join(result).strip()


def word_count(markdown_text: str) -> int:
    """Return the approximate word count of a Markdown document."""
    clean = re.sub(r"[#*`_>\[\]()!]", " ", markdown_text)
    words = clean.split()
    return len(words)


def estimate_reading_time(markdown_text: str, wpm: int = 200) -> int:
    """
    Estimate the reading time of a tutorial in minutes.

    Args:
        markdown_text: The Markdown content.
        wpm: Words per minute reading speed (default 200).

    Returns:
        Estimated reading time in whole minutes (minimum 1).
    """
    count = word_count(markdown_text)
    minutes = max(1, round(count / wpm))
    return minutes


def truncate_preview(markdown_text: str, max_chars: int = 300) -> str:
    """
    Return a plain-text preview of the Markdown content, truncated to max_chars.
    """
    clean = re.sub(r"[#*`_>\[\]()!\-]", "", markdown_text)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars].rsplit(" ", 1)[0] + "…"
