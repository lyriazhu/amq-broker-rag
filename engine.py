"""
engine.py — Loads the persisted ChromaDB index and returns a
LlamaIndex chat engine backed by a local Ollama LLM.
No API key required.

Routing architecture
--------------------
Three QueryEngineTools are assembled and dispatched by a RouterQueryEngine:

  1. amq_broker_docs   — ChromaDB vector search over official AMQ Broker docs
  2. prometheus_metrics — Live metrics via Prometheus HTTP API (optional)
  3. jolokia_jmx        — Live JMX attributes via Jolokia REST (optional)

The live tools are only registered when their respective environment variables
(PROMETHEUS_URL, JOLOKIA_URL) are set.  When neither is configured the
RouterQueryEngine holds only the doc tool, preserving existing behaviour.
"""
import os
import re
import logging
from typing import List
from dotenv import load_dotenv

load_dotenv()

import chromadb
from llama_index.core import VectorStoreIndex, Settings
from llama_index.core.chat_engine import CondensePlusContextChatEngine
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.vector_stores import MetadataFilter, MetadataFilters, FilterOperator, FilterCondition
from llama_index.core.tools import QueryEngineTool
from llama_index.core.query_engine import RouterQueryEngine
from llama_index.core.selectors import LLMSingleSelector
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.schema import QueryBundle, NodeWithScore, TextNode
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.ollama import Ollama

logger = logging.getLogger(__name__)


class _RouterRetriever(BaseRetriever):
    """
    Thin BaseRetriever adapter that delegates to a RouterQueryEngine.

    CondensePlusContextChatEngine requires a BaseRetriever, but
    RouterQueryEngine is a query engine (not a retriever).  This adapter
    bridges the gap: it calls router.query() and wraps the response text
    as a single NodeWithScore so the chat engine can include it in context.
    Source nodes from the router's response are also forwarded when available.
    """

    def __init__(self, router: RouterQueryEngine) -> None:
        super().__init__()
        self._router = router

    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        response = self._router.query(query_bundle)
        response_text = str(response)

        # Prefer the source nodes the router already collected (e.g. from the
        # doc tool's ChromaDB results).  Fall back to a synthetic node that
        # wraps the router's text response (used for live tool answers).
        source_nodes = getattr(response, "source_nodes", None)
        if source_nodes:
            return source_nodes

        return [
            NodeWithScore(
                node=TextNode(
                    text=response_text,
                    metadata=getattr(response, "metadata", {}) or {},
                ),
                score=1.0,
            )
        ]

    async def _aretrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        # Fall back to sync for now — Ollama and Jolokia/Prometheus clients
        # are synchronous; async support can be added later.
        return self._retrieve(query_bundle)

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

You also have access to live data tools:
- Use the Prometheus metrics tool when asked about current message counts,
  queue depths, consumer counts, memory or disk usage, or any real-time metric.
- Use the Jolokia JMX tool when asked about live broker state: uptime, version,
  HA replication sync, NodeID, or current queue/address attributes.
- Use the AMQ Broker docs tool for all configuration, conceptual, or
  troubleshooting questions that require documentation.

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


def _build_doc_tool(query: str | None = None) -> QueryEngineTool:
    """
    Build a QueryEngineTool backed by a QueryFusionRetriever over ChromaDB.
    When *query* contains a version string the retriever is scoped to that
    version's documents only.
    """
    chroma_client     = chromadb.PersistentClient(path=CHROMA_PATH)
    chroma_collection = chroma_client.get_or_create_collection("artemis_docs")
    vector_store      = ChromaVectorStore(chroma_collection=chroma_collection)
    index             = VectorStoreIndex.from_vector_store(vector_store)

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

    from llama_index.core.query_engine import RetrieverQueryEngine

    doc_query_engine = RetrieverQueryEngine.from_args(
        retriever=retriever,
        node_postprocessors=[],
        verbose=False,
    )

    return QueryEngineTool.from_defaults(
        query_engine=doc_query_engine,
        name="amq_broker_docs",
        description=(
            "Use this tool for ANY question about AMQ Broker / Apache ActiveMQ Artemis "
            "documentation: configuration, deployment on OpenShift, getting started, "
            "HA setup, address and queue concepts, flow control, paging, journal, "
            "release notes, troubleshooting procedures, and CLI commands. "
            "Do NOT use for live/real-time metric values from a running broker."
        ),
    )


def _build_live_tools() -> list[QueryEngineTool]:
    """Conditionally build Prometheus and Jolokia tools based on env config."""
    from tools.prometheus_query_engine import build_prometheus_tool
    from tools.jolokia_query_engine import build_jolokia_tool

    tools = []
    prom = build_prometheus_tool()
    if prom:
        tools.append(prom)
    jol = build_jolokia_tool()
    if jol:
        tools.append(jol)
    return tools


def build_chat_engine(query: str | None = None):
    """
    Build and return a CondensePlusContextChatEngine backed by a
    RouterQueryEngine.

    The router dispatches each condensed query to the most appropriate tool:
      - doc questions   → amq_broker_docs (ChromaDB + QueryFusionRetriever)
      - live metrics    → prometheus_metrics (Prometheus HTTP API)
      - live JMX state  → jolokia_jmx (Jolokia REST API)

    Live tools are only included when PROMETHEUS_URL / JOLOKIA_URL are set.
    When *query* is provided and contains a version string the doc retriever
    is scoped to documents for that version, preventing cross-version
    hallucinations.
    """
    doc_tool   = _build_doc_tool(query)
    live_tools = _build_live_tools()

    all_tools = [doc_tool] + live_tools

    if len(all_tools) == 1:
        # Only the doc tool — use it directly as the query engine to avoid the
        # selector LLM call overhead when no live tools are available.
        router_engine = doc_tool.query_engine
        logger.debug("build_chat_engine: no live tools configured, using doc engine directly")
    else:
        router_engine = RouterQueryEngine(
            selector=LLMSingleSelector.from_defaults(),
            query_engine_tools=all_tools,
            verbose=False,
        )
        logger.debug(
            "build_chat_engine: RouterQueryEngine with tools: %s",
            [t.metadata.name for t in all_tools],
        )

    memory = ChatMemoryBuffer.from_defaults(token_limit=4096)

    # CondensePlusContextChatEngine only accepts a BaseRetriever.
    # _RouterRetriever bridges the gap when using RouterQueryEngine.
    # When only the doc tool is present, the doc retriever is used directly.
    if isinstance(router_engine, RouterQueryEngine):
        retriever = _RouterRetriever(router_engine)
    else:
        # Bare doc query engine — expose its underlying retriever directly.
        retriever = router_engine._retriever  # type: ignore[attr-defined]

    return CondensePlusContextChatEngine.from_defaults(
        retriever=retriever,
        memory=memory,
        node_postprocessors=[],
        system_prompt=SYSTEM_PROMPT,
        verbose=False,
    )
