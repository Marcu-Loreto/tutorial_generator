"""
File loader service.

Responsibilities:
  - Load agent prompt templates from /prompts
  - Accept uploaded files from Streamlit (BytesIO), save them to /uploads,
    and extract their plain-text content (.txt, .md, .pdf)
"""

import io
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Directory resolution
# ---------------------------------------------------------------------------

_BASE_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = _BASE_DIR / "prompts"
UPLOADS_DIR = _BASE_DIR / "uploads"

AGENT_PROMPT_FILES: dict[str, str] = {
    "brainstorm": "brainstorm_agent.md",
    "prd": "prd_agent.md",
    "spec": "spec_agent.md",
    "writer": "writer_agent.md",
    "reviewer": "reviewer_agent.md",
    "fixer": "fixer_agent.md",
    "tutorial_tutor_agent": "tutorial_tutor_agent.md",
}

ACCEPTED_EXTENSIONS: set[str] = {".txt", ".md", ".pdf"}

# Maximum extracted text size (characters) to avoid bloated DB entries
MAX_EXTRACTED_CHARS = 200_000


# ---------------------------------------------------------------------------
# Prompt loader
# ---------------------------------------------------------------------------

def load_prompt(agent_name: str) -> str:
    """
    Load the Markdown prompt template for the given agent.

    Args:
        agent_name: One of 'brainstorm', 'prd', 'spec', 'writer', 'reviewer', 'fixer'.

    Returns:
        Prompt text as a string.

    Raises:
        ValueError: If agent_name is not recognised.
        FileNotFoundError: If the prompt file does not exist.
    """
    if agent_name not in AGENT_PROMPT_FILES:
        raise ValueError(
            f"Unknown agent '{agent_name}'. "
            f"Valid options: {list(AGENT_PROMPT_FILES.keys())}"
        )
    filepath = PROMPTS_DIR / AGENT_PROMPT_FILES[agent_name]
    if not filepath.exists():
        raise FileNotFoundError(f"Prompt file not found: {filepath}")
    return filepath.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_uploads_dir() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def _safe_filename(original_name: str) -> str:
    """
    Build a collision-safe filename: <timestamp>_<sanitised_original>.

    Sanitisation: keeps alphanumerics, dots, hyphens and underscores;
    replaces everything else with underscores.
    """
    stem = Path(original_name).stem
    suffix = Path(original_name).suffix.lower()

    # Normalise unicode then strip non-ASCII
    stem = unicodedata.normalize("NFKD", stem)
    stem = stem.encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"[^\w\-]", "_", stem).strip("_") or "file"

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return f"{timestamp}_{stem}{suffix}"


def _clean_text(raw: str) -> str:
    """Normalise whitespace and enforce the character cap."""
    text = re.sub(r"\r\n", "\n", raw)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MAX_EXTRACTED_CHARS:
        text = text[:MAX_EXTRACTED_CHARS] + "\n\n[... conteúdo truncado por exceder o limite máximo ...]"
    return text


# ---------------------------------------------------------------------------
# Format-specific extractors
# ---------------------------------------------------------------------------

