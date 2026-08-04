"""
app.py — Chainlit chat UI for AMQ Broker documentation search.
Retrieves indexed Red Hat documentation and answers questions with source citations.
Supports per-session file uploads: uploaded docs are embedded in-memory and merged
with the shared ChromaDB index for the duration of the session.
Usage: chainlit run app.py
Opens at http://127.0.0.1:8000
"""
import warnings
import logging

# Suppress non-critical warnings
warnings.filterwarnings("ignore", category=UserWarning, message=".*resource_tracker.*")
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*UnsupportedFieldAttributeWarning.*")
warnings.filterwarnings("ignore", message=".*validate_default.*")
warnings.filterwarnings("ignore", message=".*unauthenticated requests.*")

# Reduce verbosity of specific libraries to suppress debug/info logs
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub.utils").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub.repocard").setLevel(logging.ERROR)
logging.getLogger("chromadb").setLevel(logging.CRITICAL)
logging.getLogger("chromadb.telemetry").setLevel(logging.CRITICAL)
logging.getLogger("chromadb.telemetry.events").setLevel(logging.CRITICAL)

from llama_index.core import SimpleDirectoryReader, VectorStoreIndex, Settings
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.chat_engine import CondensePlusContextChatEngine
from llama_index.core.memory import ChatMemoryBuffer
from engine import build_chat_engine, SYSTEM_PROMPT
import chainlit as cl
import os
import shutil
import tempfile

# Supported upload extensions (matches ingest.py)
_SUPPORTED_EXTS = {".pdf", ".txt", ".md", ".xml"}

# Build the base engine once when the server starts (index stays in RAM)
_engine = build_chat_engine()
# Keep a reference to the base retriever for merging with per-session uploads
_base_retriever = _engine._retriever


def _build_upload_engine(file_paths: list[str]) -> CondensePlusContextChatEngine:
    """
    Parse uploaded files, embed them in-memory, and return a new chat engine
    whose retriever fuses results from the in-memory index and the shared
    ChromaDB index.
    """
    # Copy files into a temp directory so SimpleDirectoryReader can scan them
    tmp_dir = tempfile.mkdtemp(prefix="cl_upload_")
    try:
        for src in file_paths:
            shutil.copy2(src, tmp_dir)

        documents = SimpleDirectoryReader(
            tmp_dir,
            required_exts=list(_SUPPORTED_EXTS),
            recursive=False,
        ).load_data()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    node_parser = SentenceSplitter(chunk_size=512, chunk_overlap=64)
    nodes = node_parser.get_nodes_from_documents(documents)

    upload_index = VectorStoreIndex(nodes)
    upload_retriever = upload_index.as_retriever(similarity_top_k=4)

    fused_retriever = QueryFusionRetriever(
        retrievers=[_base_retriever, upload_retriever],
        similarity_top_k=6,
        num_queries=1,       # no query rewriting — use the original query only
        use_async=False,
    )

    memory = ChatMemoryBuffer.from_defaults(token_limit=4096)

    return CondensePlusContextChatEngine.from_defaults(
        retriever=fused_retriever,
        memory=memory,
        system_prompt=SYSTEM_PROMPT,
        verbose=False,
    )


@cl.on_chat_start
async def on_start():
    cl.user_session.set("engine", _engine)
    cl.user_session.set("uploaded_files", set())
    await cl.Message(
        content=(
            "**AMQ Broker Documentation Search** is ready.\n\n"
            "Search the indexed Red Hat AMQ Broker documentation for "
            "configuration, troubleshooting, HA setup, and performance tuning. "
            "Answers are grounded in official Red Hat docs with source citations.\n\n"
            "You can also **upload your own files** (`.pdf`, `.txt`, `.md`, `.xml`) "
            "and they will be included in the search for this session.\n\n"
            "_Try: \"How do I configure persistent storage?\"_"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    engine = cl.user_session.get("engine")
    uploaded_files: set = cl.user_session.get("uploaded_files")

    # Collect any new supported files attached to this message
    new_files = []
    if message.elements:
        for el in message.elements:
            if (
                hasattr(el, "path")
                and el.path
                and os.path.splitext(el.name or el.path)[1].lower() in _SUPPORTED_EXTS
                and el.path not in uploaded_files
            ):
                new_files.append(el.path)
                uploaded_files.add(el.path)

    # If new files arrived, rebuild the engine with the fused retriever
    if new_files:
        all_uploaded = list(uploaded_files)
        thinking = cl.Message(content="⏳ Indexing uploaded file(s), please wait…")
        await thinking.send()
        engine = await cl.make_async(_build_upload_engine)(all_uploaded)
        cl.user_session.set("engine", engine)
        cl.user_session.set("uploaded_files", uploaded_files)
        await thinking.remove()
        names = ", ".join(os.path.basename(p) for p in new_files)
        await cl.Message(content=f"✅ Indexed **{names}** — now included in this session's search.").send()

    msg = cl.Message(content="")
    await msg.send()

    # Stream tokens into the message as they arrive
    stream = await cl.make_async(engine.stream_chat)(message.content)
    for token in stream.response_gen:
        await msg.stream_token(token)

    await msg.update()

    # Attach retrieved source documents as collapsible elements
    sources = []
    if hasattr(stream, "source_nodes"):
        for i, node in enumerate(stream.source_nodes, 1):
            meta    = node.node.metadata
            fname   = meta.get("file_name", "unknown")
            score   = round(node.score or 0, 3)
            snippet = (node.node.get_content() or "")[:400]
            sources.append(
                cl.Text(
                    name=f"Source {i} — {fname} (score {score})",
                    content=snippet + ("…" if len(snippet) == 400 else ""),
                    display="side",
                )
            )

    msg.elements = sources
    await msg.update()
