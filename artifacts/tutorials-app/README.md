# TutorialGen

**TutorialGen** é uma aplicação Python/Streamlit para criar, armazenar e interagir com tutoriais técnicos gerados por IA, orquestrada por 7 agentes LangGraph.

---

## Índice

1. [Descrição do projeto](#descrição-do-projeto)
2. [Como rodar no Replit](#como-rodar-no-replit)
3. [Estrutura de pastas](#estrutura-de-pastas)
4. [Fluxo LangGraph](#fluxo-langgraph)
5. [Como usar cada funcionalidade](#como-usar-cada-funcionalidade)
6. [Variáveis de ambiente](#variáveis-de-ambiente)
7. [Roadmap técnico](#roadmap-técnico)

---

## Descrição do projeto

TutorialGen transforma um tema e algumas perguntas em um tutorial técnico completo em Markdown, com:

- **6 agentes em sequência**: Brainstorm → PRD → Spec → Writer → Reviewer → Fixer
- **Ciclos de revisão automática**: até 2 rodadas de correção com feedback estruturado
- **Roteamento inteligente de LLM**: usa o modelo correto (simples/médio/complexo) por tarefa
- **Agente tutor conversacional**: faça perguntas sobre qualquer tutorial salvo
- **Exportação**: Markdown, PDF (ReportLab) e Word (.docx)
- **Persistência**: SQLite com FTS-like search

---

## Como rodar no Replit

1. **Fork / abra o Repl** no Replit.
2. Adicione o secret `OPENAI_API_KEY` com sua chave da OpenAI (Settings → Secrets).
3. Clique em **Run** — o workflow `Start application` executa automaticamente:
   ```
   cd artifacts/tutorials-app && streamlit run app.py --server.port 5000
   ```
4. Acesse o app no painel de preview integrado.

> **Modelos opcionais**: copie `.env.example` para `.env` e ajuste `LLM_SIMPLE_MODEL`,
> `LLM_MEDIUM_MODEL` e `LLM_COMPLEX_MODEL` conforme sua conta.

---

## Estrutura de pastas

```
artifacts/tutorials-app/
├── app.py                      # UI principal Streamlit (roteamento de páginas)
├── requirements.txt            # Dependências Python
├── .env.example                # Template de variáveis de ambiente
│
├── database/
│   └── tutorials.db            # SQLite (criado automaticamente)
│
├── exports/                    # Arquivos exportados (.md, .pdf, .docx)
├── uploads/                    # Arquivos enviados pelo usuário
│
├── prompts/                    # Prompts Markdown de cada agente
│   ├── brainstorm_agent.md
│   ├── prd_agent.md
│   ├── spec_agent.md
│   ├── writer_agent.md
│   ├── reviewer_agent.md
│   ├── fixer_agent.md
│   └── tutorial_tutor_agent.md
│
├── services/
│   ├── agents.py               # 7 funções de agente (brainstorm → tutor)
│   ├── database.py             # CRUD SQLite (tutorials + tutorial_chats)
│   ├── exporters.py            # Exportação MD / DOCX / PDF
│   ├── file_loader.py          # Upload e extração de texto (TXT/MD/PDF)
│   ├── langgraph_flow.py       # Orquestração LangGraph do pipeline
│   ├── llm_router.py           # Roteamento inteligente de modelo LLM
│   └── memory.py               # Memória de sessão do brainstorm
│
└── utils/
    ├── date_utils.py           # Formatação e tempo relativo de datas
    ├── input_sanitizer.py      # Sanitização e validação de entradas
    ├── markdown_utils.py       # Extração, contagem e estimativa de leitura
    └── ui_helpers.py           # Componentes Streamlit reutilizáveis
```

---

## Fluxo LangGraph

```
Usuário (chat brainstorm)
        │
        ▼
┌───────────────┐
│  Brainstorm   │  Estrutura requisitos, público, profundidade
└──────┬────────┘
       │
       ▼
┌───────────────┐
│     PRD       │  Gera documento de requisitos do produto (JSON)
└──────┬────────┘
       │
       ▼
┌───────────────┐
│     Spec      │  Detalha 14 seções com tempos e checkpoints (JSON)
└──────┬────────┘
       │
       ▼
┌───────────────┐
│    Writer     │  Escreve o tutorial completo em Markdown
└──────┬────────┘
       │
       ▼
┌───────────────┐    score < 7 ou issues críticos
│   Reviewer    │─────────────────────────────────────────┐
└──────┬────────┘                                         │
       │ aprovado ou 2 ciclos atingidos                   ▼
       │                                         ┌────────────────┐
       │                                         │    Fixer       │
       │◄────────────────────────────────────────┤  (corrige e    │
       │                                         │   re-revisa)   │
       ▼                                         └────────────────┘
┌───────────────┐
│  Resultado    │  final_content_md + review + prd + spec
└───────────────┘
```

O grafo é definido em `services/langgraph_flow.py` usando `StateGraph` do LangGraph.
O estado transitório (`AgentState`) carrega o conteúdo em draft e os metadados entre nós.

### Roteamento de LLM

Cada tarefa tem uma complexidade atribuída em `llm_router.py`:

| Complexidade | Tarefas                           | Modelo padrão |
|--------------|-----------------------------------|---------------|
| `simple`     | brainstorm, tags, sumário, tutor  | MiniMax-2.5   |
| `medium`     | PRD, Spec, revisor básico         | gpt-5.4-nano  |
| `complex`    | Writer, Fixer, revisor crítico    | gpt-5.4-mini  |

Os modelos são configuráveis via variáveis de ambiente.  
O modo de IA (sidebar) permite forçar econômico ou qualidade máxima.

---

## Como usar cada funcionalidade

### 📝 Criar Tutorial

1. Preencha **Nome do tutorial** e **Tecnologia** (obrigatórios).
2. (Opcional) Faça upload de documentos de referência (TXT, MD, PDF).
3. Converse com o **Agente de Brainstorm** para definir público, nível e profundidade.
4. Clique em **Gerar Tutorial Completo** — o pipeline de 6 agentes executa.
5. Revise o resultado na aba **Preview** ou edite diretamente na aba **Editor**.
6. Confirme e clique em **Salvar no Banco**.

### 🔍 Pesquisar Tutoriais

- Use o formulário com filtros de texto, tecnologia, tags e datas.
- Clique em um tutorial para abrir o modo de detalhe com 6 abas:
  - **Preview** — renderização Markdown com botões de export
  - **Editar conteúdo** — edição inline do Markdown
  - **Editar metadados** — título, tecnologia, tags
  - **Spec** — visualização estruturada das 14 seções
  - **PRD** — documento de requisitos do produto
  - **Revisão** — score, critérios e issues identificados

### 💬 Conversar com Tutorial

1. Selecione um tutorial salvo no dropdown.
2. Escolha seu nível (iniciante / intermediário / avançado).
3. Digite sua pergunta no chat — o Tutor Agent responde em contexto.
4. O histórico é persistido no banco e recarregado a cada visita.
5. Use **Limpar chat** para reiniciar a conversa.

**Exemplos de perguntas:**
- *"Explique a seção de instalação de forma mais simples"*
- *"Crie um exercício sobre o conteúdo desta seção"*
- *"Como adapto os comandos para Windows?"*
- *"Qual erro comum pode acontecer aqui?"*

---

## Variáveis de ambiente

Copie `.env.example` para `.env` e ajuste conforme necessário:

| Variável           | Padrão       | Descrição                                      |
|--------------------|--------------|------------------------------------------------|
| `OPENAI_API_KEY`   | —            | Chave da API OpenAI (obrigatória para IA real) |
| `LLM_SIMPLE_MODEL` | MiniMax-2.5  | Modelo para tarefas simples                    |
| `LLM_MEDIUM_MODEL` | gpt-5.4-nano | Modelo para tarefas médias                     |
| `LLM_COMPLEX_MODEL`| gpt-5.4-mini | Modelo para tarefas complexas                  |
| `LLM_MODE`         | balanced     | `balanced` / `economic` / `quality`            |

---

## Roadmap técnico

| Área              | Status   | Notas                                              |
|-------------------|----------|----------------------------------------------------|
| SQLite → Postgres | Planejado | `database.py` usa apenas SQL padrão; migração simples |
| Multi-usuário     | Planejado | Adicionar coluna `user_id` em `tutorials` e `tutorial_chats` |
| Versionamento     | Planejado | Tabela `tutorial_versions` com snapshot por save   |
| OpenAI streaming  | Planejado | Substituir `run_llm_task` por streaming com `st.write_stream` |
| Autenticação      | Planejado | Replit Auth ou Clerk para isolar dados por usuário |
| Testes automatizados | Parcial | Adicionar pytest com mocks para os agentes        |
