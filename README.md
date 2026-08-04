# AMQ Broker RAG

A local Retrieval-Augmented Generation (RAG) assistant for Red Hat AMQ Broker operations. Ask questions about broker configuration, troubleshooting, HA setup, OpenShift deployment, and performance tuning — and get answers grounded in the official Red Hat documentation, with source citations.

No API keys or internet connection required at query time. Everything runs locally using Ollama and a HuggingFace embedding model.

---

## What it does

The assistant indexes the official Red Hat AMQ Broker PDF documentation (versions 7.10 – 7.14, plus AMQ Clients and Qpid JMS/Proton guides) into a local ChromaDB vector store. When you ask a question, it retrieves the most relevant document chunks and passes them to a locally running LLM (via Ollama) alongside a focused system prompt that keeps answers concrete and operational.

Answers include:

- CLI (`artemis`) commands
- `broker.xml` / `bootstrap.xml` config XML snippets
- Hawtio console and JMX MBean steps
- OpenShift deployment guidance
- HA replication and paging/journal diagnostics
- Citations back to the source document section

---

## Architecture

```
docs/ (PDFs)
    │
    ▼
ingest.py  ──►  SentenceSplitter  ──►  HuggingFace embeddings  ──►  ChromaDB (./chroma_db)
                                                                          │
chainlit run app.py                                                       │
    │                                                                     │
    ▼                                                                     │
app.py  ──►  engine.py  ──►  LlamaIndex VectorStoreIndex  ◄──────────────┘
                │
                ▼
            Ollama (llama3.2)  ──►  Chainlit chat UI (http://127.0.0.1:8000)
```

---

## Prerequisites

| Tool | Purpose |
|------|---------|
| Python 3.11 | Runtime |
| [Ollama](https://ollama.com) | Local LLM inference |
| `llama3.2` model pulled in Ollama | Default LLM |

Pull the model before first run:

```zsh
ollama pull llama3.2
```

---

## Setup

```zsh
# 1. Create and activate a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy the example env file (defaults work out of the box)
cp .env.example .env
```

---

## Usage

### Step 1 — Ingest documents (run once)

Reads all PDFs from `./docs`, chunks them, generates embeddings, and persists the index to `./chroma_db`.

```zsh
python ingest.py
```

Re-run any time you add new documents to `./docs`.

### Step 2 — Start the chat UI

```zsh
chainlit run app.py
```

Opens at **http://127.0.0.1:8000**. Each response shows retrieved source document chunks in a collapsible side panel.

---

## Configuration

All settings can be overridden in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `DOCS_PATH` | `./docs` | Directory containing source PDFs |
| `CHROMA_PATH` | `./chroma_db` | Persisted vector store location |
| `EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | HuggingFace embedding model (downloads ~130 MB on first use) |
| `LLM_MODEL` | `llama3.2` | Ollama model name |

---

## Indexed documentation

| Document | Versions |
|----------|---------|
| Red Hat AMQ Broker — Getting Started | 7.10 – 7.14 |
| Red Hat AMQ Broker — Configuring AMQ Broker | 7.10 – 7.14 |
| Red Hat AMQ Broker — Managing AMQ Broker | 7.10 – 7.14 |
| Red Hat AMQ Broker — Deploying on OpenShift | 7.10 – 7.14 |
| Red Hat AMQ Broker — Release Notes | 7.10 – 7.14 |
| AMQ Spring Boot Starter | 3.5.6 |
| AMQ Clients Overview | 2026.Q1 |
| Red Hat build of Apache Qpid JMS | 2.10 |
| Red Hat build of Apache Qpid ProtonJ2 | 1.1.0 |
| Red Hat build of Apache Qpid Proton .NET | 1.0.0 |
