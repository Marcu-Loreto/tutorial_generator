"""
Agent implementations for the tutorial generation pipeline.

Each agent is a pure function:
    Input:  shared state dict
    Output: updated state dict (never mutates the original)

Execution strategy
------------------
All agents call the LLM Router (services/llm_router.py) which selects the
appropriate model based on task complexity ("simple" / "medium" / "complex")
and the active AI mode ("balanced" / "economic" / "quality").

Writer and Fixer agents require a real LLM — they return an error state if
no API key is configured. Brainstorm, PRD, Spec and Reviewer agents fall back
to deterministic templates when the LLM is unavailable, so the pipeline can
still produce structured data for inspection.

State schema (keys used across agents)
---------------------------------------
topic                  : str   — raw user input from the chat
technology             : str
target_audience        : str
technical_level        : str   — "iniciante" | "intermediário" | "avançado"
objective              : str
operating_environment  : str
prerequisites          : list[str]
depth                  : str
practical_examples     : dict  — {include, count, description}
common_errors          : list[str]
expected_outcome       : str
source_documents_text  : str   — merged text from uploaded docs
chat_history           : list[dict] — {user_message, agent_response}
brainstorm             : dict  — output of brainstorm_agent
prd                    : dict  — output of prd_agent
spec                   : dict  — output of spec_agent
draft_content          : str   — output of writer_agent
review                 : dict  — output of reviewer_agent
final_content_md       : str   — output of fixer_agent
errors                 : list[str]
status                 : str   — pipeline status label
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from textwrap import dedent
from typing import Any

from services.llm_router import run_llm_task

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenAI integration (optional)
# ---------------------------------------------------------------------------

def _openai_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _call_openai(system_prompt: str, user_message: str, model: str = "gpt-4o-mini") -> str:
    """
    Call OpenAI Chat Completions and return the assistant message content.

    Raises RuntimeError on any failure so callers can fall back to templates.
    """
    try:
        from openai import OpenAI  # type: ignore
        client = OpenAI()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.4,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        raise RuntimeError(f"OpenAI call failed: {exc}") from exc


def _get_mode(state: dict) -> str:
    """Return the active AI mode from state or environment variable."""
    return state.get("ai_mode") or os.getenv("LLM_MODE", "balanced")


def _load_prompt(agent_name: str) -> str:
    """Load a system prompt from /prompts. Returns empty string on failure."""
    try:
        from services.file_loader import load_prompt
        return load_prompt(agent_name)
    except Exception as exc:
        logger.warning("Could not load prompt for %s: %s", agent_name, exc)
        return ""


def _state_summary(state: dict) -> str:
    """Serialise relevant state fields as a JSON user message for the LLM."""
    fields = {
        "technology": state.get("technology", ""),
        "target_audience": state.get("target_audience", ""),
        "technical_level": state.get("technical_level", ""),
        "objective": state.get("objective", ""),
        "operating_environment": state.get("operating_environment", ""),
        "prerequisites": state.get("prerequisites", []),
        "depth": state.get("depth", ""),
        "practical_examples": state.get("practical_examples", {}),
        "common_errors": state.get("common_errors", []),
        "expected_outcome": state.get("expected_outcome", ""),
        "source_documents_text": (state.get("source_documents_text") or "")[:3000],
        "brainstorm": state.get("brainstorm", {}),
        "prd": state.get("prd", {}),
        "spec": state.get("spec", {}),
        "draft_content": (state.get("draft_content") or "")[:6000],
        "review": state.get("review", {}),
    }
    return json.dumps(fields, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m/%Y")


def _s(state: dict, key: str, default: Any = "") -> Any:
    """Safe getter with default."""
    return state.get(key) or default


def _list(state: dict, key: str) -> list:
    v = state.get(key)
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip():
        return [item.strip() for item in v.split(",") if item.strip()]
    return []


def _level_label(level: str) -> str:
    mapping = {"iniciante": "🟢 Iniciante", "intermediário": "🟡 Intermediário", "avançado": "🔴 Avançado"}
    return mapping.get(level.lower(), level.capitalize())


def _reading_time(depth: str, examples: int) -> int:
    base = {"introdutório": 20, "completo": 45, "aprofundado com internals": 75}.get(depth, 35)
    return base + examples * 5


# ---------------------------------------------------------------------------
# 1. Brainstorm Agent
# ---------------------------------------------------------------------------

def brainstorm_agent(state: dict) -> dict:
    """
    Consolidate user inputs from the chat and state into a structured
    brainstorm dict. Uses OpenAI if available, otherwise builds from state.
    """
    state = dict(state)
    state.setdefault("errors", [])

    tech = _s(state, "technology", "a tecnologia escolhida")
    audience = _s(state, "target_audience", "desenvolvedores")
    level = _s(state, "technical_level", "intermediário")
    objective = _s(state, "objective", f"aprender a usar {tech} de forma prática")
    env = _s(state, "operating_environment", "Linux / macOS / Windows")
    prereqs = _list(state, "prerequisites") or [f"Conhecimentos básicos de {tech}", "Terminal / linha de comando"]
    depth = _s(state, "depth", "completo")
    errors = _list(state, "common_errors") or [
        "Erros de permissão e configuração",
        "Conflitos de versão",
        "Variáveis de ambiente ausentes",
    ]
    outcome = _s(state, "expected_outcome", f"Domínio prático de {tech} em um projeto real")

    # Try LLM router first
    system_prompt = _load_prompt("brainstorm")
    try:
        raw = run_llm_task(
            "brainstorm", system_prompt, _state_summary(state), mode=_get_mode(state)
        )
        parsed = json.loads(raw)
        state["brainstorm"] = parsed
        state["status"] = "brainstorm_done"
        logger.info("brainstorm_agent: LLM response parsed successfully.")
        return state
    except RuntimeError as exc:
        logger.warning("brainstorm_agent LLM unavailable, using template: %s", exc)
    except Exception as exc:
        logger.warning("brainstorm_agent LLM response not parseable, using template: %s", exc)

    # Template fallback (structures user input — no generative content)
    example_count = 3
    if isinstance(state.get("practical_examples"), dict):
        example_count = state["practical_examples"].get("count", 3) or 3

    brainstorm = {
        "technology": tech,
        "target_audience": audience,
        "technical_level": level,
        "objective": objective,
        "operating_environment": env,
        "prerequisites": prereqs,
        "depth": depth,
        "practical_examples": {
            "include": True,
            "count": example_count,
            "description": f"Exemplos práticos e funcionais cobrindo casos de uso reais de {tech}",
        },
        "common_errors": errors,
        "expected_outcome": outcome,
        "summary": (
            f"Tutorial {depth} sobre {tech} voltado para {audience} ({level}). "
            f"Objetivo: {objective}. "
            f"Ao final, o leitor terá alcançado: {outcome}."
        ),
    }

    state["brainstorm"] = brainstorm
    state["status"] = "brainstorm_done"
    logger.info("brainstorm_agent: template completed for technology=%r", tech)
    return state


# ---------------------------------------------------------------------------
# 2. PRD Agent
# ---------------------------------------------------------------------------

def prd_agent(state: dict) -> dict:
    """Generate a Product Requirements Document from the brainstorm output."""
    state = dict(state)
    state.setdefault("errors", [])
    bs = state.get("brainstorm", {})
    tech = bs.get("technology") or _s(state, "technology", "Tecnologia")
    audience = bs.get("target_audience", "desenvolvedores")
    level = bs.get("technical_level", "intermediário")
    objective = bs.get("objective", f"Aprender {tech}")
    depth = bs.get("depth", "completo")
    prereqs = bs.get("prerequisites", [])
    errors = bs.get("common_errors", [])
    outcome = bs.get("expected_outcome", f"Domínio de {tech}")
    ex_count = bs.get("practical_examples", {}).get("count", 3)
    reading_time = _reading_time(depth, ex_count)

    system_prompt = _load_prompt("prd")
    try:
        raw = run_llm_task(
            "prd", system_prompt, _state_summary(state), mode=_get_mode(state)
        )
        state["prd"] = json.loads(raw)
        state["status"] = "prd_done"
        logger.info("prd_agent: LLM response parsed successfully.")
        return state
    except RuntimeError as exc:
        logger.warning("prd_agent LLM unavailable, using template: %s", exc)
    except Exception as exc:
        logger.warning("prd_agent LLM response not parseable, using template: %s", exc)

    prd = {
        "title": f"Como Usar {tech}: Guia {depth.capitalize()} para {audience.capitalize()}",
        "description": (
            f"Um guia {depth} e prático sobre {tech}, criado especialmente para {audience}. "
            f"Este tutorial cobre desde a instalação até casos de uso reais, "
            f"incluindo exemplos funcionais e resolução de problemas comuns."
        ),
        "objective": objective,
        "users": {
            "primary": audience,
            "secondary": f"Profissionais de tecnologia que desejam revisar {tech}",
        },
        "scope": {
            "in_scope": [
                f"Introdução e conceitos fundamentais de {tech}",
                "Instalação e configuração do ambiente",
                f"Uso prático com {ex_count} exemplos funcionais",
                "Troubleshooting dos erros mais comuns",
                "Boas práticas e padrões recomendados",
                "Checklist de validação e próximos passos",
            ],
            "out_of_scope": [
                f"Comparações detalhadas de {tech} com outras tecnologias",
                "Deployment em produção de larga escala",
                "Otimizações avançadas de performance",
            ],
        },
        "features": [
            {"id": "F01", "name": "Conceitos Fundamentais", "description": f"Explicação clara do que é {tech} e por que usá-lo", "priority": "alta"},
            {"id": "F02", "name": "Instalação Passo a Passo", "description": "Guia de instalação com validação em cada etapa", "priority": "alta"},
            {"id": "F03", "name": "Exemplos Práticos", "description": f"{ex_count} exemplos com código completo e executável", "priority": "alta"},
            {"id": "F04", "name": "Troubleshooting", "description": f"Resolução dos {len(errors)} erros mais comuns identificados", "priority": "alta"},
            {"id": "F05", "name": "Boas Práticas", "description": "Recomendações baseadas em uso real em produção", "priority": "média"},
            {"id": "F06", "name": "Checklist Final", "description": "Lista de verificação para confirmar o aprendizado", "priority": "média"},
        ],
        "success_criteria": [
            f"O leitor consegue instalar e configurar {tech} sem erros",
            f"O leitor executa pelo menos {ex_count} exemplos práticos com sucesso",
            f"O leitor alcança o resultado esperado: {outcome}",
            "O leitor sabe como diagnosticar e corrigir os erros mais comuns",
            "O leitor conhece as boas práticas para uso em projetos reais",
        ],
        "constraints": [
            f"Ambiente: {bs.get('operating_environment', 'multiplataforma')}",
            f"Pré-requisitos: {', '.join(prereqs) if prereqs else 'conhecimentos básicos de terminal'}",
            f"Nível técnico: {level}",
        ],
        "risks": [
            {
                "risk": "Versões de dependências podem variar entre ambientes",
                "mitigation": "Especificar versões exatas testadas no tutorial",
            },
            {
                "risk": "Comandos podem diferir entre sistemas operacionais",
                "mitigation": "Indicar variações para Linux, macOS e Windows onde necessário",
            },
        ],
        "deliverables": [
            f"Tutorial completo em Markdown sobre {tech}",
            "Exemplos de código funcionais e comentados",
            "Guia de troubleshooting com soluções testadas",
            "Checklist de aprendizado",
        ],
        "estimated_reading_time_minutes": reading_time,
        "language": "pt-BR",
    }

    state["prd"] = prd
    state["status"] = "prd_done"
    logger.info("prd_agent: template completed, title=%r", prd["title"])
    return state


# ---------------------------------------------------------------------------
# 3. Spec Agent
# ---------------------------------------------------------------------------

def spec_agent(state: dict) -> dict:
    """Build a detailed tutorial specification with sections and checkpoints."""
    state = dict(state)
    state.setdefault("errors", [])
    bs = state.get("brainstorm", {})
    prd = state.get("prd", {})

    tech = bs.get("technology") or _s(state, "technology", "a tecnologia")
    level = bs.get("technical_level", "intermediário")
    errors = bs.get("common_errors", ["Erros de configuração", "Conflito de dependências"])
    ex_count = bs.get("practical_examples", {}).get("count", 3)
    depth = bs.get("depth", "completo")
    env = bs.get("operating_environment", "Linux / macOS / Windows")
    reading_time = prd.get("estimated_reading_time_minutes", _reading_time(depth, ex_count))

    system_prompt = _load_prompt("spec")
    try:
        raw = run_llm_task(
            "spec", system_prompt, _state_summary(state), mode=_get_mode(state)
        )
        state["spec"] = json.loads(raw)
        state["status"] = "spec_done"
        logger.info("spec_agent: LLM response parsed successfully.")
        return state
    except RuntimeError as exc:
        logger.warning("spec_agent LLM unavailable, using template: %s", exc)
    except Exception as exc:
        logger.warning("spec_agent LLM response not parseable, using template: %s", exc)

    per_section = max(3, reading_time // 14)

    sections = [
        {
            "order": 1, "title": "Visão Geral", "type": "introduction",
            "objective": f"Entender o que é {tech}, para que serve e quando usá-lo",
            "content_outline": [
                f"O que é {tech} e qual problema ele resolve",
                f"Casos de uso mais comuns de {tech}",
                f"Por que {tech} é relevante para {bs.get('target_audience', 'desenvolvedores')}",
                f"O que você vai construir neste tutorial",
            ],
            "code_examples": [],
            "commands": [],
            "tables": [{"title": f"Quando usar {tech}", "columns": ["Cenário", "Recomendado?", "Motivo"], "purpose": "Ajudar o leitor a decidir quando aplicar a tecnologia"}],
            "checkpoint": f"O leitor compreende o que é {tech} e por que vai aprendê-lo",
            "validation": "O leitor consegue explicar o problema que o tutorial resolve",
            "estimated_minutes": per_section,
        },
        {
            "order": 2, "title": "Para Quem é Este Tutorial", "type": "introduction",
            "objective": "Confirmar que o leitor tem o perfil certo para este tutorial",
            "content_outline": [
                f"Perfil do leitor ideal: {bs.get('target_audience', 'desenvolvedores')}",
                f"Nível técnico assumido: {level}",
                "O que você vai conseguir fazer ao final",
                "O que este tutorial NÃO cobre",
            ],
            "code_examples": [],
            "commands": [],
            "tables": [],
            "checkpoint": "O leitor confirma que este tutorial é adequado para seu perfil",
            "validation": "O leitor leu os pré-requisitos e está preparado para continuar",
            "estimated_minutes": 2,
        },
        {
            "order": 3, "title": "Pré-requisitos", "type": "introduction",
            "objective": "Garantir que o ambiente está pronto antes de começar",
            "content_outline": [f"Verificar: {req}" for req in (bs.get("prerequisites") or [f"Terminal / linha de comando", f"Conhecimentos básicos de {tech}"])],
            "code_examples": [{"language": "bash", "description": "Verificar versões instaladas", "is_runnable": True}],
            "commands": ["# Verificar versões — adapte para sua ferramenta"],
            "tables": [],
            "checkpoint": "Todos os pré-requisitos estão instalados e verificados",
            "validation": "Os comandos de verificação retornam as versões esperadas sem erros",
            "estimated_minutes": per_section,
        },
        {
            "order": 4, "title": "Conceitos Fundamentais", "type": "concept",
            "objective": f"Compreender os conceitos core de {tech} antes de instalar",
            "content_outline": [
                f"Arquitetura e componentes principais de {tech}",
                "Terminologia essencial com definições claras",
                "Como os componentes se relacionam entre si",
                "Diagrama de fluxo de dados (descrito em texto)",
            ],
            "code_examples": [],
            "commands": [],
            "tables": [{"title": "Terminologia", "columns": ["Termo", "Definição", "Analogia"], "purpose": "Glossário de referência rápida"}],
            "checkpoint": f"O leitor consegue explicar os {3} principais conceitos de {tech}",
            "validation": "O leitor entende o diagrama de arquitetura descrito",
            "estimated_minutes": per_section,
        },
        {
            "order": 5, "title": "Instalação", "type": "installation",
            "objective": f"Instalar {tech} corretamente no ambiente especificado",
            "content_outline": [
                f"Instalação em {env}",
                "Verificação da instalação",
                "Configuração inicial mínima",
                "Solução de problemas comuns de instalação",
            ],
            "code_examples": [{"language": "bash", "description": f"Instalar {tech}", "is_runnable": True}],
            "commands": [f"# Instalar {tech} — substitua pelo comando da sua plataforma"],
            "tables": [{"title": "Opções de instalação", "columns": ["Método", "Plataforma", "Recomendado"], "purpose": "Comparar métodos de instalação disponíveis"}],
            "checkpoint": f"{tech} instalado e respondendo corretamente",
            "validation": f"Comando de verificação de versão de {tech} retorna sem erros",
            "estimated_minutes": per_section,
        },
        {
            "order": 6, "title": "Configuração", "type": "configuration",
            "objective": f"Configurar {tech} para uso no projeto",
            "content_outline": [
                "Arquivo de configuração principal e sua estrutura",
                "Parâmetros obrigatórios e seus valores padrão",
                "Variáveis de ambiente relevantes",
                "Boas práticas de configuração desde o início",
            ],
            "code_examples": [{"language": "yaml", "description": "Arquivo de configuração base", "is_runnable": False}],
            "commands": [],
            "tables": [{"title": "Parâmetros de configuração", "columns": ["Parâmetro", "Tipo", "Padrão", "Descrição"], "purpose": "Referência dos parâmetros mais importantes"}],
            "checkpoint": f"{tech} configurado e pronto para uso",
            "validation": "A configuração é validada sem erros pelo próprio sistema",
            "estimated_minutes": per_section,
        },
        {
            "order": 7, "title": "Primeiro Exemplo Prático", "type": "example",
            "objective": f"Executar o primeiro exemplo funcional com {tech}",
            "content_outline": [
                "Estrutura do projeto de exemplo",
                "Escrita do código passo a passo com explicações",
                "Execução e verificação do resultado",
                "O que acontece internamente durante a execução",
            ],
            "code_examples": [{"language": "bash", "description": "Executar o primeiro exemplo", "is_runnable": True}],
            "commands": [f"# Execute o primeiro exemplo de {tech}"],
            "tables": [],
            "checkpoint": "O primeiro exemplo executa com sucesso e produz o resultado esperado",
            "validation": "A saída do comando corresponde à saída esperada documentada",
            "estimated_minutes": per_section,
        },
        {
            "order": 8, "title": "Uso Passo a Passo", "type": "example",
            "objective": f"Dominar o fluxo completo de trabalho com {tech}",
            "content_outline": [
                "Fluxo completo de trabalho (workflow) típico",
                f"Caso de uso real: {bs.get('objective', 'projeto prático')}",
                "Cada passo explicado com o porquê da decisão",
                f"Validação do resultado esperado: {bs.get('expected_outcome', 'resultado funcional')}",
            ],
            "code_examples": [
                {"language": "bash", "description": "Fluxo completo de trabalho", "is_runnable": True},
                {"language": "python", "description": "Exemplo de integração ou script auxiliar", "is_runnable": True},
            ],
            "commands": [],
            "tables": [],
            "checkpoint": f"O leitor consegue executar o fluxo completo de trabalho com {tech}",
            "validation": f"O resultado esperado foi alcançado: {bs.get('expected_outcome', '')}",
            "estimated_minutes": per_section * 2,
        },
        {
            "order": 9, "title": "Exemplos Avançados", "type": "example",
            "objective": f"Explorar recursos mais avançados de {tech}",
            "content_outline": [
                f"Exemplo avançado 1: Cenário de uso complexo com {tech}",
                "Exemplo avançado 2: Integração com outras ferramentas",
                "Customizações e extensões",
                "Quando e por que usar cada abordagem avançada",
            ],
            "code_examples": [{"language": "bash", "description": "Exemplo avançado", "is_runnable": True}],
            "commands": [],
            "tables": [],
            "checkpoint": "O leitor compreende e consegue adaptar os exemplos avançados",
            "validation": "O leitor executa pelo menos um exemplo avançado com sucesso",
            "estimated_minutes": per_section,
        },
        {
            "order": 10, "title": "Boas Práticas", "type": "best_practices",
            "objective": f"Adotar as melhores práticas de uso de {tech} em produção",
            "content_outline": [
                "O que FAZER: práticas recomendadas pela comunidade",
                "O que NÃO FAZER: antipadrões comuns e seus riscos",
                "Segurança: configurações críticas de segurança",
                "Performance: ajustes para ambientes de produção",
            ],
            "code_examples": [],
            "commands": [],
            "tables": [{"title": "Fazer vs. Não Fazer", "columns": ["✅ Fazer", "❌ Não fazer", "Motivo"], "purpose": "Referência rápida de boas práticas"}],
            "checkpoint": "O leitor conhece as principais boas práticas e antipadrões",
            "validation": "O leitor revisa seu próprio código e identifica melhorias",
            "estimated_minutes": per_section,
        },
        {
            "order": 11, "title": "Erros Comuns e Como Resolver", "type": "troubleshooting",
            "objective": "Diagnosticar e resolver os erros mais frequentes",
            "content_outline": [f"Erro: {err}" for err in (errors or ["Erro de configuração", "Conflito de dependências", "Permissão negada"])],
            "code_examples": [],
            "commands": [],
            "tables": [{"title": "Guia de Troubleshooting", "columns": ["Erro", "Causa", "Solução"], "purpose": "Referência rápida para diagnóstico"}],
            "checkpoint": "O leitor sabe como diagnosticar e resolver os erros mais comuns",
            "validation": "O leitor consegue identificar qual erro está enfrentando pela mensagem de saída",
            "estimated_minutes": per_section,
        },
        {
            "order": 12, "title": "Checklist Final", "type": "conclusion",
            "objective": "Confirmar que todos os objetivos de aprendizado foram atingidos",
            "content_outline": [f"✅ {criterion}" for criterion in (prd.get("success_criteria") or [f"Instalar {tech}", f"Executar exemplos práticos"])],
            "code_examples": [],
            "commands": [],
            "tables": [],
            "checkpoint": "Todos os itens do checklist foram verificados",
            "validation": "O leitor pode marcar todos os itens como concluídos",
            "estimated_minutes": 3,
        },
        {
            "order": 13, "title": "Conclusão", "type": "conclusion",
            "objective": "Consolidar o aprendizado e recapitular os pontos principais",
            "content_outline": [
                f"Recapitulação do que foi aprendido sobre {tech}",
                f"Resultado alcançado: {bs.get('expected_outcome', 'domínio prático')}",
                "Casos de uso onde aplicar o conhecimento adquirido",
            ],
            "code_examples": [],
            "commands": [],
            "tables": [],
            "checkpoint": "O leitor tem uma visão consolidada do que aprendeu",
            "validation": "O leitor consegue descrever o que aprendeu em suas próprias palavras",
            "estimated_minutes": 3,
        },
        {
            "order": 14, "title": "Próximos Passos", "type": "conclusion",
            "objective": "Orientar o leitor sobre como continuar evoluindo",
            "content_outline": [
                f"Recursos oficiais de {tech}: documentação, repositórios, comunidade",
                f"Tópicos avançados para explorar depois de dominar o básico de {tech}",
                "Projetos práticos sugeridos para consolidar o aprendizado",
                "Ferramentas complementares que trabalham bem com " + tech,
            ],
            "code_examples": [],
            "commands": [],
            "tables": [],
            "checkpoint": "O leitor tem um plano claro para continuar aprendendo",
            "validation": "O leitor escolheu pelo menos um próximo passo para executar",
            "estimated_minutes": 3,
        },
    ]

    spec = {
        "tutorial_title": prd.get("title", f"Tutorial Completo de {tech}"),
        "tech_stack": [{"name": tech, "version": "latest", "purpose": "Tecnologia principal do tutorial"}],
        "sections": sections,
        "mandatory_sections": ["Visão Geral", "Pré-requisitos", "Conceitos Fundamentais", "Instalação", "Checklist Final", "Conclusão", "Próximos Passos"],
        "didactic_order_rationale": "Progressão: contexto → teoria → instalação → prática simples → prática avançada → operação → consolidação",
        "total_estimated_minutes": reading_time,
        "complexity_notes": f"Atenção especial às seções de Instalação e Configuração — variam por sistema operacional. Troubleshooting deve cobrir todos os erros listados no Brainstorm.",
    }

    state["spec"] = spec
    state["status"] = "spec_done"
    logger.info("spec_agent: template completed with %d sections", len(sections))
    return state


# ---------------------------------------------------------------------------
# 4. Writer Agent
# ---------------------------------------------------------------------------

def writer_agent(state: dict) -> dict:
    """Generate the complete tutorial Markdown from brainstorm + PRD + spec."""
    state = dict(state)
    state.setdefault("errors", [])
    bs = state.get("brainstorm", {})
    prd = state.get("prd", {})
    spec = state.get("spec", {})

    tech = bs.get("technology") or _s(state, "technology", "a tecnologia")
    level = bs.get("technical_level", "intermediário")
    env = bs.get("operating_environment", "Linux / macOS / Windows")
    prereqs = bs.get("prerequisites", [f"Conhecimentos básicos de {tech}", "Terminal / linha de comando"])
    errors = bs.get("common_errors", ["Permissão negada", "Conflito de versões", "Variáveis de ambiente ausentes"])
    outcome = bs.get("expected_outcome", f"Domínio prático de {tech}")
    objective = bs.get("objective", f"Aprender {tech} na prática")
    audience = bs.get("target_audience", "desenvolvedores")
    depth = bs.get("depth", "completo")
    ex_count = bs.get("practical_examples", {}).get("count", 3)
    reading_time = spec.get("total_estimated_minutes") or prd.get("estimated_reading_time_minutes") or _reading_time(depth, ex_count)
    title = spec.get("tutorial_title") or prd.get("title") or f"Como Usar {tech}: Guia {depth.capitalize()}"
    source_docs = _s(state, "source_documents_text", "")

    # LLM required — no generative template fallback for writer
    system_prompt = _load_prompt("writer")
    try:
        raw = run_llm_task(
            "writer", system_prompt, _state_summary(state), mode=_get_mode(state)
        )
        state["draft_content"] = raw
        state["status"] = "writer_done"
        logger.info("writer_agent: LLM draft generated (%d chars)", len(raw))
        return state
    except RuntimeError as exc:
        logger.error("writer_agent LLM unavailable: %s", exc)
        state["errors"].append(f"writer_agent: {exc}")
        state["draft_content"] = ""
        state["status"] = "error"
        return state

    # ----- Template engine (legacy — kept for reference only, not reached) -----
    prereqs_md = "\n".join(f"- {p}" for p in prereqs)
    errors_rows = "\n".join(
        f"| `{err.split()[0] if err.split() else err}` | {err} | Verifique a seção de instalação e os logs do sistema |"
        for err in errors
    )
    success_criteria = "\n".join(f"- [ ] {c}" for c in (prd.get("success_criteria") or [f"Instalar e configurar {tech}", f"Executar {ex_count} exemplos práticos", f"Alcançar: {outcome}"]))
    in_scope = "\n".join(f"- {s}" for s in (prd.get("scope", {}).get("in_scope") or [f"Conceitos e instalação de {tech}", "Exemplos práticos", "Troubleshooting"]))
    out_scope = "\n".join(f"- {s}" for s in (prd.get("scope", {}).get("out_of_scope") or ["Optimizações avançadas de produção", "Comparações com tecnologias alternativas"]))
    source_note = f"\n> 💡 **Nota:** Este tutorial incorpora conteúdo dos documentos de referência fornecidos.\n" if source_docs.strip() else ""

    examples_md = ""
    for i in range(1, ex_count + 1):
        examples_md += dedent(f"""
            ### Exemplo {i} — Caso de uso {i}

            Este exemplo demonstra um caso de uso prático de **{tech}**.

            ```bash
            # Exemplo {i}: substitua pelos parâmetros do seu ambiente
            # Ajuste conforme seu ambiente
            ```

            ```
            # Saída esperada:
            # Operação concluída com sucesso
            ```

            > ✅ **Checkpoint {i}:** O exemplo {i} executou sem erros e produziu a saída esperada.

        """)

    advanced_examples = dedent(f"""
        ### Caso de uso avançado: Integração e automação

        O exemplo a seguir demonstra como integrar **{tech}** em um fluxo de trabalho automatizado.

        ```python
        # Exemplo avançado — Ajuste conforme seu ambiente
        # Este script demonstra automação com {tech}

        import subprocess
        import sys

        def run_{tech.lower().replace(' ', '_').replace('-', '_')}():
            \"\"\"Execute {tech} de forma programática.\"\"\"
            try:
                result = subprocess.run(
                    ["echo", "Integração com {tech} funcionando"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                print(result.stdout)
                return True
            except subprocess.CalledProcessError as e:
                print(f"Erro: {{e.stderr}}", file=sys.stderr)
                return False

        if __name__ == "__main__":
            success = run_{tech.lower().replace(' ', '_').replace('-', '_')}()
            sys.exit(0 if success else 1)
        ```

        > ✅ **Checkpoint:** O script executa sem erros e imprime a mensagem de confirmação.

    """)

    best_practices_rows = dedent(f"""
        | ✅ Sempre versionar arquivos de configuração | ❌ Nunca commitar credenciais ou secrets | Risco de segurança |
        | ✅ Usar variáveis de ambiente para configurações | ❌ Não hardcodar valores sensíveis no código | Manutenibilidade |
        | ✅ Testar em ambiente isolado antes de produção | ❌ Não aplicar mudanças diretamente em produção | Estabilidade |
        | ✅ Documentar decisões de configuração | ❌ Não deixar configurações sem comentários | Colaboração |
        | ✅ Monitorar logs em produção | ❌ Não ignorar warnings e deprecation notices | Confiabilidade |
    """)

    draft = dedent(f"""\
        # {title}

        **Nível:** {_level_label(level)}
        **Tempo estimado:** {reading_time} minutos
        **Tecnologia:** {tech}
        **Ambiente:** {env}
        {source_note}
        ---

        ## 1. Visão Geral

        **{tech}** é uma tecnologia amplamente adotada para resolver problemas de **{objective.lower()}**.

        Neste tutorial, você vai aprender a usar **{tech}** de forma prática, seguindo um caminho progressivo do básico ao avançado. Ao final, você terá alcançado: **{outcome}**.

        Este guia é voltado para **{audience}** e tem profundidade **{depth}**.

        > 💡 **Dica:** Siga cada seção em ordem. Cada etapa é pré-requisito para a próxima.

        ---

        ## 2. Para Quem é Este Tutorial

        Este tutorial foi criado para: **{audience}**.

        **Você vai conseguir:**
        {in_scope}

        **Este tutorial NÃO cobre:**
        {out_scope}

        ---

        ## 3. Pré-requisitos

        Antes de começar, certifique-se de que você tem:

        {prereqs_md}

        **Verificação rápida:**

        ```bash
        # Execute estes comandos para confirmar que os pré-requisitos estão instalados
        # Adapte os nomes dos comandos para sua tecnologia específica
        echo "Verificando ambiente..."
        # Ajuste conforme seu ambiente
        ```

        ```
        # Saída esperada: versões instaladas sem erros
        ```

        > ✅ **Checkpoint:** Todos os pré-requisitos estão instalados e verificados.

        ---

        ## 4. Conceitos Fundamentais

        Antes de instalar e usar **{tech}**, é importante entender os conceitos que sustentam seu funcionamento.

        ### O que é {tech}?

        **{tech}** é uma ferramenta/tecnologia que resolve o problema de **{objective.lower()}**. Ela é amplamente usada por **{audience}** que precisam de uma solução **{level}** para seus projetos.

        ### Componentes principais

        | Componente | Função | Analogia |
        |------------|--------|----------|
        | Core | Funcionalidade principal do sistema | Motor de um carro |
        | Configuração | Define o comportamento do sistema | Painel de controle |
        | Interface | Como você interage com o sistema | Volante e pedais |

        ### Como {tech} funciona

        O fluxo básico de **{tech}** segue este padrão:

        1. **Entrada:** Você fornece os dados ou comandos
        2. **Processamento:** O sistema executa a lógica principal
        3. **Saída:** O resultado é entregue no formato esperado

        > ✅ **Checkpoint:** Você compreende o que é {tech} e como seus componentes se relacionam.

        ---

        ## 5. Instalação

        Instale **{tech}** seguindo os passos abaixo para **{env}**.

        ```bash
        # Passo 1: Preparar o ambiente
        # Ajuste o comando conforme seu sistema operacional e gerenciador de pacotes

        # Passo 2: Instalar {tech}
        # Substitua pelo comando oficial de instalação da sua plataforma

        # Passo 3: Verificar a instalação
        echo "Instalação de {tech} concluída"
        ```

        ```
        # Saída esperada:
        # {tech} instalado com sucesso
        # Versão: (versão instalada)
        ```

        ### Opções de instalação

        | Método | Plataforma | Recomendado | Observação |
        |--------|------------|-------------|------------|
        | Gerenciador de pacotes | Linux | ✅ Sim | Mais simples para começar |
        | Binário oficial | Todos | ✅ Sim | Maior controle de versão |
        | Docker | Todos | ⚠️ Avançado | Requer Docker instalado |

        > ⚠️ **Atenção:** Sempre instale a versão estável mais recente. Evite versões de desenvolvimento em ambientes de produção.

        > ✅ **Checkpoint:** {tech} está instalado e a verificação de versão retorna sem erros.

        ---

        ## 6. Configuração

        Com **{tech}** instalado, configure-o para o seu projeto.

        ```yaml
        # Arquivo de configuração base — adapte os valores ao seu ambiente
        # Salve como: config.yaml (ou o nome adequado para sua tecnologia)

        # Configurações gerais
        environment: development
        debug: false

        # Configurações específicas de {tech}
        # Ajuste conforme a documentação oficial
        settings:
          option_1: valor_padrao
          option_2: valor_padrao
        ```

        ### Parâmetros principais

        | Parâmetro | Tipo | Padrão | Descrição |
        |-----------|------|--------|-----------|
        | `environment` | string | `development` | Ambiente de execução |
        | `debug` | boolean | `false` | Ativa logs detalhados |
        | `timeout` | integer | `30` | Tempo limite em segundos |

        ### Variáveis de ambiente

        | Variável | Obrigatória | Descrição |
        |----------|-------------|-----------|
        | `APP_ENV` | Não | Define o ambiente (`development`, `production`) |
        | `APP_DEBUG` | Não | Ativa modo debug (`true`/`false`) |

        > 💡 **Dica:** Use um arquivo `.env` para gerenciar variáveis de ambiente localmente. Nunca commite este arquivo no Git.

        > ✅ **Checkpoint:** A configuração é válida e o sistema inicia sem erros de configuração.

        ---

        ## 7. Primeiro Exemplo Prático

        Vamos executar o primeiro exemplo com **{tech}** para confirmar que tudo está funcionando.

        ```bash
        # Primeiro exemplo: operação básica com {tech}
        # Ajuste os parâmetros conforme seu ambiente

        echo "Iniciando primeiro exemplo com {tech}..."
        # Substitua pela operação básica da sua tecnologia
        ```

        ```
        # Saída esperada:
        # Operação executada com sucesso
        # Status: OK
        ```

        **O que aconteceu:**
        1. O sistema inicializou com as configurações definidas
        2. A operação básica foi executada
        3. O resultado foi retornado no formato esperado

        > ✅ **Checkpoint:** O primeiro exemplo executou com sucesso e produziu a saída esperada acima.

        ---

        ## 8. Uso Passo a Passo

        Agora que o primeiro exemplo funcionou, vamos seguir o fluxo completo de trabalho com **{tech}**.

        **Objetivo desta seção:** {objective}

        {examples_md}

        > ✅ **Checkpoint:** Você executou {ex_count} exemplos práticos com sucesso e atingiu: **{outcome}**

        ---

        ## 9. Exemplos Avançados

        {advanced_examples}

        ---

        ## 10. Boas Práticas

        Adote estas práticas para usar **{tech}** de forma profissional e segura.

        | ✅ Fazer | ❌ Não fazer | Motivo |
        |----------|-------------|--------|
        {best_practices_rows}

        ### Checklist de segurança

        - [ ] Variáveis de ambiente configuradas (sem valores hardcoded)
        - [ ] Arquivos de configuração fora do controle de versão quando contêm secrets
        - [ ] Logs monitorados em produção
        - [ ] Backups configurados para dados críticos

        ---

        ## 11. Erros Comuns e Como Resolver

        | Erro | Causa provável | Solução |
        |------|---------------|---------|
        {errors_rows}

        ### Diagnóstico geral

        Quando algo der errado, siga este processo:

        1. **Leia o log completo** — a maioria dos erros tem mensagem clara
        2. **Verifique a configuração** — compare com o exemplo deste tutorial
        3. **Verifique as permissões** — erros de permissão são os mais comuns
        4. **Isole o problema** — teste cada componente separadamente
        5. **Consulte a documentação oficial** — pode haver mudanças de API

        > 💡 **Dica:** Ative o modo debug temporariamente para obter logs detalhados.

        ---

        ## 12. Checklist Final

        Antes de considerar este tutorial concluído, verifique:

        {success_criteria}
        - [ ] Você conhece os principais erros e como resolvê-los
        - [ ] Você sabe onde encontrar a documentação oficial de {tech}

        ---

        ## 13. Conclusão

        Parabéns! Você concluiu o tutorial de **{tech}**.

        **O que você aprendeu:**
        - Os conceitos fundamentais de {tech} e sua arquitetura
        - Como instalar e configurar {tech} em {env}
        - {ex_count} exemplos práticos com casos de uso reais
        - Boas práticas e como evitar os erros mais comuns
        - Como diagnosticar e resolver problemas

        **Resultado alcançado:** {outcome}

        ---

        ## 14. Próximos Passos

        Agora que você domina o básico de **{tech}**, explore:

        - 📚 **Documentação oficial** — sempre a fonte mais confiável e atualizada
        - 🔧 **Projetos práticos** — aplique o que aprendeu em um projeto pessoal ou profissional
        - 🌐 **Comunidade** — fóruns, repositórios e grupos de discussão da tecnologia
        - 📈 **Tópicos avançados** — performance, segurança, integração com outras ferramentas

        > 💡 **Sugestão:** Escolha um projeto real para aplicar {tech} nos próximos 7 dias. A prática é o melhor caminho para a consolidação do aprendizado.

        ---
        *Tutorial gerado por TutorialGen · {_now_str()}*
    """)

    state["draft_content"] = draft
    state["status"] = "writer_done"
    logger.info("writer_agent: template draft generated (%d chars)", len(draft))
    return state


# ---------------------------------------------------------------------------
# 5. Reviewer Agent
# ---------------------------------------------------------------------------

def reviewer_agent(state: dict) -> dict:
    """Analyse the draft and return a structured review report."""
    state = dict(state)
    state.setdefault("errors", [])
    draft = state.get("draft_content", "")
    spec = state.get("spec", {})
    bs = state.get("brainstorm", {})

    if not draft.strip():
        state["errors"].append("reviewer_agent: draft_content is empty — cannot review.")
        state["review"] = {"status": "NEEDS_REVISION", "overall_score": 0.0, "issues": [{"id": "I00", "severity": "critical", "criterion": "completeness", "section": "global", "description": "O rascunho do tutorial está vazio.", "fix_instruction": "Execute o writer_agent antes do reviewer_agent."}], "missing_sections": [], "positive_aspects": [], "summary": "Tutorial vazio, não é possível revisar."}
        return state

    system_prompt = _load_prompt("reviewer")
    try:
        raw = run_llm_task(
            "reviewer", system_prompt, _state_summary(state), mode=_get_mode(state)
        )
        state["review"] = json.loads(raw)
        state["status"] = "reviewer_done"
        logger.info("reviewer_agent: LLM review parsed.")
        return state
    except RuntimeError as exc:
        logger.warning("reviewer_agent LLM unavailable, using structural analysis: %s", exc)
    except Exception as exc:
        logger.warning("reviewer_agent LLM response not parseable, using structural analysis: %s", exc)

    # Structural analysis fallback (no LLM needed — checks draft structure)
    tech = bs.get("technology", "a tecnologia")
    mandatory = spec.get("mandatory_sections", ["Visão Geral", "Pré-requisitos", "Conclusão", "Próximos Passos"])
    missing = [s for s in mandatory if s not in draft]
    has_checkpoints = "✅ **Checkpoint" in draft
    has_code_blocks = "```" in draft
    has_table = "|" in draft
    has_errors_section = "Erros Comuns" in draft or "Troubleshooting" in draft
    has_best_practices = "Boas Práticas" in draft
    char_count = len(draft)

    issues = []
    if missing:
        issues.append({
            "id": "I01", "severity": "major",
            "criterion": "completeness", "section": "global",
            "description": f"Seções obrigatórias ausentes: {', '.join(missing)}",
            "fix_instruction": f"Adicionar as seções: {', '.join(missing)} seguindo a estrutura da Spec.",
        })
    if not has_checkpoints:
        issues.append({
            "id": "I02", "severity": "minor",
            "criterion": "didactic_order", "section": "global",
            "description": "Nenhum checkpoint `> ✅ **Checkpoint:**` encontrado.",
            "fix_instruction": "Adicionar um checkpoint ao final de cada seção principal.",
        })
    if not has_code_blocks:
        issues.append({
            "id": "I03", "severity": "critical",
            "criterion": "code_examples", "section": "global",
            "description": "Nenhum bloco de código encontrado no tutorial.",
            "fix_instruction": "Adicionar blocos de código com linguagem especificada para todos os comandos e exemplos.",
        })
    if not has_errors_section:
        issues.append({
            "id": "I04", "severity": "major",
            "criterion": "completeness", "section": "Erros Comuns",
            "description": "Seção de erros comuns e troubleshooting ausente.",
            "fix_instruction": f"Adicionar seção '11. Erros Comuns e Como Resolver' cobrindo os erros listados no Brainstorm: {bs.get('common_errors', [])}",
        })

    scores = {
        "clarity": 8 if char_count > 2000 else 5,
        "completeness": 9 if not missing else 6,
        "technical_consistency": 8 if has_code_blocks else 5,
        "content_gaps": 8 if has_errors_section else 6,
        "didactic_order": 9 if has_checkpoints else 7,
        "code_examples": 9 if has_code_blocks else 4,
        "commands": 8 if has_code_blocks else 4,
    }
    overall = round(sum(scores.values()) / len(scores), 1)
    status = "APPROVED" if overall >= 8.5 and not any(i["severity"] == "critical" for i in issues) else "NEEDS_REVISION"

    review = {
        "status": status,
        "overall_score": overall,
        "scores": scores,
        "issues": issues,
        "missing_sections": missing,
        "positive_aspects": [
            f"Estrutura de 14 seções bem organizada e progressiva" if char_count > 3000 else "Conteúdo presente",
            f"Tabelas de referência incluídas" if has_table else "Formatação básica presente",
            f"Seção de boas práticas incluída" if has_best_practices else "Tutorial cobre os conceitos básicos",
        ],
        "summary": (
            f"Tutorial sobre {tech} gerado com score geral {overall}/10. "
            f"{'Aprovado para publicação.' if status == 'APPROVED' else f'{len(issues)} issue(s) identificado(s) para correção.'} "
            f"{'Checkpoints presentes.' if has_checkpoints else 'Checkpoints ausentes — recomendado adicionar.'} "
            f"{'Blocos de código presentes.' if has_code_blocks else 'CRÍTICO: Blocos de código ausentes.'}"
        ),
    }

    state["review"] = review
    state["status"] = "reviewer_done"
    logger.info("reviewer_agent: review done — status=%s score=%.1f issues=%d", status, overall, len(issues))
    return state


# ---------------------------------------------------------------------------
# 6. Fixer Agent
# ---------------------------------------------------------------------------

def fixer_agent(state: dict) -> dict:
    """Apply reviewer fixes and return the final polished Markdown tutorial."""
    state = dict(state)
    state.setdefault("errors", [])
    draft = state.get("draft_content", "")
    review = state.get("review", {})
    bs = state.get("brainstorm", {})
    spec = state.get("spec", {})

    if not draft.strip():
        state["errors"].append("fixer_agent: draft_content is empty.")
        state["final_content_md"] = ""
        return state

    # LLM required — no generative template fallback for fixer
    system_prompt = _load_prompt("fixer")
    try:
        raw = run_llm_task(
            "fixer", system_prompt, _state_summary(state), mode=_get_mode(state)
        )
        state["final_content_md"] = raw
        state["status"] = "complete"
        logger.info("fixer_agent: LLM final (%d chars)", len(raw))
        return state
    except RuntimeError as exc:
        logger.error("fixer_agent LLM unavailable: %s", exc)
        state["errors"].append(f"fixer_agent: {exc}")
        state["final_content_md"] = ""
        state["status"] = "error"
        return state

    review_status = review.get("status", "NEEDS_REVISION")
    issues = review.get("issues", [])
    missing_sections = review.get("missing_sections", [])
    tech = bs.get("technology", "a tecnologia")
    errors_list = bs.get("common_errors", [])

    fixed_draft = draft

    # Apply structural fixes based on review issues
    for issue in issues:
        if issue.get("severity") == "critical" and issue.get("criterion") == "code_examples":
            if "```" not in fixed_draft:
                fixed_draft += dedent("""

                    ---

                    > ⚠️ **Nota do Fixer:** Blocos de código foram identificados como ausentes pelo revisor.
                    > Todos os comandos executáveis devem ser formatados em blocos de código com linguagem especificada.

                """)

    # Add missing mandatory sections
    for section in missing_sections:
        if section not in fixed_draft:
            if section == "Próximos Passos":
                fixed_draft += dedent(f"""

                    ## 14. Próximos Passos

                    - 📚 Documentação oficial de **{tech}**
                    - 🔧 Projetos práticos para consolidar o aprendizado
                    - 🌐 Comunidade e fóruns de discussão
                    - 📈 Tópicos avançados: performance, segurança, integração

                """)
            elif section == "Checklist Final":
                criteria = "\n".join(f"- [ ] {c}" for c in (state.get("prd", {}).get("success_criteria") or [f"Instalei e configurei {tech}", "Executei os exemplos práticos", "Entendi as boas práticas"]))
                fixed_draft += dedent(f"""

                    ## 12. Checklist Final

                    {criteria}
                    - [ ] Conheço os principais erros e como resolvê-los

                """)

    # Ensure troubleshooting section exists
    if "Erros Comuns" not in fixed_draft and errors_list:
        errors_rows = "\n".join(
            f"| `{e.split()[0] if e.split() else e}` | {e} | Verifique a documentação e os logs |"
            for e in errors_list
        )
        fixed_draft += dedent(f"""

            ## 11. Erros Comuns e Como Resolver

            | Erro | Descrição | Solução |
            |------|-----------|---------|
            {errors_rows}

            > ✅ **Checkpoint:** Você sabe identificar e resolver os erros mais comuns.

        """)

    # Build changelog
    applied = []
    skipped = []
    for issue in issues:
        sev = issue.get("severity", "minor")
        if sev in ("critical", "major"):
            applied.append(f"| {issue['id']} | {sev} | {issue.get('section', '—')} | {issue.get('fix_instruction', '—')[:80]} |")
        else:
            skipped.append(issue["id"])

    applied_md = "\n".join(applied) if applied else "| — | — | — | Nenhum issue crítico encontrado |"
    skipped_md = f"Issues minor ignorados: {', '.join(skipped)}" if skipped else "Nenhum issue ignorado."
    original_score = review.get("overall_score", 0)
    final_score = min(10.0, original_score + 0.5 * len(applied))

    changelog = dedent(f"""

        ---

        ## Changelog de Revisão

        | ID | Severidade | Seção | Correção aplicada |
        |----|------------|-------|-------------------|
        {applied_md}

        **Status da revisão:** {review_status} → FINALIZADO
        **{skipped_md}**
        **Score original:** {original_score} → **Score estimado pós-correção:** {min(10.0, final_score):.1f}

        *Tutorial revisado e finalizado por TutorialGen · {_now_str()}*
    """)

    final = fixed_draft.rstrip() + "\n" + changelog

    state["final_content_md"] = final
    state["status"] = "complete"
    logger.info(
        "fixer_agent: finalised (%d chars, %d issues applied, %d skipped)",
        len(final), len(applied), len(skipped),
    )
    return state


# ---------------------------------------------------------------------------
# 7. Tutorial Tutor Agent
# ---------------------------------------------------------------------------

def tutorial_tutor_agent(state: dict) -> dict:
    """
    Answer user questions about a specific tutorial using its full context.

    Expected state keys:
        question              : str   — user's current question
        tutorial_content      : str   — final_content_md of the tutorial
        prd                   : str | dict — product requirements doc
        spec                  : str | dict — technical specification
        source_documents_text : str   — original uploaded docs
        chat_history          : list[dict] — [{role, message}, ...]
        user_level            : str   — "iniciante"|"intermediário"|"avançado"
        ai_mode               : str   — "balanced"|"economic"|"quality"

    Returns updated state with:
        answer          : str — tutor's response in Markdown
        tutor_complexity: str — complexity level used for routing
    """
    state = dict(state)
    state.setdefault("errors", [])

    question = state.get("question", "").strip()
    if not question:
        state["answer"] = "Por favor, faça uma pergunta sobre o tutorial."
        return state

    tutorial_content = state.get("tutorial_content", "")
    prd = state.get("prd", "")
    spec = state.get("spec", "")
    source_docs = state.get("source_documents_text", "")
    chat_history: list[dict] = state.get("chat_history", [])
    user_level = state.get("user_level", "intermediário")

    # Serialize prd/spec if they are dicts
    prd_text = json.dumps(prd, ensure_ascii=False, indent=2) if isinstance(prd, dict) else str(prd or "")
    spec_text = json.dumps(spec, ensure_ascii=False, indent=2) if isinstance(spec, dict) else str(spec or "")

    # Determine task complexity from the question content
    complex_keywords = [
        "arquitetura", "architecture", "troubleshoot", "erro", "error",
        "debug", "avançado", "advanced", "comparação", "comparison",
        "exercício", "exercise", "internals", "exemplo avançado",
        "aprofunde", "explique em detalhes", "por que", "como funciona",
    ]
    medium_keywords = [
        "explique", "explain", "exemplo", "example", "adaptar",
        "simplify", "simplifique", "próximos passos", "next steps",
    ]
    q_lower = question.lower()
    if len(question) > 500 or any(kw in q_lower for kw in complex_keywords):
        task_type = "tutor_complex"
    elif any(kw in q_lower for kw in medium_keywords):
        task_type = "tutor_simple"  # medium difficulty handled as tutor_simple
    else:
        task_type = "tutor_simple"

    state["tutor_complexity"] = task_type

    # Build the last 10 turns of chat history as a context block
    history_block = ""
    if chat_history:
        recent = chat_history[-10:]
        lines = []
        for turn in recent:
            role_label = "Usuário" if turn.get("role") == "user" else "Tutor"
            lines.append(f"{role_label}: {turn.get('message', '')}")
        history_block = "\n".join(lines)

    # Truncate large content to fit within a reasonable prompt size
    content_limit = 8000
    tutorial_snippet = tutorial_content[:content_limit]
    if len(tutorial_content) > content_limit:
        tutorial_snippet += f"\n\n... [tutorial truncado — {len(tutorial_content):,} chars no total]"

    source_snippet = source_docs[:2000] if source_docs.strip() else ""

    user_prompt = json.dumps(
        {
            "question": question,
            "user_level": user_level,
            "tutorial_content": tutorial_snippet,
            "prd": prd_text[:1500],
            "spec": spec_text[:1500],
            "source_documents": source_snippet,
            "chat_history_recent": history_block,
        },
        ensure_ascii=False,
        indent=2,
    )

    system_prompt = _load_prompt("tutorial_tutor_agent")

    # Call LLM
    try:
        answer = run_llm_task(
            task_type, system_prompt, user_prompt,
            context=question,
            mode=_get_mode(state),
        )
        state["answer"] = answer
        logger.info(
            "tutorial_tutor_agent: answered (task=%s, answer_len=%d)",
            task_type, len(answer),
        )
    except RuntimeError as exc:
        logger.error("tutorial_tutor_agent LLM unavailable: %s", exc)
        state["errors"].append(f"tutorial_tutor_agent: {exc}")
        state["answer"] = (
            f"**Não foi possível gerar a resposta.**\n\n"
            f"{exc}\n\n"
            f"Configure a variável `OPENAI_API_KEY` para usar o tutor."
        )

    return state
