import json
import logging
import os

import streamlit as st

from services.database import (
    add_chat_message,
    clear_chat_history,
    count_tutorials,
    get_chat_history,
    get_tutorial_by_id,
    init_db,
    list_tutorials,
    save_tutorial,
    search_tutorials,
)
from services.file_loader import ACCEPTED_EXTENSIONS, process_uploaded_file
from services.memory import (
    MAX_INTERACTIONS,
    add_interaction,
    clear_memory,
    get_context_block,
    get_last_n,
    interaction_count,
    list_history,
)
from services.langgraph_flow import run_tutorial_graph
from services.agents import brainstorm_agent, tutorial_tutor_agent
from services.llm_router import (
    get_configured_models,
    get_last_model_used,
    classify_task_complexity,
)
from utils.date_utils import format_display_date, relative_time
from utils.markdown_utils import truncate_preview
from utils.input_sanitizer import (
    sanitize_title,
    sanitize_technology,
    sanitize_tags,
    sanitize_question,
    sanitize_markdown_content,
)
from utils.ui_helpers import render_export_buttons, render_content_metrics, safe_load_tutorial

logging.basicConfig(level=logging.INFO)

st.set_page_config(
    page_title="TutorialGen",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global CSS polish ──────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    /* Markdown headings */
    .stMarkdown h1 { border-bottom: 2px solid #e0e0e0; padding-bottom: .35em; margin-top: .8em; }
    .stMarkdown h2 { border-bottom: 1px solid #ececec; padding-bottom: .2em; margin-top: .7em; }
    .stMarkdown h3 { margin-top: .6em; }
    /* Inline code */
    .stMarkdown code {
        background: #f6f8fa;
        border: 1px solid #e1e4e8;
        border-radius: 4px;
        padding: .15em .45em;
        font-size: .9em;
    }
    /* Code blocks */
    .stMarkdown pre {
        background: #f6f8fa;
        border: 1px solid #e1e4e8;
        border-radius: 6px;
        padding: 1em 1.2em;
        overflow-x: auto;
    }
    /* Blockquotes */
    .stMarkdown blockquote {
        border-left: 4px solid #0068c9;
        padding-left: .9em;
        color: #555;
        margin: .5em 0;
    }
    /* Tables */
    .stMarkdown table { border-collapse: collapse; width: 100%; margin: .5em 0; }
    .stMarkdown table th { background: #f0f2f6; font-weight: 600; text-align: left; }
    .stMarkdown table th,
    .stMarkdown table td { border: 1px solid #e0e0e0; padding: 7px 12px; }
    .stMarkdown table tr:nth-child(even) td { background: #fafbfc; }
    /* Horizontal rule */
    .stMarkdown hr { border: none; border-top: 1px solid #e0e0e0; margin: 1.2em 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Database bootstrap
# ---------------------------------------------------------------------------
if "db_initialised" not in st.session_state:
    try:
        init_db()
        st.session_state.db_initialised = True
    except Exception as exc:
        st.error(f"Erro ao inicializar o banco de dados: {exc}")
        st.stop()

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "extracted_docs": [],
    "brainstorm_data": {},
    "pipeline_result": None,
    "final_content": "",
    "pipeline_log": [],
    "pipeline_done": False,
    "save_success_id": None,
    "ai_mode": os.getenv("LLM_MODE", "balanced"),
    # Tutor page state
    "tutor_tutorial_id": None,
    "tutor_tutorial": None,
    "tutor_chat": [],
    # Save confirmation gate
    "confirm_save": False,
}
for _k, _v in _DEFAULTS.items():
    st.session_state.setdefault(_k, _v)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
MENU_OPTIONS = {
    "📝 Criar Tutorial": "criar",
    "🔍 Pesquisar Tutoriais": "pesquisar",
    "💬 Conversar com Tutorial": "conversar",
    "ℹ️ Sobre": "sobre",
}

with st.sidebar:
    st.title("📚 TutorialGen")
    st.markdown("---")
    selection = st.radio(
        "Navegação",
        list(MENU_OPTIONS.keys()),
        label_visibility="collapsed",
    )
    page = MENU_OPTIONS[selection]
    st.markdown("---")

    total = 0
    try:
        total = count_tutorials()
    except Exception:
        pass

    col_s1, col_s2 = st.columns(2)
    col_s1.metric("Tutoriais salvos", total)
    col_s2.metric("Interações", interaction_count())
    st.caption("v1.0.0 — Hardening + Preparação para Escala")

    st.markdown("---")

    # ── AI Settings ──────────────────────────────────────────────────────────
    with st.expander("⚙️ Configurações de IA", expanded=False):
        _models = get_configured_models()
        st.markdown("**Modelos configurados**")
        st.markdown(
            f"🟢 **Simples:** `{_models['simple']}`  \n"
            f"🟡 **Médio:** `{_models['medium']}`  \n"
            f"🔴 **Complexo:** `{_models['complex']}`"
        )

        _last = get_last_model_used()
        if _last:
            st.markdown(f"**Último modelo usado:** `{_last}`")

        st.markdown("**Modo de IA**")
        _mode_labels = {
            "balanced": "⚖️ Balanceado — usa o modelo adequado para cada tarefa",
            "economic": "💰 Econômico — prefere modelos menores para reduzir custo",
            "quality": "🏆 Qualidade máxima — sempre usa o modelo mais forte",
        }
        _mode_key = st.radio(
            "Modo",
            options=list(_mode_labels.keys()),
            format_func=lambda k: _mode_labels[k],
            index=list(_mode_labels.keys()).index(
                st.session_state.get("ai_mode", "balanced")
            ),
            label_visibility="collapsed",
        )
        if _mode_key != st.session_state.get("ai_mode"):
            st.session_state.ai_mode = _mode_key

        _has_key = bool(os.getenv("OPENAI_API_KEY", "").strip())
        if _has_key:
            st.success("✅ OPENAI_API_KEY configurada", icon=None)
        else:
            st.warning(
                "⚠️ OPENAI_API_KEY não configurada.  \n"
                "Configure sua chave de API para gerar tutoriais de alta qualidade.",
                icon=None,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _docs_as_text() -> str:
    """Merge all uploaded doc texts into a single string."""
    return "\n\n---\n\n".join(
        f"### {d['name']}\n\n{d['text']}"
        for d in st.session_state.extracted_docs
    )


def _build_brainstorm_response(user_msg: str, bs: dict, turn: int) -> str:
    """
    Format the brainstorm_agent output dict into a friendly conversational
    reply, with turn-aware follow-up questions.
    """
    tech = bs.get("technology", "a tecnologia mencionada")
    audience = bs.get("target_audience", "desenvolvedores")
    level = bs.get("technical_level", "intermediário")
    objective = bs.get("objective", "")
    prereqs = bs.get("prerequisites", [])
    depth = bs.get("depth", "completo")
    outcome = bs.get("expected_outcome", "")
    errors = bs.get("common_errors", [])
    ex = bs.get("practical_examples", {})

    lines = []

    if turn == 1:
        lines.append(f"Ótimo! Entendi que você quer criar um tutorial sobre **{tech}**.")
        lines.append("")
        lines.append(
            "Para gerar um tutorial bem estruturado, me conte um pouco mais:"
        )
        lines.append("")
        lines.append("- **Qual é o público-alvo?** (ex: devs júnior, equipes de dados, sysadmins)")
        lines.append("- **Qual o nível técnico assumido?** (iniciante, intermediário ou avançado)")
        lines.append("- **Qual o principal objetivo** que o leitor deve atingir ao final?")

    elif turn == 2:
        lines.append(f"Perfeito! Público: **{audience}**, nível: **{level}**.")
        lines.append("")
        lines.append("Mais algumas perguntas para enriquecer o tutorial:")
        lines.append("")
        lines.append("- **Quais pré-requisitos** o leitor deve ter? (ex: Python 3.10+, Docker instalado)")
        lines.append("- **Com que profundidade** devo cobrir? (introdutório / completo / aprofundado com internals)")
        lines.append("- **Quais erros comuns** o tutorial deve cobrir para ajudar o leitor a não travar?")

    elif turn == 3:
        prereqs_str = ", ".join(prereqs) if prereqs else "a serem confirmados"
        errors_str = ", ".join(errors[:3]) if errors else "a serem identificados"
        lines.append("Excelente! Já tenho informações suficientes para gerar o tutorial.")
        lines.append("")
        lines.append("**Resumo do escopo levantado:**")
        lines.append("")
        lines.append(f"| Campo | Valor |")
        lines.append(f"|---|---|")
        lines.append(f"| Tecnologia | {tech} |")
        lines.append(f"| Público | {audience} |")
        lines.append(f"| Nível | {level} |")
        lines.append(f"| Profundidade | {depth} |")
        lines.append(f"| Pré-requisitos | {prereqs_str} |")
        lines.append(f"| Objetivo | {objective or '—'} |")
        lines.append(f"| Resultado esperado | {outcome or '—'} |")
        lines.append(f"| Erros comuns | {errors_str} |")
        lines.append(f"| Exemplos práticos | {ex.get('count', 3)} |")
        lines.append("")
        lines.append(
            "✅ Clique em **Gerar Tutorial Completo** quando estiver pronto, "
            "ou continue conversando para ajustar o escopo."
        )

    else:
        summary = bs.get("summary", "")
        lines.append("Informações atualizadas com base na sua mensagem.")
        if summary:
            lines.append("")
            lines.append(f"**Escopo atual:** {summary}")
        lines.append("")
        lines.append(
            "Pode clicar em **Gerar Tutorial Completo** quando quiser, "
            "ou continue refinando o escopo aqui."
        )

    return "\n".join(lines)


def _parse_fields_from_chat(technology: str) -> dict:
    """
    Extract structured brainstorm fields from the chat history
    plus the current brainstorm_data stored in session state.
    """
    bs = st.session_state.brainstorm_data or {}
    history = list_history()
    chat_history = [
        {"user_message": h.user_message, "agent_response": h.agent_response}
        for h in history
    ]
    context = get_context_block()

    # Merge technology from form into brainstorm if available
    if technology and not bs.get("technology"):
        bs["technology"] = technology

    return {
        "technology": bs.get("technology") or technology,
        "target_audience": bs.get("target_audience", "desenvolvedores"),
        "technical_level": bs.get("technical_level", "intermediário"),
        "objective": bs.get("objective", f"Aprender {technology} na prática"),
        "operating_environment": bs.get("operating_environment", "Linux / macOS / Windows"),
        "prerequisites": bs.get("prerequisites", []),
        "depth": bs.get("depth", "completo"),
        "practical_examples": bs.get("practical_examples", {"include": True, "count": 3, "description": ""}),
        "common_errors": bs.get("common_errors", []),
        "expected_outcome": bs.get("expected_outcome", f"Domínio prático de {technology}"),
        "chat_history": chat_history,
        "source_documents_text": _docs_as_text(),
        "requirements": context,
    }


# ===========================================================================
# Page: Criar Tutorial
# ===========================================================================
if page == "criar":
    st.header("📝 Criar Tutorial")

    # ── Reset button ──────────────────────────────────────────────────────
    if st.session_state.pipeline_done:
        if st.button("🔄 Criar novo tutorial", type="secondary"):
            st.session_state.pipeline_result = None
            st.session_state.final_content = ""
            st.session_state.pipeline_log = []
            st.session_state.pipeline_done = False
            st.session_state.brainstorm_data = {}
            st.session_state.extracted_docs = []
            st.session_state.save_success_id = None
            clear_memory()
            st.rerun()

    # ── 1. Metadados básicos ──────────────────────────────────────────────
    st.subheader("1. Informações básicas")

    col_meta1, col_meta2 = st.columns(2)
    with col_meta1:
        tutorial_title = st.text_input(
            "Nome do tutorial *",
            placeholder="Ex: Introdução ao Docker para Devs Python",
            key="input_title",
            disabled=st.session_state.pipeline_done,
        )
    with col_meta2:
        tutorial_technology = st.text_input(
            "Tecnologia / ferramenta *",
            placeholder="Ex: Docker",
            key="input_technology",
            disabled=st.session_state.pipeline_done,
        )

    tutorial_tags = st.text_input(
        "Tags (separadas por vírgula)",
        placeholder="Ex: containers, devops, linux",
        key="input_tags",
        disabled=st.session_state.pipeline_done,
    )

    # ── 2. Upload de documentos ───────────────────────────────────────────
    st.divider()
    st.subheader("2. Documentação de referência (opcional)")
    st.caption(
        f"Formatos aceitos: {', '.join(sorted(ACCEPTED_EXTENSIONS))}. "
        "O conteúdo será usado como contexto para gerar o tutorial."
    )

    if not st.session_state.pipeline_done:
        uploaded_files = st.file_uploader(
            "Selecione um ou mais arquivos",
            type=["txt", "md", "pdf"],
            accept_multiple_files=True,
            key="uploader",
        )

        if uploaded_files:
            new_names = {f.name for f in uploaded_files}
            existing_names = {d["name"] for d in st.session_state.extracted_docs}
            new_files = [f for f in uploaded_files if f.name not in existing_names]

            for uf in new_files:
                try:
                    result = process_uploaded_file(uf)
                    st.session_state.extracted_docs.append({
                        "name": result.filename,
                        "text": result.extracted_text,
                        "chars": result.char_count,
                        "pages": result.page_count,
                    })
                    st.success(f"✅ {result.summary()}")
                except (ValueError, RuntimeError) as exc:
                    st.error(f"❌ **{uf.name}**: {exc}")
                except Exception as exc:
                    st.error(f"❌ **{uf.name}**: Erro inesperado — {exc}")

            # Remove docs that were deselected
            st.session_state.extracted_docs = [
                d for d in st.session_state.extracted_docs if d["name"] in new_names
            ]

    if st.session_state.extracted_docs:
        for doc in st.session_state.extracted_docs:
            cols = st.columns([4, 1])
            with cols[0]:
                parts = [f"`{doc['name']}`", f"{doc['chars']:,} chars"]
                if doc.get("pages"):
                    parts.append(f"{doc['pages']} pág.")
                st.markdown("  ·  ".join(parts))
            with cols[1]:
                with st.expander("Prévia"):
                    preview = doc["text"][:600] + ("…" if len(doc["text"]) > 600 else "")
                    st.text(preview)

        total_chars = sum(d["chars"] for d in st.session_state.extracted_docs)
        col_dc1, col_dc2 = st.columns([4, 1])
        col_dc1.caption(
            f"Total: {len(st.session_state.extracted_docs)} arquivo(s) · {total_chars:,} caracteres"
        )
        if not st.session_state.pipeline_done:
            with col_dc2:
                if st.button("🗑️ Limpar docs", use_container_width=True):
                    st.session_state.extracted_docs = []
                    st.rerun()
    else:
        st.caption("Nenhum documento carregado ainda.")

    # ── 3. Chat de brainstorm ─────────────────────────────────────────────
    st.divider()
    st.subheader("3. Brainstorm com o agente")
    st.caption(
        "Converse para definir o escopo do tutorial. "
        f"O histórico mantém as últimas {MAX_INTERACTIONS} interações."
    )

    history = list_history()

    if not history:
        tech_hint = f" sobre **{tutorial_technology}**" if tutorial_technology.strip() else ""
        st.info(
            f"Olá! Vamos criar um tutorial{tech_hint}. "
            "Descreva o que você quer ensinar e para quem.",
            icon="💬",
        )
    else:
        for turn in history:
            with st.chat_message("user"):
                st.markdown(turn.user_message)
                ts = turn.formatted_timestamp()
                if ts:
                    st.caption(ts)
            with st.chat_message("assistant", avatar="🤖"):
                st.markdown(turn.agent_response)

    if not st.session_state.pipeline_done:
        user_input = st.chat_input(
            "Ex: Quero criar um tutorial sobre JWT em FastAPI para devs Python intermediários...",
            key="brainstorm_input",
        )

        if user_input:
            # Build state for brainstorm_agent
            tech = tutorial_technology.strip() or "a tecnologia"
            agent_state = {
                "technology": tech,
                "target_audience": "",
                "technical_level": "intermediário",
                "objective": f"Aprender {tech} na prática",
                "operating_environment": "Linux / macOS / Windows",
                "prerequisites": [],
                "depth": "completo",
                "practical_examples": {"include": True, "count": 3, "description": ""},
                "common_errors": [],
                "expected_outcome": f"Domínio prático de {tech}",
                "source_documents_text": _docs_as_text(),
                "chat_history": [
                    {"user_message": h.user_message, "agent_response": h.agent_response}
                    for h in history
                ],
                "errors": [],
            }

            # Merge existing brainstorm data if available
            if st.session_state.brainstorm_data:
                for k, v in st.session_state.brainstorm_data.items():
                    if v and not agent_state.get(k):
                        agent_state[k] = v

            # Try to extract technology from user message if not set
            if not tutorial_technology.strip():
                words = user_input.split()
                for i, w in enumerate(words):
                    if w.lower() in ("sobre", "de", "para", "em") and i + 1 < len(words):
                        candidate = words[i + 1].rstrip(".,;")
                        if len(candidate) > 2:
                            agent_state["technology"] = candidate
                            break

            try:
                updated = brainstorm_agent(agent_state)
                bs = updated.get("brainstorm", {})
                st.session_state.brainstorm_data = bs
            except Exception as exc:
                bs = {}
                logging.warning("brainstorm_agent failed in chat: %s", exc)

            turn_num = len(history) + 1
            agent_response = _build_brainstorm_response(user_input, bs, turn_num)

            try:
                add_interaction(user_message=user_input, agent_response=agent_response)
            except ValueError as exc:
                st.error(f"Erro ao registrar interação: {exc}")
            st.rerun()

    # History controls
    if history:
        col_h1, col_h2 = st.columns([3, 1])
        with col_h1:
            count = interaction_count()
            st.caption(
                f"{count}/{MAX_INTERACTIONS} interação(ões). "
                + ("Limite atingido — as mais antigas serão descartadas." if count >= MAX_INTERACTIONS else "")
            )
        if not st.session_state.pipeline_done:
            with col_h2:
                if st.button("🗑️ Limpar chat", use_container_width=True):
                    clear_memory()
                    st.session_state.brainstorm_data = {}
                    st.rerun()

    # ── 4. Gerar Tutorial ─────────────────────────────────────────────────
    st.divider()
    st.subheader("4. Gerar tutorial completo")

    if not st.session_state.pipeline_done:
        tech_ok = bool(tutorial_technology.strip())
        title_ok = bool(tutorial_title.strip())
        can_generate = tech_ok and title_ok

        if not title_ok or not tech_ok:
            val_col1, val_col2 = st.columns(2)
            with val_col1:
                if not title_ok:
                    st.warning("⚠️ Preencha o **Nome do tutorial** antes de gerar.")
            with val_col2:
                if not tech_ok:
                    st.warning("⚠️ Preencha a **Tecnologia / ferramenta** antes de gerar.")

        col_gen1, col_gen2 = st.columns([3, 1])
        with col_gen1:
            generate_btn = st.button(
                "🚀 Gerar Tutorial Completo",
                type="primary",
                use_container_width=True,
                disabled=not can_generate,
                help="" if can_generate else "Preencha o nome do tutorial e a tecnologia para continuar.",
            )
        with col_gen2:
            st.caption("O pipeline executa 6 agentes em sequência com até 2 ciclos de revisão.")

        if generate_btn and can_generate:
            tech = tutorial_technology.strip()
            fields = _parse_fields_from_chat(tech)
            fields["technology"] = tech

            # Use form title as tutorial title hint
            if tutorial_title.strip():
                fields["title"] = tutorial_title.strip()

            # Show live progress via st.status
            with st.status("⏳ Executando pipeline de geração...", expanded=True) as status_box:
                _STEPS = [
                    ("🧠", "Brainstorm", "Coletando e estruturando requisitos...",
                     "Requisitos estruturados e brainstorm validado."),
                    ("📋", "PRD", "Gerando PRD — documento de requisitos do produto...",
                     "PRD gerado com objetivo, escopo e critérios de sucesso."),
                    ("📐", "Spec", "Criando Spec técnica — detalhando as 14 seções...",
                     "Spec criada com seções, tempos e pontos de verificação."),
                    ("✍️", "Writer", "Escrevendo o tutorial completo em Markdown...",
                     "Tutorial escrito com exemplos e blocos de código."),
                    ("🔍", "Reviewer", "Revisando qualidade, completude e didática...",
                     "Revisão concluída — issues identificados."),
                    ("🛠️", "Fixer", "Aplicando correções e finalizando o conteúdo...",
                     "Correções aplicadas — tutorial pronto."),
                ]

                placeholders = []
                for icon, name, desc, _ in _STEPS:
                    ph = st.empty()
                    ph.markdown(f"🔄 **{name}** — {desc}")
                    placeholders.append((ph, icon, name))

                try:
                    fields["ai_mode"] = st.session_state.get("ai_mode", "balanced")
                    result = run_tutorial_graph(fields)

                    # Update step display with event log
                    log = result.get("messages", [])
                    for i, (ph, icon, name) in enumerate(placeholders):
                        matched = next(
                            (m for m in log if name.lower() in m.lower()),
                            None,
                        )
                        done_msg = _STEPS[i][3]
                        if matched:
                            ph.markdown(f"✅ **{name}** — {matched.split('] ', 1)[-1]}")
                        else:
                            ph.markdown(f"✅ **{name}** — {done_msg}")

                    st.session_state.pipeline_result = result
                    st.session_state.final_content = result.get("final") or result.get("draft") or ""
                    st.session_state.pipeline_log = log
                    st.session_state.pipeline_done = True
                    st.session_state.confirm_save = False

                    rev_count = result.get("revision_count", 0)
                    score = (result.get("review") or {}).get("overall_score", 0)
                    status_box.update(
                        label=f"✅ Tutorial gerado! Score de qualidade: {score}/10 · {rev_count} ciclo(s) de revisão",
                        state="complete",
                        expanded=False,
                    )
                    st.rerun()

                except Exception as exc:
                    status_box.update(label="❌ Erro na geração — verifique os logs abaixo", state="error")
                    st.error(
                        f"**Falha no pipeline de geração.**  \n"
                        f"Detalhes: `{exc}`  \n\n"
                        "Verifique se a `OPENAI_API_KEY` está configurada nas Configurações de IA (sidebar)."
                    )
                    logging.exception("Pipeline failed")

    else:
        # Show pipeline summary after completion
        result = st.session_state.pipeline_result or {}
        rev_count = result.get("revision_count", 0)
        rev_status = (result.get("review") or {}).get("status", "—")
        score = (result.get("review") or {}).get("overall_score", 0)
        final_len = len(st.session_state.final_content)

        col_r1, col_r2, col_r3, col_r4 = st.columns(4)
        col_r1.metric("Status", "✅ Completo")
        col_r2.metric("Score de revisão", f"{score}/10")
        col_r3.metric("Ciclos de revisão", rev_count)
        col_r4.metric("Caracteres", f"{final_len:,}")

        # Content quality alerts
        if final_len < 1000:
            st.warning(
                "⚠️ **Conteúdo gerado está muito curto** — menos de 1 000 caracteres.  \n"
                "Considere refinar o brainstorm e clicar em **Criar novo tutorial** para tentar novamente.",
                icon="⚠️",
            )
        elif score and score < 5:
            st.warning(
                f"⚠️ **Score de revisão baixo ({score}/10).** "
                "Revise o relatório de revisão abaixo e edite o conteúdo na aba Editor antes de salvar.",
                icon="⚠️",
            )

        # Event log collapsible
        log = st.session_state.pipeline_log
        if log:
            with st.expander("📋 Log do pipeline", expanded=False):
                for entry in log:
                    st.text(entry)

        # Review details collapsible
        review = result.get("review") or {}
        issues = review.get("issues", [])
        if review:
            with st.expander("🔍 Relatório de revisão", expanded=False):
                col_rv1, col_rv2 = st.columns(2)
                with col_rv1:
                    st.markdown("**Pontuações por critério:**")
                    scores = review.get("scores", {})
                    for criterion, val in scores.items():
                        bar = "█" * int(val) + "░" * (10 - int(val))
                        st.markdown(f"`{criterion}` {bar} **{val}/10**")
                with col_rv2:
                    positives = review.get("positive_aspects", [])
                    if positives:
                        st.markdown("**Pontos positivos:**")
                        for p in positives:
                            st.markdown(f"- {p}")
                if issues:
                    st.markdown("**Issues identificados:**")
                    for issue in issues:
                        sev = issue.get("severity", "minor")
                        icon = "🔴" if sev == "critical" else "🟡" if sev == "major" else "🟢"
                        st.markdown(
                            f"{icon} `{issue.get('id', '—')}` **{sev}** · "
                            f"{issue.get('section', '—')} — {issue.get('description', '')}"
                        )

    # ── 5. Preview e editor ───────────────────────────────────────────────
    if st.session_state.pipeline_done and st.session_state.final_content:
        st.divider()
        st.subheader("5. Tutorial gerado")

        tab_preview, tab_editor = st.tabs(["👁️ Preview", "✏️ Editor"])

        with tab_preview:
            result = st.session_state.pipeline_result or {}
            _exp_title = (
                result.get("title")
                or tutorial_title.strip()
                or tutorial_technology.strip()
                or "tutorial"
            )
            render_content_metrics(st.session_state.final_content)
            st.divider()
            with st.container(border=True):
                st.markdown(st.session_state.final_content)

            st.divider()
            st.markdown("#### 📥 Exportar tutorial")
            render_export_buttons(_exp_title, st.session_state.final_content, "criar")

        with tab_editor:
            st.caption(
                "Edite o conteúdo antes de salvar. "
                "Suporta Markdown completo — use o Preview para verificar a formatação."
            )
            edited = st.text_area(
                "Conteúdo do tutorial (Markdown)",
                value=st.session_state.final_content,
                height=500,
                key="editor_content",
                label_visibility="collapsed",
            )
            if edited != st.session_state.final_content:
                st.session_state.final_content = edited
                st.caption("✏️ Alterações detectadas — salve abaixo para persistir.")

    # ── 6. Salvar no banco ────────────────────────────────────────────────
    if st.session_state.pipeline_done:
        st.divider()
        st.subheader("6. Salvar no banco de dados")

        if st.session_state.save_success_id:
            st.success(
                f"✅ Tutorial salvo com sucesso! **ID: {st.session_state.save_success_id}**  "
                "Você pode encontrá-lo na aba **Pesquisar Tutoriais**."
            )
        else:
            result = st.session_state.pipeline_result or {}
            review = result.get("review") or {}
            prd_dict = result.get("prd") or {}
            spec_dict = result.get("spec") or {}

            # Auto-populate title if not filled
            auto_title = (
                tutorial_title.strip()
                or result.get("title", "")
                or f"Tutorial de {tutorial_technology.strip()}"
            )

            col_save1, col_save2 = st.columns(2)
            with col_save1:
                save_title = st.text_input(
                    "Título do tutorial *",
                    value=auto_title,
                    key="save_title",
                )
            with col_save2:
                save_tech = st.text_input(
                    "Tecnologia *",
                    value=tutorial_technology.strip() or (result.get("brainstorm") or {}).get("technology", ""),
                    key="save_tech",
                )

            save_tags = st.text_input(
                "Tags",
                value=tutorial_tags.strip(),
                key="save_tags",
            )

            st.caption(
                "Serão salvos: título, tecnologia, tags, PRD, Spec, "
                "notas de revisão, documentos de referência e o Markdown final."
            )

            # Confirmation gate
            fields_ready = bool(save_title.strip() and save_tech.strip())
            confirmed = st.checkbox(
                "✅ Confirmo que desejo salvar este tutorial no banco de dados",
                value=st.session_state.confirm_save,
                key="chk_confirm_save",
                disabled=not fields_ready,
            )
            st.session_state.confirm_save = confirmed

            save_btn = st.button(
                "💾 Salvar no Banco",
                type="primary",
                use_container_width=False,
                disabled=not (fields_ready and confirmed),
                help="Marque a confirmação acima para habilitar o salvamento." if not confirmed else "",
            )

            if save_btn and fields_ready and confirmed:
                with st.spinner("Salvando tutorial no banco de dados..."):
                    try:
                        new_id = save_tutorial(
                            title=sanitize_title(save_title),
                            technology=sanitize_technology(save_tech),
                            tags=sanitize_tags(save_tags),
                            requirements=result.get("requirements", ""),
                            prd=json.dumps(prd_dict, ensure_ascii=False) if prd_dict else "",
                            spec=json.dumps(spec_dict, ensure_ascii=False) if spec_dict else "",
                            draft_content=sanitize_markdown_content(result.get("draft", "")),
                            review_notes=json.dumps(review, ensure_ascii=False) if review else "",
                            final_content_md=sanitize_markdown_content(st.session_state.final_content),
                            source_documents_text=_docs_as_text(),
                        )
                        st.session_state.save_success_id = new_id
                        st.session_state.confirm_save = False
                        st.rerun()
                    except Exception as exc:
                        st.error(
                            f"❌ **Falha ao salvar no banco de dados.**  \n"
                            f"Detalhes: `{exc}`  \n\n"
                            "Verifique se o banco está acessível e tente novamente."
                        )
                        logging.exception("save_tutorial failed")


# ===========================================================================
# Page: Pesquisar Tutoriais
# ===========================================================================
elif page == "pesquisar":
    from services.database import update_tutorial

    st.header("🔍 Pesquisar Tutoriais")

    # ── Search helpers ────────────────────────────────────────────────────
    def _do_search(q, tech, tags_q, d_from, d_to) -> list[dict]:
        combined_query = " ".join(filter(None, [q.strip(), tags_q.strip()]))
        return search_tutorials(
            query=combined_query,
            technology=tech.strip(),
            date_from=d_from.isoformat() if d_from else "",
            date_to=d_to.isoformat() if d_to else "",
        )

    # Initialize search session state
    st.session_state.setdefault("search_results", None)   # None = not searched yet
    st.session_state.setdefault("search_ran", False)
    st.session_state.setdefault("edit_update_msg", None)  # success/error after save

    # ── Search form ───────────────────────────────────────────────────────
    with st.form("form_busca"):
        col_q1, col_q2, col_q3 = st.columns([3, 2, 2])
        with col_q1:
            f_query = st.text_input(
                "Título ou conteúdo",
                placeholder="Ex: Docker, API REST...",
            )
        with col_q2:
            f_technology = st.text_input(
                "Tecnologia",
                placeholder="Ex: Python",
            )
        with col_q3:
            f_tags = st.text_input(
                "Tags",
                placeholder="Ex: devops, jwt",
            )

        col_d1, col_d2, col_btn1, col_btn2 = st.columns([2, 2, 1, 1])
        with col_d1:
            f_date_from = st.date_input("De", value=None, key="search_date_from")
        with col_d2:
            f_date_to = st.date_input("Até", value=None, key="search_date_to")
        with col_btn1:
            st.markdown("<br>", unsafe_allow_html=True)
            search_btn = st.form_submit_button("🔎 Buscar", use_container_width=True, type="primary")
        with col_btn2:
            st.markdown("<br>", unsafe_allow_html=True)
            list_all_btn = st.form_submit_button("📋 Listar todos", use_container_width=True)

    if search_btn:
        try:
            st.session_state.search_results = _do_search(
                f_query, f_technology, f_tags, f_date_from, f_date_to
            )
            st.session_state.search_ran = True
            # Clear open tutorial when running a new search
            st.session_state.pop("viewing_id", None)
            st.session_state.edit_update_msg = None
        except Exception as exc:
            st.error(f"Erro ao buscar: {exc}")

    if list_all_btn:
        try:
            st.session_state.search_results = search_tutorials()
            st.session_state.search_ran = True
            st.session_state.pop("viewing_id", None)
            st.session_state.edit_update_msg = None
        except Exception as exc:
            st.error(f"Erro ao listar: {exc}")

    results: list[dict] = st.session_state.search_results or []

    st.divider()

    # =========================================================================
    # DETAIL VIEW — a tutorial is open
    # =========================================================================
    if "viewing_id" in st.session_state:
        tut = safe_load_tutorial(st.session_state.viewing_id)
        if not tut:
            del st.session_state.viewing_id
        else:
            # ── Header ────────────────────────────────────────────────────
            col_back, col_hdr = st.columns([1, 6])
            with col_back:
                if st.button("← Voltar", use_container_width=True):
                    del st.session_state.viewing_id
                    st.session_state.edit_update_msg = None
                    st.rerun()
            with col_hdr:
                st.markdown(f"### {tut['title']}")

            # ── Metadata strip ────────────────────────────────────────────
            col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
            col_m1.metric("ID", f"#{tut['id']}")
            col_m2.metric("Tecnologia", tut.get("technology") or "—")
            col_m3.metric("Tags", tut.get("tags") or "—")
            col_m4.metric("Criado em", format_display_date(tut["created_at"]))
            col_m5.metric("Atualizado", format_display_date(tut.get("updated_at", tut["created_at"])))

            # ── Update feedback ───────────────────────────────────────────
            if st.session_state.edit_update_msg:
                kind, msg = st.session_state.edit_update_msg
                if kind == "success":
                    st.success(msg)
                else:
                    st.error(msg)

            # ── Tabs ──────────────────────────────────────────────────────
            tab_preview, tab_edit_content, tab_edit_meta, tab_spec, tab_prd, tab_review = st.tabs([
                "👁️ Preview",
                "✏️ Editar conteúdo",
                "🏷️ Editar metadados",
                "📐 Spec",
                "📋 PRD",
                "🔍 Revisão",
            ])

            # ── Tab: Preview ──────────────────────────────────────────────
            with tab_preview:
                content_md = tut.get("final_content_md") or tut.get("draft_content") or ""
                if content_md:
                    _ptitle = tut.get("title") or "tutorial"
                    render_content_metrics(content_md)
                    st.divider()
                    with st.container(border=True):
                        st.markdown(content_md)

                    st.divider()
                    st.markdown("#### 📥 Exportar tutorial")
                    render_export_buttons(_ptitle, content_md, f"pesq_{tut['id']}")
                else:
                    st.info("Este tutorial não possui conteúdo ainda.")

            # ── Tab: Editar conteúdo ──────────────────────────────────────
            with tab_edit_content:
                st.caption(
                    "Edite o Markdown diretamente. O Preview é atualizado após salvar."
                )
                content_key = f"edit_content_{tut['id']}"
                current_content = tut.get("final_content_md") or tut.get("draft_content") or ""

                edited_md = st.text_area(
                    "Conteúdo (Markdown)",
                    value=current_content,
                    height=520,
                    key=content_key,
                    label_visibility="collapsed",
                )

                col_ec1, col_ec2, col_ec3 = st.columns([2, 2, 3])
                with col_ec1:
                    save_content_btn = st.button(
                        "💾 Salvar conteúdo",
                        key=f"save_content_{tut['id']}",
                        type="primary",
                        use_container_width=True,
                    )
                with col_ec2:
                    if edited_md != current_content:
                        delta = len(edited_md) - len(current_content)
                        sign = "+" if delta >= 0 else ""
                        st.caption(f"✏️ {sign}{delta} caracteres alterados")
                with col_ec3:
                    st.caption("Salvar substitui o conteúdo final no banco de dados.")

                if save_content_btn:
                    if not edited_md.strip():
                        st.warning("O conteúdo não pode estar vazio.")
                    else:
                        try:
                            ok = update_tutorial(
                                tut["id"],
                                final_content_md=edited_md,
                            )
                            if ok:
                                st.session_state.edit_update_msg = (
                                    "success",
                                    f"✅ Conteúdo do tutorial **#{tut['id']}** atualizado com sucesso.",
                                )
                            else:
                                st.session_state.edit_update_msg = (
                                    "error",
                                    "Nenhuma linha foi atualizada — verifique o ID.",
                                )
                            st.rerun()
                        except Exception as exc:
                            st.session_state.edit_update_msg = ("error", f"Erro ao salvar: {exc}")
                            st.rerun()

            # ── Tab: Editar metadados ─────────────────────────────────────
            with tab_edit_meta:
                st.caption("Atualize título, tecnologia e tags sem reprocessar o conteúdo.")

                with st.form(f"form_edit_meta_{tut['id']}"):
                    col_em1, col_em2 = st.columns(2)
                    with col_em1:
                        new_title = st.text_input(
                            "Título *",
                            value=tut.get("title", ""),
                        )
                    with col_em2:
                        new_tech = st.text_input(
                            "Tecnologia *",
                            value=tut.get("technology", ""),
                        )
                    new_tags = st.text_input(
                        "Tags (separadas por vírgula)",
                        value=tut.get("tags", ""),
                    )
                    new_requirements = st.text_area(
                        "Requisitos / contexto",
                        value=tut.get("requirements", ""),
                        height=80,
                    )
                    save_meta_btn = st.form_submit_button(
                        "💾 Salvar metadados",
                        type="primary",
                        use_container_width=True,
                    )

                if save_meta_btn:
                    if not new_title.strip() or not new_tech.strip():
                        st.warning("Título e tecnologia são obrigatórios.")
                    else:
                        try:
                            ok = update_tutorial(
                                tut["id"],
                                title=sanitize_title(new_title),
                                technology=sanitize_technology(new_tech),
                                tags=sanitize_tags(new_tags),
                                requirements=new_requirements.strip(),
                            )
                            if ok:
                                st.session_state.edit_update_msg = (
                                    "success",
                                    f"✅ Metadados do tutorial **#{tut['id']}** atualizados.",
                                )
                            else:
                                st.session_state.edit_update_msg = (
                                    "error", "Nenhuma linha atualizada."
                                )
                            st.rerun()
                        except Exception as exc:
                            st.session_state.edit_update_msg = ("error", f"Erro: {exc}")
                            st.rerun()

            # ── Tab: Spec ─────────────────────────────────────────────────
            with tab_spec:
                raw_spec = tut.get("spec", "")
                if raw_spec:
                    try:
                        spec_data = json.loads(raw_spec)
                        sections = spec_data.get("sections", [])
                        if sections:
                            total_min = spec_data.get("total_estimated_minutes", 0)
                            st.caption(
                                f"{len(sections)} seções · {total_min} min estimados · "
                                f"{spec_data.get('didactic_order_rationale', '')}"
                            )
                            st.divider()

                            for sec in sections:
                                with st.container(border=True):
                                    col_s1, col_s2 = st.columns([5, 1])
                                    with col_s1:
                                        type_badge = {
                                            "introduction": "🟢 Introdução",
                                            "concept": "🔵 Conceito",
                                            "installation": "🟠 Instalação",
                                            "configuration": "🟡 Configuração",
                                            "example": "🟣 Exemplo",
                                            "best_practices": "⚪ Boas Práticas",
                                            "troubleshooting": "🔴 Troubleshooting",
                                            "conclusion": "✅ Conclusão",
                                        }.get(sec.get("type", ""), sec.get("type", ""))

                                        st.markdown(
                                            f"**{sec['order']}. {sec['title']}** — {type_badge}"
                                        )
                                        if sec.get("objective"):
                                            st.caption(sec["objective"])
                                        outline = sec.get("content_outline", [])
                                        if outline:
                                            for item in outline:
                                                st.markdown(f"  - {item}")
                                    with col_s2:
                                        st.caption(f"⏱️ {sec.get('estimated_minutes', '?')} min")
                                        if sec.get("checkpoint"):
                                            st.caption(f"✅ {sec['checkpoint'][:60]}…")
                        else:
                            st.json(spec_data)
                    except json.JSONDecodeError:
                        st.text(raw_spec)
                else:
                    st.info("Spec não disponível para este tutorial.")

            # ── Tab: PRD ──────────────────────────────────────────────────
            with tab_prd:
                raw_prd = tut.get("prd", "")
                if raw_prd:
                    try:
                        prd_data = json.loads(raw_prd)
                        # Render PRD fields as readable sections
                        st.markdown(f"**{prd_data.get('title', '')}**")
                        st.caption(prd_data.get("description", ""))
                        st.divider()

                        col_prd1, col_prd2 = st.columns(2)
                        with col_prd1:
                            st.markdown("**Objetivo**")
                            st.write(prd_data.get("objective", "—"))
                            st.markdown("**Usuários**")
                            users = prd_data.get("users", {})
                            st.write(f"Primário: {users.get('primary', '—')}")
                            st.write(f"Secundário: {users.get('secondary', '—')}")
                            st.markdown("**Critérios de sucesso**")
                            for c in prd_data.get("success_criteria", []):
                                st.markdown(f"- {c}")
                        with col_prd2:
                            st.markdown("**Escopo — Inclui**")
                            for s in prd_data.get("scope", {}).get("in_scope", []):
                                st.markdown(f"- ✅ {s}")
                            st.markdown("**Escopo — Exclui**")
                            for s in prd_data.get("scope", {}).get("out_of_scope", []):
                                st.markdown(f"- ❌ {s}")

                        feats = prd_data.get("features", [])
                        if feats:
                            st.markdown("**Funcionalidades**")
                            feat_rows = {
                                "ID": [f["id"] for f in feats],
                                "Nome": [f["name"] for f in feats],
                                "Descrição": [f["description"] for f in feats],
                                "Prioridade": [f["priority"] for f in feats],
                            }
                            st.dataframe(feat_rows, use_container_width=True, hide_index=True)

                        risks = prd_data.get("risks", [])
                        if risks:
                            st.markdown("**Riscos**")
                            for r in risks:
                                st.markdown(f"- ⚠️ **{r.get('risk', '')}** → {r.get('mitigation', '')}")

                        reading_time_prd = prd_data.get("estimated_reading_time_minutes")
                        if reading_time_prd:
                            st.caption(f"Tempo de leitura estimado: {reading_time_prd} min")

                    except json.JSONDecodeError:
                        st.text(raw_prd)
                else:
                    st.info("PRD não disponível para este tutorial.")

            # ── Tab: Revisão ──────────────────────────────────────────────
            with tab_review:
                raw_review = tut.get("review_notes", "")
                if raw_review:
                    try:
                        rv = json.loads(raw_review)
                        score = rv.get("overall_score", 0)
                        rv_status = rv.get("status", "—")

                        col_rv1, col_rv2, col_rv3 = st.columns(3)
                        col_rv1.metric("Score geral", f"{score}/10")
                        col_rv2.metric("Status", rv_status)
                        col_rv3.metric("Issues", len(rv.get("issues", [])))

                        st.caption(rv.get("summary", ""))
                        st.divider()

                        col_rv_a, col_rv_b = st.columns(2)
                        with col_rv_a:
                            scores_map = rv.get("scores", {})
                            if scores_map:
                                st.markdown("**Pontuações por critério**")
                                score_rows = {
                                    "Critério": list(scores_map.keys()),
                                    "Score": list(scores_map.values()),
                                    "Barra": [
                                        "█" * int(v) + "░" * (10 - int(v))
                                        for v in scores_map.values()
                                    ],
                                }
                                st.dataframe(score_rows, use_container_width=True, hide_index=True)

                        with col_rv_b:
                            positives = rv.get("positive_aspects", [])
                            if positives:
                                st.markdown("**Pontos positivos**")
                                for p in positives:
                                    st.markdown(f"- ✅ {p}")

                        issues = rv.get("issues", [])
                        if issues:
                            st.markdown("**Issues identificados**")
                            issue_rows = {
                                "ID": [i.get("id", "—") for i in issues],
                                "Severidade": [i.get("severity", "—") for i in issues],
                                "Seção": [i.get("section", "—") for i in issues],
                                "Descrição": [i.get("description", "") for i in issues],
                                "Correção": [i.get("fix_instruction", "") for i in issues],
                            }
                            st.dataframe(issue_rows, use_container_width=True, hide_index=True)

                    except json.JSONDecodeError:
                        st.text(raw_review)
                else:
                    st.info("Notas de revisão não disponíveis.")

            # ── Source docs ───────────────────────────────────────────────
            if tut.get("source_documents_text"):
                with st.expander("📎 Documentos de referência utilizados"):
                    preview_src = tut["source_documents_text"][:2500]
                    truncated = len(tut["source_documents_text"]) > 2500
                    st.text(preview_src + ("\n\n[...] (truncado)" if truncated else ""))

    # =========================================================================
    # LIST VIEW — no tutorial open
    # =========================================================================
    else:
        if not st.session_state.search_ran:
            # Auto-load all on first visit
            try:
                st.session_state.search_results = search_tutorials()
                st.session_state.search_ran = True
                results = st.session_state.search_results
            except Exception as exc:
                st.error(f"Erro ao carregar tutoriais: {exc}")
                results = []

        if not results:
            if st.session_state.search_ran:
                st.info(
                    "Nenhum tutorial encontrado com os filtros aplicados. "
                    "Ajuste a busca ou crie um tutorial na aba **Criar Tutorial**.",
                    icon="🔍",
                )
            else:
                st.info("Use os campos acima para buscar ou clique em **Listar todos**.", icon="💡")
        else:
            st.caption(f"**{len(results)}** tutorial(is) encontrado(s)")

            # ── Results table ─────────────────────────────────────────────
            table_rows = []
            for t in results:
                content = t.get("final_content_md") or t.get("draft_content") or ""
                table_rows.append({
                    "ID": t["id"],
                    "Título": t["title"],
                    "Tecnologia": t.get("technology") or "—",
                    "Tags": t.get("tags") or "—",
                    "Criado em": format_display_date(t["created_at"]),
                    "Atualizado": format_display_date(t.get("updated_at", t["created_at"])),
                    "Leitura": f"{estimate_reading_time(content)} min" if content else "—",
                    "Chars": f"{len(content):,}" if content else "0",
                    "PRD": "✅" if t.get("prd") else "—",
                    "Spec": "✅" if t.get("spec") else "—",
                })

            import pandas as pd
            df = pd.DataFrame(table_rows)

            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "ID": st.column_config.NumberColumn("ID", width="small"),
                    "Título": st.column_config.TextColumn("Título", width="large"),
                    "Tecnologia": st.column_config.TextColumn("Tecnologia", width="medium"),
                    "Tags": st.column_config.TextColumn("Tags", width="medium"),
                    "Criado em": st.column_config.TextColumn("Criado em", width="medium"),
                    "Atualizado": st.column_config.TextColumn("Atualizado", width="medium"),
                    "Leitura": st.column_config.TextColumn("Leitura", width="small"),
                    "Chars": st.column_config.TextColumn("Chars", width="small"),
                    "PRD": st.column_config.TextColumn("PRD", width="small"),
                    "Spec": st.column_config.TextColumn("Spec", width="small"),
                },
            )

            st.divider()
            st.markdown("**Abrir tutorial:**")

            # ── Open buttons grid ─────────────────────────────────────────
            COLS_PER_ROW = 3
            chunks = [
                results[i : i + COLS_PER_ROW]
                for i in range(0, len(results), COLS_PER_ROW)
            ]
            for chunk in chunks:
                cols = st.columns(COLS_PER_ROW)
                for col, tut in zip(cols, chunk):
                    with col:
                        content_len = len(
                            tut.get("final_content_md") or tut.get("draft_content") or ""
                        )
                        label = (
                            f"#{tut['id']} · {tut['title'][:30]}"
                            + ("…" if len(tut["title"]) > 30 else "")
                        )
                        sub = f"🔧 {tut.get('technology') or '—'} · {content_len:,} chars"
                        if st.button(
                            label,
                            key=f"open_{tut['id']}",
                            use_container_width=True,
                            help=sub,
                        ):
                            st.session_state.viewing_id = tut["id"]
                            st.session_state.edit_update_msg = None
                            st.rerun()


# ===========================================================================
# Page: Conversar com Tutorial
# ===========================================================================
elif page == "conversar":
    st.header("💬 Conversar com Tutorial")
    st.caption("Faça perguntas, peça exemplos, exercícios ou explicações sobre qualquer tutorial salvo.")

    # ── Tutorial selector ─────────────────────────────────────────────────
    all_tutorials: list[dict] = []
    try:
        all_tutorials = list_tutorials()
    except Exception as exc:
        st.error(f"Erro ao carregar tutoriais: {exc}")

    if not all_tutorials:
        st.info(
            "Nenhum tutorial salvo ainda. "
            "Crie e salve um tutorial na página **📝 Criar Tutorial** primeiro."
        )
        st.stop()

    tut_options = {
        t["id"]: f"[#{t['id']}] {t['title']} — {t['technology']}"
        for t in all_tutorials
    }

    selected_id: int = st.selectbox(
        "Selecione o tutorial",
        options=list(tut_options.keys()),
        format_func=lambda x: tut_options[x],
        key="tutor_selector",
    )

    # When tutorial changes, reload data and chat history from DB
    if selected_id != st.session_state.get("tutor_tutorial_id"):
        st.session_state.tutor_tutorial_id = selected_id
        try:
            st.session_state.tutor_tutorial = get_tutorial_by_id(selected_id)
            history_rows = get_chat_history(selected_id)
            st.session_state.tutor_chat = [
                {"role": r["role"], "message": r["message"]}
                for r in history_rows
            ]
        except Exception as exc:
            st.error(f"Erro ao carregar tutorial: {exc}")
            st.stop()

    tutorial = st.session_state.tutor_tutorial
    if tutorial is None:
        try:
            tutorial = get_tutorial_by_id(selected_id)
            st.session_state.tutor_tutorial = tutorial
        except Exception as exc:
            st.error(f"Tutorial não encontrado: {exc}")
            st.stop()

    # ── Tutorial summary card ─────────────────────────────────────────────
    with st.expander("📖 Resumo do tutorial selecionado", expanded=False):
        col_t1, col_t2, col_t3 = st.columns(3)
        col_t1.metric("Título", tutorial.get("title", "—")[:40])
        col_t2.metric("Tecnologia", tutorial.get("technology", "—"))
        col_t3.metric("Tags", tutorial.get("tags", "—")[:30] or "—")

        created = tutorial.get("created_at", "")[:10]
        updated = tutorial.get("updated_at", "")[:10]
        st.caption(f"Criado: {created} · Atualizado: {updated}")

        content_preview = tutorial.get("final_content_md", "")
        if content_preview:
            st.markdown(truncate_preview(content_preview, max_chars=600))
        else:
            st.info("Este tutorial não tem conteúdo final ainda.")

    st.markdown("---")

    # ── User level selector ────────────────────────────────────────────────
    col_lv, col_clear = st.columns([3, 1])
    with col_lv:
        user_level = st.selectbox(
            "Nível do usuário",
            options=["iniciante", "intermediário", "avançado"],
            index=1,
            key="tutor_user_level",
            label_visibility="visible",
        )
    with col_clear:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("🗑️ Limpar chat", use_container_width=True):
            try:
                clear_chat_history(selected_id)
            except Exception as exc:
                st.error(f"Erro ao limpar chat: {exc}")
            st.session_state.tutor_chat = []
            st.rerun()

    # ── Chat history display ──────────────────────────────────────────────
    chat_msgs: list[dict] = st.session_state.get("tutor_chat", [])

    if not chat_msgs:
        st.info(
            "Nenhuma conversa ainda. Comece fazendo uma pergunta sobre o tutorial!  \n\n"
            "**Exemplos:**  \n"
            "- *Explique a seção de configuração de forma mais simples*  \n"
            "- *Me dê outro exemplo prático*  \n"
            "- *Crie um exercício sobre instalação*  \n"
            "- *Como adapto os comandos para Windows?*  \n"
            "- *Qual erro comum pode acontecer aqui?*"
        )

    for msg in chat_msgs:
        with st.chat_message(msg["role"]):
            st.markdown(msg["message"])

    # ── Chat input ────────────────────────────────────────────────────────
    question = st.chat_input(
        f"Faça uma pergunta sobre '{tutorial.get('title', 'o tutorial')}'...",
        key="tutor_input",
    )

    if question:
        question = sanitize_question(question)
        if not question:
            st.warning("⚠️ Pergunta inválida ou vazia — tente novamente.")
            st.stop()

        # Display user message immediately
        with st.chat_message("user"):
            st.markdown(question)

        # Save user message to DB
        try:
            add_chat_message(selected_id, "user", question)
        except Exception as exc:
            st.warning(f"Não foi possível salvar mensagem no banco: {exc}")

        # Build agent state
        tutor_state = {
            "question": question,
            "tutorial_content": tutorial.get("final_content_md", ""),
            "prd": tutorial.get("prd", ""),
            "spec": tutorial.get("spec", ""),
            "source_documents_text": tutorial.get("source_documents_text", ""),
            "chat_history": chat_msgs[-10:],
            "user_level": user_level,
            "ai_mode": st.session_state.get("ai_mode", "balanced"),
        }

        # Get and stream response
        with st.chat_message("assistant"):
            with st.spinner("Pensando..."):
                try:
                    result = tutorial_tutor_agent(tutor_state)
                    answer = result.get("answer", "Desculpe, não consegui gerar uma resposta.")
                    complexity = result.get("tutor_complexity", "—")
                except Exception as exc:
                    answer = f"Erro ao chamar o agente tutor: {exc}"
                    complexity = "—"
                    logging.exception("tutorial_tutor_agent failed")

            st.markdown(answer)
            st.caption(f"Complexidade da pergunta: `{complexity}`")

        # Save assistant response to DB
        try:
            add_chat_message(selected_id, "assistant", answer)
        except Exception as exc:
            st.warning(f"Não foi possível salvar resposta no banco: {exc}")

        # Update session state
        st.session_state.tutor_chat = chat_msgs + [
            {"role": "user", "message": question},
            {"role": "assistant", "message": answer},
        ]


# ===========================================================================
# Page: Sobre
# ===========================================================================
elif page == "sobre":
    st.header("ℹ️ Sobre o TutorialGen")
    st.markdown(
        """
        ### O que é o TutorialGen?

        **TutorialGen** é uma ferramenta para **criação e armazenamento de tutoriais técnicos**,
        potencializada por 6 agentes de IA orquestrados com **LangGraph**.

        ### Tecnologias utilizadas

        | Camada | Tecnologia |
        |---|---|
        | Interface | Streamlit |
        | Banco de dados | SQLite |
        | Orquestração de agentes | LangGraph |
        | Formato de conteúdo | Markdown |
        | Linguagem | Python 3.11+ |
        | LLM (opcional) | OpenAI GPT-4o via `OPENAI_API_KEY` |

        ### Pipeline de agentes

        | # | Agente | Responsabilidade |
        |---|---|---|
        | 1 | Brainstorm Agent | Estrutura escopo e requisitos do chat |
        | 2 | PRD Agent | Cria o documento de requisitos do produto |
        | 3 | Spec Agent | Detalha as 14 seções do tutorial |
        | 4 | Writer Agent | Escreve o tutorial completo em Markdown |
        | 5 | Reviewer Agent | Avalia qualidade com scores por critério |
        | 6 | Fixer Agent | Aplica correções e gera changelog de revisão |

        ### Fluxo LangGraph

        ```
        Brainstorm → PRD → Spec → Writer → Reviewer → Fixer
                                              ↑            │
                                              └────────────┘
                                           (máx. 2 ciclos de revisão)
        ```

        ### Campos salvos no banco

        | Campo | Descrição |
        |---|---|
        | `title` | Título do tutorial |
        | `technology` | Tecnologia principal |
        | `tags` | Tags para busca |
        | `prd` | PRD em JSON |
        | `spec` | Spec completa em JSON (14 seções) |
        | `draft_content` | Rascunho inicial do Writer |
        | `review_notes` | Relatório do Reviewer em JSON |
        | `final_content_md` | Tutorial final editado |
        | `source_documents_text` | Texto dos docs enviados |

        ### Exportação disponível

        | Formato | Extensão | Como usar |
        |---|---|---|
        | Markdown | `.md` | Botão ⬇️ na aba Preview |
        | PDF | `.pdf` | Botão ⬇️ na aba Preview |
        | Word | `.docx` | Botão ⬇️ na aba Preview |

        > Os arquivos exportados são salvos em `/exports` e disponibilizados para download direto.

        ### Status do projeto

        > ✅ **Etapa 1** — Estrutura inicial e scaffold Streamlit
        > ✅ **Etapa 2** — Banco de dados SQLite (13 campos, 3 índices)
        > ✅ **Etapa 3** — Upload e extração de TXT, MD e PDF
        > ✅ **Etapa 4** — Memória conversacional (sliding window 10 turnos)
        > ✅ **Etapa 5** — Prompts dos 6 agentes em `/prompts`
        > ✅ **Etapa 6** — Implementação dos 6 agentes com LLM Router inteligente
        > ✅ **Etapa 7** — Orquestração LangGraph com loop reviewer→fixer (máx. 2 ciclos)
        > ✅ **Etapa 8** — Interface completa de criação, progresso ao vivo e salvamento
        > ✅ **Etapa 9** — Pesquisa avançada com 5 filtros, tabela e edição inline
        > ✅ **Etapa 10** — Exportação para MD, PDF (ReportLab) e DOCX (python-docx)
        > ✅ **Etapa 11** — Revisão final, correção de bugs e 15/15 testes validados
        > ✅ **Etapa 12** — Roteador inteligente de LLM (simple/medium/complex), modos econômico/qualidade, `.env.example`
        > ✅ **Etapa 13** — Tutorial Tutor Agent conversacional (chat com tutorial, histórico persistente no banco)
        > ✅ **Etapa 14** — Melhoria de UX: validações, confirmação de salvamento, alertas de qualidade, CSS polish, mensagens de status granulares
        > ✅ **Etapa 15** — Hardening: sanitização de inputs, helpers reutilizáveis (`ui_helpers`, `input_sanitizer`), eliminação de código duplicado, README.md
        """
    )