def _extract_txt(data: bytes) -> str:
    """Decode plain-text / Markdown bytes as UTF-8 (with fallback to latin-1)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def _extract_pdf(data: bytes) -> str:
    """
    Extract text from a PDF using pypdf.

    Raises:
        ImportError: If pypdf is not installed.
        ValueError: If the PDF has no extractable text (e.g. scanned image PDF).
    """
    try:
        import pypdf  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "pypdf não está instalado. Execute: pip install pypdf"
        ) from exc

    reader = pypdf.PdfReader(io.BytesIO(data))

    if len(reader.pages) == 0:
        raise ValueError("O PDF não contém páginas.")

    parts: list[str] = []
    for page_num, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
            if page_text.strip():
                parts.append(page_text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao extrair texto da página %d: %s", page_num, exc)

    if not parts:
        raise ValueError(
            "Nenhum texto pôde ser extraído do PDF. "
            "O arquivo pode ser um PDF de imagem (escaneado) sem camada de texto."
        )

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class UploadResult:
    """Return value of process_uploaded_file."""

    __slots__ = ("filename", "saved_path", "extracted_text", "char_count", "page_count")

    def __init__(
        self,
        filename: str,
        saved_path: str,
        extracted_text: str,
        char_count: int,
        page_count: int = 0,
    ) -> None:
        self.filename = filename
        self.saved_path = saved_path
        self.extracted_text = extracted_text
        self.char_count = char_count
        self.page_count = page_count

    def summary(self) -> str:
        parts = [f"**{self.filename}** — {self.char_count:,} caracteres extraídos"]
        if self.page_count:
            parts.append(f"({self.page_count} página(s))")
        return " ".join(parts)


def process_uploaded_file(uploaded_file) -> UploadResult:
    """
    Validate, save and extract text from a Streamlit UploadedFile object.

    Accepted formats: .txt, .md, .pdf

    Args:
        uploaded_file: The object returned by st.file_uploader.

    Returns:
        An UploadResult with the saved path and extracted text.

    Raises:
        ValueError: For empty files, unsupported formats, or empty extraction.
        RuntimeError: For unexpected read/write failures.
    """
    if uploaded_file is None:
        raise ValueError("Nenhum arquivo fornecido.")

    original_name: str = uploaded_file.name
    suffix = Path(original_name).suffix.lower()

    if suffix not in ACCEPTED_EXTENSIONS:
        raise ValueError(
            f"Formato '{suffix}' não suportado. "
            f"Formatos aceitos: {', '.join(sorted(ACCEPTED_EXTENSIONS))}"
        )

    # Read raw bytes
    try:
        data: bytes = uploaded_file.read()
    except Exception as exc:
        raise RuntimeError(f"Falha ao ler o arquivo '{original_name}': {exc}") from exc

    if not data:
        raise ValueError(f"O arquivo '{original_name}' está vazio.")

    # Extract text
    page_count = 0
    try:
        if suffix == ".pdf":
            import pypdf  # noqa: PLC0415
            reader = pypdf.PdfReader(io.BytesIO(data))
            page_count = len(reader.pages)
            raw_text = _extract_pdf(data)
        else:
            raw_text = _extract_txt(data)
    except (ValueError, ImportError):
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Erro inesperado ao processar '{original_name}': {exc}"
        ) from exc

    extracted_text = _clean_text(raw_text)

    if not extracted_text.strip():
        raise ValueError(
            f"Nenhum texto utilizável foi extraído de '{original_name}'. "
            "Verifique se o arquivo possui conteúdo de texto."
        )

    # Persist to /uploads
    _ensure_uploads_dir()
    safe_name = _safe_filename(original_name)
    save_path = UPLOADS_DIR / safe_name
    try:
        save_path.write_bytes(data)
        logger.info(
            "Arquivo salvo em %s (%d bytes, %d chars extraídos)",
            save_path, len(data), len(extracted_text),
        )
    except OSError as exc:
        raise RuntimeError(f"Falha ao salvar o arquivo em disco: {exc}") from exc

    return UploadResult(
        filename=original_name,
        saved_path=str(save_path),
        extracted_text=extracted_text,
        char_count=len(extracted_text),
        page_count=page_count,
    )


def load_saved_upload(filename: str) -> str:
    """
    Read a previously saved upload from the /uploads directory by its stored filename.

    Args:
        filename: The filename as stored (not the original uploaded name).

    Returns:
        File content as a plain string.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file extension is not supported.
    """
    filepath = UPLOADS_DIR / filename
    if not filepath.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {filepath}")

    suffix = filepath.suffix.lower()
    if suffix not in ACCEPTED_EXTENSIONS:
        raise ValueError(f"Extensão '{suffix}' não suportada para leitura.")

    data = filepath.read_bytes()

    if suffix == ".pdf":
        return _clean_text(_extract_pdf(data))
    return _clean_text(_extract_txt(data))
