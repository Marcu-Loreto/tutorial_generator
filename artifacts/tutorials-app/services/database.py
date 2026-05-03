"""
Database service for persisting tutorials using SQLite.

All interactions with tutorials.db go through this module.
Schema version: 3  (added tutorial_chats table)
"""

import sqlite3
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "database", "tutorials.db")


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    """
    Return a connection to the SQLite database.

    Creates the database directory if it does not exist.
    Enables WAL mode for better concurrent-read performance and sets
    row_factory so rows are returned as dict-like objects.
    """
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_db() -> None:
    """
    Create the tutorials table and its indexes if they do not exist.

    Safe to call multiple times — all statements use IF NOT EXISTS.
    """
    try:
        conn = get_connection()
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tutorials (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    title                   TEXT    NOT NULL,
                    technology              TEXT    NOT NULL DEFAULT '',
                    created_at              TEXT    NOT NULL,
                    updated_at              TEXT    NOT NULL,
                    requirements            TEXT    DEFAULT '',
                    prd                     TEXT    DEFAULT '',
                    spec                    TEXT    DEFAULT '',
                    draft_content           TEXT    DEFAULT '',
                    review_notes            TEXT    DEFAULT '',
                    final_content_md        TEXT    DEFAULT '',
                    source_documents_text   TEXT    DEFAULT '',
                    tags                    TEXT    DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tutorials_title ON tutorials (title)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tutorials_technology ON tutorials (technology)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tutorials_created_at ON tutorials (created_at)"
            )
            # tutorial_chats — per-tutorial conversation history
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tutorial_chats (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    tutorial_id  INTEGER NOT NULL
                                     REFERENCES tutorials(id) ON DELETE CASCADE,
                    role         TEXT    NOT NULL,
                    message      TEXT    NOT NULL,
                    created_at   TEXT    NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chats_tutorial_id ON tutorial_chats (tutorial_id)"
            )
        conn.close()
        logger.info("Database initialised successfully at %s", DB_PATH)
    except sqlite3.Error as exc:
        logger.error("Failed to initialise database: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    """Return current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    """Convert a sqlite3.Row to a plain dict, or return None."""
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def save_tutorial(
    title: str,
    technology: str = "",
    requirements: str = "",
    prd: str = "",
    spec: str = "",
    draft_content: str = "",
    review_notes: str = "",
    final_content_md: str = "",
    source_documents_text: str = "",
    tags: str = "",
) -> int:
    """
    Insert a new tutorial record into the database.

    Args:
        title: Tutorial title (required).
        technology: Main technology covered (e.g. "Python", "Docker").
        requirements: Raw requirements text or JSON.
        prd: Product Requirements Document content.
        spec: Technical specification content.
        draft_content: First draft written by the Writer agent.
        review_notes: Feedback produced by the Reviewer agent.
        final_content_md: Corrected final Markdown content.
        source_documents_text: Text extracted from uploaded source documents.
        tags: Comma-separated tags.

    Returns:
        The auto-generated integer ID of the new row.

    Raises:
        ValueError: If title is empty.
        sqlite3.Error: On any database error.
    """
    if not title or not title.strip():
        raise ValueError("Tutorial title must not be empty.")

    now = _utcnow()
    try:
        conn = get_connection()
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO tutorials (
                    title, technology, created_at, updated_at,
                    requirements, prd, spec,
                    draft_content, review_notes, final_content_md,
                    source_documents_text, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title.strip(), technology, now, now,
                    requirements, prd, spec,
                    draft_content, review_notes, final_content_md,
                    source_documents_text, tags,
                ),
            )
            new_id = cursor.lastrowid
        conn.close()
        logger.info("Tutorial saved with id=%s title=%r", new_id, title)
        return new_id
    except sqlite3.Error as exc:
        logger.error("save_tutorial failed: %s", exc)
        raise


def update_tutorial(tutorial_id: int, **fields) -> bool:
    """
    Update one or more fields of an existing tutorial.

    Only the fields passed as keyword arguments are updated.
    updated_at is always refreshed automatically.

    Allowed fields:
        title, technology, requirements, prd, spec,
        draft_content, review_notes, final_content_md,
        source_documents_text, tags

    Args:
        tutorial_id: ID of the tutorial to update.
        **fields: Keyword arguments mapping column names to new values.

    Returns:
        True if the row was found and updated, False if no row matched.

    Raises:
        ValueError: If an unknown field name is provided or fields is empty.
        sqlite3.Error: On any database error.
    """
    allowed = {
        "title", "technology", "requirements", "prd", "spec",
        "draft_content", "review_notes", "final_content_md",
        "source_documents_text", "tags",
    }

    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown field(s) for update: {unknown}")
    if not fields:
        raise ValueError("At least one field must be provided to update.")

    fields["updated_at"] = _utcnow()
    set_clause = ", ".join(f"{col} = ?" for col in fields)
    values = list(fields.values()) + [tutorial_id]

    try:
        conn = get_connection()
        with conn:
            cursor = conn.execute(
                f"UPDATE tutorials SET {set_clause} WHERE id = ?",
                values,
            )
            updated = cursor.rowcount > 0
        conn.close()
        if updated:
            logger.info("Tutorial id=%s updated: fields=%s", tutorial_id, list(fields.keys()))
        else:
            logger.warning("update_tutorial: no row found with id=%s", tutorial_id)
        return updated
    except sqlite3.Error as exc:
        logger.error("update_tutorial failed for id=%s: %s", tutorial_id, exc)
        raise


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

