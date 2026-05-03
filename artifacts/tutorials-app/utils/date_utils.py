"""
Date and time utility helpers used across the application.
"""

from datetime import datetime, timezone


def utc_now_iso() -> str:
    """Return the current UTC datetime as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def format_display_date(iso_string: str) -> str:
    """
    Convert an ISO 8601 datetime string to a human-readable display format.

    Example:
        "2024-06-15T10:30:00+00:00"  →  "15/06/2024 10:30"

    Args:
        iso_string: A datetime string in ISO 8601 format.

    Returns:
        A formatted date string, or the original string if parsing fails.
    """
    try:
        dt = datetime.fromisoformat(iso_string)
        return dt.strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError):
        return iso_string


def days_since(iso_string: str) -> int:
    """
    Calculate how many full days have passed since the given ISO 8601 datetime.

    Args:
        iso_string: A datetime string in ISO 8601 format.

    Returns:
        Number of days, or -1 if parsing fails.
    """
    try:
        dt = datetime.fromisoformat(iso_string)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return delta.days
    except (ValueError, TypeError):
        return -1


def relative_time(iso_string: str) -> str:
    """
    Return a human-friendly relative time string.

    Examples:
        "agora mesmo", "há 5 minutos", "há 2 horas", "há 3 dias"
    """
    try:
        dt = datetime.fromisoformat(iso_string)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        total_seconds = int(delta.total_seconds())

        if total_seconds < 60:
            return "agora mesmo"
        elif total_seconds < 3600:
            minutes = total_seconds // 60
            return f"há {minutes} minuto{'s' if minutes > 1 else ''}"
        elif total_seconds < 86400:
            hours = total_seconds // 3600
            return f"há {hours} hora{'s' if hours > 1 else ''}"
        else:
            days = total_seconds // 86400
            return f"há {days} dia{'s' if days > 1 else ''}"
    except (ValueError, TypeError):
        return iso_string
