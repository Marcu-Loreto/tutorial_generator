"""
Reusable Streamlit UI components for TutorialGen.

Centralises patterns that were duplicated across the Criar and Pesquisar
pages, so each page composes from these helpers instead of repeating code.
"""

from __future__ import annotations

import logging

import streamlit as st

from services.exporters import ExportError, export_docx, export_markdown, export_pdf
from utils.markdown_utils import estimate_reading_time

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tutorial content metrics row
# ---------------------------------------------------------------------------

def render_content_metrics(content_md: str) -> None:
    """
    Display a 3-column metrics row for a Markdown document.

    Shows estimated reading time, character count, and number of H2 sections.

    Args:
        content_md: The Markdown text to measure.
    """
    rt = estimate_reading_time(content_md)
    col1, col2, col3 = st.columns(3)
    col1.metric("⏱️ Leitura", f"~{rt} min")
    col2.metric("📄 Tamanho", f"{len(content_md):,} chars")
    col3.metric("📑 Seções H2", content_md.count("\n## "))


# ---------------------------------------------------------------------------
# Export download buttons
# ---------------------------------------------------------------------------

def render_export_buttons(title: str, content_md: str, key_prefix: str) -> None:
    """
    Render three side-by-side download buttons: Markdown, PDF, Word.

    Each export is wrapped in its own try/except so a failure in one
    format does not prevent the others from rendering.

    Args:
        title:      Tutorial title used for filename generation.
        content_md: Markdown content to export.
        key_prefix: Unique prefix for Streamlit widget keys
                    (avoids DuplicateWidgetID errors across pages).
    """
    col1, col2, col3 = st.columns(3)

    with col1:
        try:
            data, fname = export_markdown(title, content_md)
            st.download_button(
                label="⬇️ Markdown (.md)",
                data=data,
                file_name=fname,
                mime="text/markdown",
                use_container_width=True,
                key=f"dl_md_{key_prefix}",
            )
        except ExportError as exc:
            st.error(f"❌ Falha ao exportar Markdown: {exc}")
            logger.warning("export_markdown failed [%s]: %s", key_prefix, exc)

    with col2:
        try:
            data, fname = export_pdf(title, content_md)
            st.download_button(
                label="⬇️ PDF (.pdf)",
                data=data,
                file_name=fname,
                mime="application/pdf",
                use_container_width=True,
                key=f"dl_pdf_{key_prefix}",
            )
        except ExportError as exc:
            st.error(f"❌ Falha ao exportar PDF: {exc}")
            logger.warning("export_pdf failed [%s]: %s", key_prefix, exc)

    with col3:
        try:
            data, fname = export_docx(title, content_md)
            st.download_button(
                label="⬇️ Word (.docx)",
                data=data,
                file_name=fname,
                mime=(
                    "application/vnd.openxmlformats-officedocument"
                    ".wordprocessingml.document"
                ),
                use_container_width=True,
                key=f"dl_docx_{key_prefix}",
            )
        except ExportError as exc:
            st.error(f"❌ Falha ao exportar Word (.docx): {exc}")
            logger.warning("export_docx failed [%s]: %s", key_prefix, exc)


# ---------------------------------------------------------------------------
# Safe DB wrapper
# ---------------------------------------------------------------------------

def safe_load_tutorial(tutorial_id: int) -> dict | None:
    """
    Load a tutorial from the database with a user-friendly error fallback.

    Returns the tutorial dict on success, or None on failure (showing
    an st.error in the UI so the user knows what happened).

    Args:
        tutorial_id: Primary key of the tutorial to load.
    """
    from services.database import get_tutorial_by_id  # local import to avoid circulars

    try:
        tut = get_tutorial_by_id(tutorial_id)
        if tut is None:
            st.warning(
                f"Tutorial **#{tutorial_id}** não encontrado. "
                "Pode ter sido removido do banco de dados."
            )
        return tut
    except Exception as exc:
        st.error(
            f"❌ Erro ao carregar tutorial **#{tutorial_id}**:  \n`{exc}`  \n\n"
            "Verifique se o banco de dados está acessível."
        )
        logger.exception("safe_load_tutorial failed for id=%s", tutorial_id)
        return None
