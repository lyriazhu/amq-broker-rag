"""
engine.py — Loads the persisted ChromaDB index and returns a
LlamaIndex chat engine backed by a local Ollama LLM.
No API key required.
"""
import os
import re
from dotenv import load_dotenv

load_dotenv()

import chromadb
from llama_index.core import VectorStoreIndex, Settings
from llama_index.core.chat_engine import CondensePlusContextChatEngine
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.vector_stores import MetadataFilter, MetadataFilters, FilterOperator, FilterCondition
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.ollama import Ollama

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
LLM_MODEL   = os.getenv("LLM_MODEL", "llama3.2")

# Document filename suffixes shared across all indexed versions.
# Used to build exact-match $in filters — ChromaDB 0.6.x does not support
# substring ($contains) operators, only equality-based ones.
_DOC_SUFFIXES = [
    "Configuring_AMQ_Broker-en-US.pdf",
    "Deploying_AMQ_Broker_on_OpenShift-en-US.pdf",
    "Getting_Started_with_AMQ_Broker-en-US.pdf",
    "Managing_AMQ_Broker-en-US.pdf",
    "Release_Notes_for_Red_Hat_AMQ_Broker_{ver}-en-US.pdf",
]

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
- Cite the source document and section when you use retrieved context.
- If the retrieved context does not contain the answer, say so clearly
  rather than guessing or referencing unrelated document versions.
- Keep answers concise unless detail is explicitly requested.
- Do not answer questions unrelated to AMQ Broker / Artemis operations.
"""

Settings.embed_model = HuggingFaceEmbedding(model_name=EMBED_MODEL)
Settings.llm         = Ollama(model=LLM_MODEL, request_timeout=120.0)

# Matches version strings like "7.14", "7.10", "7.11.2" in the user query
_VERSION_RE = re.compile(r'\b7\.\d+(?:\.\d+)?\b')


def _version_filters(query: str) -> MetadataFilters | None:
    """
    If the query mentions one or more AMQ 7.x version strings, return
    MetadataFilters that restrict retrieval to documents for those versions
    using exact $in matching (ChromaDB 0.6.x has no substring operator).
    When multiple versions are detected (e.g. "difference between 7.13 and
    7.14"), filenames for all detected versions are included in a single $in
    filter so cross-version comparison queries retrieve context from both.
    Returns None when no version is detected.
    """
    versions = list(dict.fromkeys(_VERSION_RE.findall(query)))  # deduplicated, order-preserving
    if not versions:
        return None
    # Build the exact filenames that LlamaIndex stores in file_name metadata,
    # for every detected version, and merge them into one $in list.
    filenames = []
    for version in versions:
        prefix = f"Red_Hat_AMQ_Broker-{version}-"
        filenames.extend(
            prefix + s.replace("{ver}", version) for s in _DOC_SUFFIXES
        )
    return MetadataFilters(
        filters=[
            MetadataFilter(
                key="file_name",
                value=filenames,
                operator=FilterOperator.IN,
            )
        ],
        condition=FilterCondition.AND,
    )


def build_chat_engine(query: str | None = None):
    """
    Build and return a chat engine.  When *query* is provided and contains
    an AMQ version number the retriever is scoped to documents for that
    version only, which prevents cross-version hallucinations.

    QueryFusionRetriever rewrites the user's query into NUM_QUERY_REWRITES
    alternative phrasings before retrieval.  This is critical for vague
    comparative queries (e.g. "difference between 7.13 and 7.14") whose
    literal wording doesn't appear in any document but whose rephrased forms
    (e.g. "AMQ Broker 7.14 release notes new features") score well.
    """
    chroma_client     = chromadb.PersistentClient(path=CHROMA_PATH)
    chroma_collection = chroma_client.get_or_create_collection("artemis_docs")
    vector_store      = ChromaVectorStore(chroma_collection=chroma_collection)
    index             = VectorStoreIndex.from_vector_store(vector_store)

    memory = ChatMemoryBuffer.from_defaults(token_limit=4096)

    filters = _version_filters(query) if query else None

    base_retriever = index.as_retriever(similarity_top_k=10, filters=filters)

    retriever = QueryFusionRetriever(
        retrievers=[base_retriever],
        similarity_top_k=10,
        num_queries=4,       # original + 3 LLM-generated rephrasings
        mode="reciprocal_rerank",
        use_async=False,
        verbose=False,
    )

    return CondensePlusContextChatEngine.from_defaults(
        retriever=retriever,
        memory=memory,
        node_postprocessors=[],
        system_prompt=SYSTEM_PROMPT,
        verbose=False,
    )
