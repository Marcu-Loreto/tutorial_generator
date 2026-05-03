"""
LLM Router — selects the model based on task complexity and AI mode.

Functions
---------
get_model_for_complexity(complexity, mode)  → model name string
classify_task_complexity(task_type, user_input, context)  → "simple" | "medium" | "complex"
call_llm(model, system_prompt, user_prompt, temperature, max_tokens)  → str
run_llm_task(task_type, system_prompt, user_prompt, context, mode)  → str
get_last_model_used()  → str | None
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default model names (overridable via environment variables)
# ---------------------------------------------------------------------------

DEFAULT_SIMPLE_MODEL: str = "MiniMax-2.5"
DEFAULT_MEDIUM_MODEL: str = "gpt-5.4-nano"
DEFAULT_COMPLEX_MODEL: str = "gpt-5.4-mini"

# Task type → default complexity
TASK_COMPLEXITY_MAP: dict[str, str] = {
    # simple tasks
    "brainstorm": "simple",
    "intent_classification": "simple",
    "requirements_extraction": "simple",
    "doc_summary": "simple",
    "questions": "simple",
    "tags": "simple",
    "metadata": "simple",
    "tutor_simple": "simple",
    # medium tasks
    "prd": "medium",
    "spec": "medium",
    "tutorial_structure": "medium",
    "revision_basic": "medium",
    "language_adaptation": "medium",
    "reviewer": "medium",
    # complex tasks
    "writer": "complex",
    "fixer": "complex",
    "technical_explanation": "complex",
    "advanced_examples": "complex",
    "troubleshooting": "complex",
    "tutor_complex": "complex",
    "exercises": "complex",
}

# Module-level tracker for the last model used
_last_model_used: Optional[str] = None


def get_last_model_used() -> Optional[str]:
    """Return the name of the last model that was successfully called."""
    return _last_model_used


def get_configured_models() -> dict[str, str]:
    """Return a dict with the three configured model names."""
    return {
        "simple": os.getenv("LLM_SIMPLE_MODEL", DEFAULT_SIMPLE_MODEL),
        "medium": os.getenv("LLM_MEDIUM_MODEL", DEFAULT_MEDIUM_MODEL),
        "complex": os.getenv("LLM_COMPLEX_MODEL", DEFAULT_COMPLEX_MODEL),
    }


def get_model_for_complexity(complexity: str, mode: str = "balanced") -> str:
    """
    Return the model name for the given complexity level and AI mode.

    mode="balanced"  → complexity maps directly to model tier
    mode="economic"  → downgrades: complex→medium, medium→simple, simple→simple
    mode="quality"   → upgrades everything to complex model
    """
    models = get_configured_models()

    if mode == "economic":
        if complexity == "complex":
            return models["medium"]
        return models["simple"]

    if mode == "quality":
        return models["complex"]

    # balanced (default)
    return models.get(complexity, models["medium"])


def classify_task_complexity(
    task_type: str,
    user_input: str = "",
    context: str = "",
) -> str:
    """
    Classify a task as "simple", "medium", or "complex".

    Rules (in priority order):
    1. Combined input + context length > 3 000 chars  → complex
    2. Presence of complex keywords in user_input     → upgrade one tier
    3. Known task_type in TASK_COMPLEXITY_MAP         → use mapped value
    4. Default                                        → medium
    """
    combined_len = len(user_input) + len(context)
    if combined_len > 3000:
        return "complex"

    base = TASK_COMPLEXITY_MAP.get(task_type, "medium")

    ui_lower = user_input.lower()
    complex_keywords = [
        "arquitetura", "architecture", "troubleshoot", "debug",
        "avançado", "advanced", "comparação", "comparison",
        "performance", "segurança", "security", "otimização",
        "optimization", "exercício", "exercise", "internals",
    ]
    medium_keywords = [
        "estrutura", "structure", "revisão", "revision",
        "explicação", "explanation", "adaptar", "adapt",
    ]

    if base == "simple" and any(kw in ui_lower for kw in complex_keywords):
        return "medium"
    if base == "medium" and any(kw in ui_lower for kw in complex_keywords):
        return "complex"
    if base == "simple" and any(kw in ui_lower for kw in medium_keywords):
        return "medium"

    return base


def call_llm(
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.4,
    max_tokens: Optional[int] = None,
) -> str:
    """
    Call the OpenAI-compatible Chat Completions API.

    Raises RuntimeError with a user-friendly message on:
    - Missing API key
    - Model not found
    - Any other API error
    """
    global _last_model_used

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "Configure sua chave de API para gerar tutoriais de alta qualidade. "
            "Defina a variável de ambiente OPENAI_API_KEY."
        )

    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Pacote 'openai' não instalado. Execute: pip install openai"
        ) from exc

    client = OpenAI(api_key=api_key)

    kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
    }
    if max_tokens:
        kwargs["max_tokens"] = max_tokens

    try:
        response = client.chat.completions.create(**kwargs)
        _last_model_used = model
        total_tokens = response.usage.total_tokens if response.usage else "?"
        logger.info(
            "call_llm: model=%s tokens=%s complexity routed correctly",
            model, total_tokens,
        )
        return response.choices[0].message.content or ""

    except Exception as exc:
        error_str = str(exc).lower()
        # Detect model-not-found errors and give actionable guidance
        if any(phrase in error_str for phrase in ("model", "does not exist", "not found", "invalid model")):
            models = get_configured_models()
            env_var_map = {v: k.upper() for k, v in models.items()}
            env_var = env_var_map.get(model)
            if env_var:
                env_var_name = f"LLM_{env_var}_MODEL"
            else:
                env_var_name = "LLM_SIMPLE_MODEL / LLM_MEDIUM_MODEL / LLM_COMPLEX_MODEL"
            raise RuntimeError(
                f"Modelo '{model}' não encontrado na API. "
                f"Ajuste a variável de ambiente {env_var_name} para um modelo disponível na sua conta. "
                f"Erro original: {exc}"
            ) from exc
        raise RuntimeError(f"Erro ao chamar o modelo '{model}': {exc}") from exc


def run_llm_task(
    task_type: str,
    system_prompt: str,
    user_prompt: str,
    context: str = "",
    mode: str = "balanced",
) -> str:
    """
    Full pipeline: classify → select model → call LLM → return text.

    Parameters
    ----------
    task_type   : one of the keys in TASK_COMPLEXITY_MAP (e.g. "writer")
    system_prompt : the agent's system prompt
    user_prompt   : the user / state message
    context       : optional extra context for complexity classification
    mode          : "balanced" | "economic" | "quality"
    """
    complexity = classify_task_complexity(task_type, user_prompt, context)
    model = get_model_for_complexity(complexity, mode)
    logger.info(
        "run_llm_task: task=%s complexity=%s model=%s mode=%s",
        task_type, complexity, model, mode,
    )
    return call_llm(model, system_prompt, user_prompt)