def get_tutorial_by_id(tutorial_id: int) -> dict | None:
    """
    Return a single tutorial by its ID.

    Args:
        tutorial_id: The integer primary key.

    Returns:
        A dict with all columns, or None if not found.
    """
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM tutorials WHERE id = ?", (tutorial_id,)
        ).fetchone()
        conn.close()
        return _row_to_dict(row)
    except sqlite3.Error as exc:
        logger.error("get_tutorial_by_id failed for id=%s: %s", tutorial_id, exc)
        raise


def search_tutorials(
    query: str = "",
    technology: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 100,
) -> list[dict]:
    """
    Search tutorials with optional filters.

    Filters are ANDed together. Any filter left as an empty string is ignored.

    Args:
        query: Full-text substring to match against title, tags,
               final_content_md, draft_content, and source_documents_text.
        technology: Exact-match filter on the technology column (case-insensitive).
        date_from: ISO 8601 date string — return rows with created_at >= this value.
        date_to: ISO 8601 date string — return rows with created_at <= this value.
        limit: Maximum number of results (default 100).

    Returns:
        List of matching tutorial dicts ordered by created_at descending.
    """
    conditions: list[str] = []
    params: list = []

    if query and query.strip():
        pattern = f"%{query.strip()}%"
        conditions.append(
            """(
                title              LIKE ? OR
                tags               LIKE ? OR
                final_content_md   LIKE ? OR
                draft_content      LIKE ? OR
                source_documents_text LIKE ?
            )"""
        )
        params.extend([pattern] * 5)

    if technology and technology.strip():
        conditions.append("LOWER(technology) = LOWER(?)")
        params.append(technology.strip())

    if date_from and date_from.strip():
        conditions.append("created_at >= ?")
        params.append(date_from.strip())

    if date_to and date_to.strip():
        conditions.append("created_at <= ?")
        params.append(date_to.strip())

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT * FROM tutorials {where_clause} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    try:
        conn = get_connection()
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        logger.error("search_tutorials failed (query=%r): %s", query, exc)
        raise


def count_tutorials() -> int:
    """Return the total number of tutorials stored in the database."""
    try:
        conn = get_connection()
        row = conn.execute("SELECT COUNT(*) FROM tutorials").fetchone()
        conn.close()
        return row[0] if row else 0
    except sqlite3.Error as exc:
        logger.error("count_tutorials failed: %s", exc)
        raise


def list_tutorials(limit: int = 200, offset: int = 0) -> list[dict]:
    """Return all tutorials ordered by creation date descending (lightweight cols)."""
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, title, technology, tags, created_at, updated_at "
            "FROM tutorials ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        logger.error("list_tutorials failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Tutorial Chats CRUD
# ---------------------------------------------------------------------------

def add_chat_message(tutorial_id: int, role: str, message: str) -> int:
    """
    Append a message to a tutorial's chat history.

    Args:
        tutorial_id: ID of the tutorial this message belongs to.
        role: "user" or "assistant".
        message: Raw text content of the message.

    Returns:
        The auto-generated integer ID of the new chat row.
    """
    if role not in ("user", "assistant"):
        raise ValueError(f"role must be 'user' or 'assistant', got: {role!r}")
    if not message or not message.strip():
        raise ValueError("message must not be empty.")

    now = _utcnow()
    try:
        conn = get_connection()
        with conn:
            cursor = conn.execute(
                "INSERT INTO tutorial_chats (tutorial_id, role, message, created_at) "
                "VALUES (?, ?, ?, ?)",
                (tutorial_id, role, message.strip(), now),
            )
            new_id = cursor.lastrowid
        conn.close()
        return new_id
    except sqlite3.Error as exc:
        logger.error("add_chat_message failed for tutorial_id=%s: %s", tutorial_id, exc)
        raise


def get_chat_history(tutorial_id: int, limit: int = 100) -> list[dict]:
    """
    Return the chat history for a tutorial ordered chronologically.

    Args:
        tutorial_id: ID of the tutorial.
        limit: Maximum number of messages to return.

    Returns:
        List of dicts with keys: id, tutorial_id, role, message, created_at.
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM tutorial_chats WHERE tutorial_id = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (tutorial_id, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        logger.error("get_chat_history failed for tutorial_id=%s: %s", tutorial_id, exc)
        raise


def clear_chat_history(tutorial_id: int) -> None:
    """Delete all chat messages for a specific tutorial."""
    try:
        conn = get_connection()
        with conn:
            conn.execute(
                "DELETE FROM tutorial_chats WHERE tutorial_id = ?",
                (tutorial_id,),
            )
        conn.close()
        logger.info("Chat history cleared for tutorial_id=%s", tutorial_id)
    except sqlite3.Error as exc:
        logger.error("clear_chat_history failed for tutorial_id=%s: %s", tutorial_id, exc)
        raise
