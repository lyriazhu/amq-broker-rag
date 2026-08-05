# Project Overview

## What This Project Is

A Retrieval-Augmented Generation (RAG) system for **Red Hat AMQ Broker** (Apache ActiveMQ Artemis). It is trained on official AMQ Broker documentation and exposes a chat interface where users can ask natural-language questions and receive grounded, cited answers — without manually searching through PDFs.

## Goals and Objectives

- **Primary**: Reduce the time engineers and customers spend reading AMQ Broker documentation and debugging issues.
- **Demo milestone**: Deliver a working, polished demo showcasing question-answering over AMQ Broker docs.
- **Next priorities**:
  - Expand the knowledge base with more documentation versions and sources so more questions can be answered.
  - Make the retrieval and reasoning more advanced (e.g. better chunking, re-ranking, multi-hop queries).

## Key Users

| User | Use case |
|------|----------|
| AMQ Broker engineers | Fast lookup during debugging, config troubleshooting, HA setup |
| Customers | Self-service answers to configuration, deployment, and operations questions |

## Architecture

```
docs/ (PDFs, XMLs, MDs)
   │
   ▼ ingest.py — chunks & embeds via HuggingFace (BAAI/bge-small-en-v1.5)
   │
   ▼ chroma_db/ — persisted ChromaDB vector store (collection: artemis_docs)
   │
   ▼ engine.py — builds LlamaIndex chat engine backed by Ollama (llama3.2)
               — applies version-scoped metadata filters when query contains "7.x"
               — SimilarityPostprocessor cutoff: 0.58
   │
   ▼ app.py — Chainlit UI
            — password auth (local only)
            — SQLite chat history (chat_history.sqlite)
            — per-session file upload → in-memory index fused via QueryFusionRetriever
            — streams tokens, attaches source citations as collapsible elements
```

## Key Modules

| File | Role |
|------|------|
| [`ingest.py`](../../ingest.py) | One-time index build. Loads docs from `./docs`, chunks at 512 tokens (64 overlap), persists to ChromaDB. |
| [`engine.py`](../../engine.py) | Loads ChromaDB index, configures HuggingFace embeddings + Ollama LLM, builds `CondensePlusContextChatEngine`. Version-filter logic lives here. |
| [`app.py`](../../app.py) | Chainlit application. Handles auth, session state, file uploads, streaming responses, and source display. |
| `chroma_db/` | Persisted vector store. Not committed to git. |
| `docs/` | Source documentation PDFs/XMLs organised by AMQ Broker version. |
| `.env` / `.env.example` | Runtime config: `CHROMA_PATH`, `EMBED_MODEL`, `LLM_MODEL`, `DOCS_PATH`. |

## Configuration

Key environment variables (see `.env.example`):

```
CHROMA_PATH=./chroma_db
EMBED_MODEL=BAAI/bge-small-en-v1.5
LLM_MODEL=llama3.2
DOCS_PATH=./docs
```

## Running the Project

```bash
# 1. Build / refresh the index (run once per docs update)
python ingest.py

# 2. Start the chat UI
chainlit run app.py
# Opens at http://127.0.0.1:8000
```

## Known Constraints

- ChromaDB 0.6.x does not support substring metadata filters — version filtering uses exact `$in` matching on filenames.
- Ollama must be running locally with the target model pulled before starting the app.
- File uploads are session-scoped only; they are not persisted to the shared ChromaDB index.
