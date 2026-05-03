"""
Conversational memory for the Brainstorm agent.

All state lives in st.session_state so it persists across Streamlit reruns
within the same browser session but is automatically discarded when the
session ends — no database writes required.

Public API
----------
add_interaction(user_msg, agent_msg)   — append one turn to history
list_history()                          — return all stored interactions
get_last_n(n)                           — return the most recent n turns
clear_memory()                          — wipe the history
get_context_block(n)                    — formatted string for LLM prompts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Maximum number of interactions kept in memory at any time
MAX_INTERACTIONS: int = 10

# session_state key used by all functions in this module
_HISTORY_KEY = "brainstorm_history"
_PIPELINE_KEY = "tutorial_memory"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BrainstormInteraction:
    """A single turn in the brainstorm conversation."""

    user_message: str
    agent_response: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "user_message": self.user_message,
            "agent_response": self.agent_response,
            "timestamp": self.timestamp,
        }

    @staticmethod
    def from_dict(d: dict) -> "BrainstormInteraction":
        return BrainstormInteraction(
            user_message=d["user_message"],
            agent_response=d["agent_response"],
            timestamp=d.get("timestamp", ""),
        )

    def formatted_timestamp(self) -> str:
        """Return a short human-readable timestamp (HH:MM)."""
        try:
            dt = datetime.fromisoformat(self.timestamp)
            return dt.strftime("%H:%M")
        except (ValueError, TypeError):
            return ""


@dataclass
class TutorialMemory:
    """Holds the complete in-memory pipeline state for one generation run."""

    topic: str = ""
    brainstorm: dict = field(default_factory=dict)
    prd: dict = field(default_factory=dict)
    spec: dict = field(default_factory=dict)
    draft: str = ""
    review: dict = field(default_factory=dict)
    final: str = ""
    errors: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.topic = ""
        self.brainstorm = {}
        self.prd = {}
        self.spec = {}
        self.draft = ""
        self.review = {}
        self.final = ""
        self.errors = []

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "brainstorm": self.brainstorm,
            "prd": self.prd,
            "spec": self.spec,
            "draft": self.draft,
            "review": self.review,
            "final": self.final,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Internal helper — always returns the list stored in session_state
# ---------------------------------------------------------------------------

def _get_history_list() -> list[BrainstormInteraction]:
    """
    Return the live list of BrainstormInteraction objects from session_state.
    Initialises the list on first access.
    """
    import streamlit as st  # imported here to avoid circular issues at module load

    if _HISTORY_KEY not in st.session_state:
        st.session_state[_HISTORY_KEY] = []
    return st.session_state[_HISTORY_KEY]


# ---------------------------------------------------------------------------
# Public API — conversational memory
# ---------------------------------------------------------------------------

def add_interaction(user_message: str, agent_response: str) -> BrainstormInteraction:
    """
    Append a new user/agent turn to the brainstorm history.

    If the history already contains MAX_INTERACTIONS entries, the oldest
    entry is removed before appending (sliding window).

    Args:
        user_message:   The raw text typed by the user.
        agent_response: The response produced by the Brainstorm agent.

    Returns:
        The newly created BrainstormInteraction.

    Raises:
        ValueError: If either message is empty.
    """
    if not user_message or not user_message.strip():
        raise ValueError("user_message must not be empty.")
    if not agent_response or not agent_response.strip():
        raise ValueError("agent_response must not be empty.")

    interaction = BrainstormInteraction(
        user_message=user_message.strip(),
        agent_response=agent_response.strip(),
    )

    history = _get_history_list()
    history.append(interaction)

    # Enforce the sliding window
    if len(history) > MAX_INTERACTIONS:
        del history[: len(history) - MAX_INTERACTIONS]

    return interaction


def list_history() -> list[BrainstormInteraction]:
    """
    Return all stored interactions in chronological order (oldest first).

    Returns:
        A shallow copy of the internal history list so callers cannot
        accidentally mutate session_state.
    """
    return list(_get_history_list())


def get_last_n(n: int = MAX_INTERACTIONS) -> list[BrainstormInteraction]:
    """
    Return the most recent n interactions in chronological order.

    Args:
        n: Number of interactions to return. Clamped to [1, MAX_INTERACTIONS].

    Returns:
        Up to n BrainstormInteraction objects (oldest → newest).
    """
    n = max(1, min(n, MAX_INTERACTIONS))
    history = _get_history_list()
    return list(history[-n:])


def clear_memory() -> None:
    """
    Wipe all stored brainstorm interactions from session_state.

    Also resets the pipeline TutorialMemory if it exists.
    """
    import streamlit as st

    st.session_state[_HISTORY_KEY] = []
    if _PIPELINE_KEY in st.session_state:
        st.session_state[_PIPELINE_KEY].reset()


def interaction_count() -> int:
    """Return the number of interactions currently stored."""
    return len(_get_history_list())


def get_context_block(n: int = MAX_INTERACTIONS) -> str:
    """
    Return the last n interactions formatted as a plain-text block suitable
    for injecting into an LLM prompt.

    Format:
        User: <message>
        Agente: <response>
        ---

    Args:
        n: Number of recent turns to include.

    Returns:
        Multi-line string, or an empty string if there is no history.
    """
    turns = get_last_n(n)
    if not turns:
        return ""
    lines: list[str] = []
    for turn in turns:
        lines.append(f"User: {turn.user_message}")
        lines.append(f"Agente: {turn.agent_response}")
        lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pipeline memory accessor
# ---------------------------------------------------------------------------

def get_session_memory() -> TutorialMemory:
    """
    Return the TutorialMemory instance from session_state, creating it on
    first access.
    """
    import streamlit as st

    if _PIPELINE_KEY not in st.session_state:
        st.session_state[_PIPELINE_KEY] = TutorialMemory()
    return st.session_state[_PIPELINE_KEY]
