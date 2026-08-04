"""
engine.py — Loads the persisted ChromaDB index and returns a
LlamaIndex chat engine backed by a local Ollama LLM.
No API key required.
"""
import os
from dotenv import load_dotenv

load_dotenv()

import chromadb
from llama_index.core import VectorStoreIndex, Settings
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.ollama import Ollama

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
LLM_MODEL   = os.getenv("LLM_MODEL", "llama3.2")

SYSTEM_PROMPT = """
You are an expert Apache ActiveMQ Artemis operations engineer.
You have access to the official Artemis user manual, broker configuration
files, and past incident runbooks.

This application supports file uploads. Users can attach .pdf, .txt, .md,
or .xml files directly to their messages and they will be indexed and
included in your context automatically for that session.

Rules:
- Answer with concrete commands, Hawtio console steps, JMX MBean
  operations, CLI (artemis) commands, or config XML snippets.
- When diagnosing, prioritise safe read-only steps first.
  Flag any destructive or irreversible action with WARNING.
- Always consider: paging vs journal mode, address-full-policy,
  flow control, executor thread pools, and HA replication state.
- For config suggestions, explain the trade-off.
- Scripts must read credentials from environment variables, never
  hardcode secrets.
- Cite the source document section when you use retrieved context.
- Keep answers concise unless detail is explicitly requested.
- Do not answer questions unrelated to AMQ Broker / Artemis operations.
"""

Settings.embed_model = HuggingFaceEmbedding(model_name=EMBED_MODEL)
Settings.llm         = Ollama(model=LLM_MODEL, request_timeout=120.0)


def build_chat_engine():
    chroma_client     = chromadb.PersistentClient(path=CHROMA_PATH)
    chroma_collection = chroma_client.get_or_create_collection("artemis_docs")
    vector_store      = ChromaVectorStore(chroma_collection=chroma_collection)
    index             = VectorStoreIndex.from_vector_store(vector_store)

    memory = ChatMemoryBuffer.from_defaults(token_limit=4096)

    return index.as_chat_engine(
        chat_mode="condense_plus_context",
        memory=memory,
        node_postprocessors=[],
        system_prompt=SYSTEM_PROMPT,
        similarity_top_k=6,
        verbose=False,
    )
