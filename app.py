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
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
import chainlit as cl
import os
import shutil
import tempfile

# ---------------------------------------------------------------------------
# Persist chat history locally in a SQLite database (never committed to git).
# The .files/ directory stores uploaded file attachments across sessions.
# Both are listed in .gitignore so they remain machine-local only.
# ---------------------------------------------------------------------------
_DB_PATH = os.path.join(os.path.dirname(__file__), "chat_history.sqlite")
_FILES_DIR = os.path.join(os.path.dirname(__file__), ".files")
os.makedirs(_FILES_DIR, exist_ok=True)


@cl.data_layer
def get_data_layer():
    return SQLAlchemyDataLayer(conninfo=f"sqlite+aiosqlite:///{_DB_PATH}")


# ---------------------------------------------------------------------------
# Create the SQLite schema on first run (SQLAlchemyDataLayer does not
# auto-migrate; tables must exist before any auth or persistence call).
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    "id"          TEXT PRIMARY KEY,
    "identifier"  TEXT NOT NULL UNIQUE,
    "createdAt"   TEXT,
    "metadata"    TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS threads (
    "id"             TEXT PRIMARY KEY,
    "createdAt"      TEXT,
    "name"           TEXT,
    "userId"         TEXT REFERENCES users("id") ON DELETE CASCADE,
    "userIdentifier" TEXT,
    "tags"           TEXT,
    "metadata"       TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    "id"            TEXT PRIMARY KEY,
    "name"          TEXT NOT NULL,
    "type"          TEXT NOT NULL,
    "threadId"      TEXT NOT NULL REFERENCES threads("id") ON DELETE CASCADE,
    "parentId"      TEXT,
    "streaming"     INTEGER NOT NULL DEFAULT 0,
    "waitForAnswer" INTEGER,
    "isError"       INTEGER,
    "metadata"      TEXT,
    "tags"          TEXT,
    "input"         TEXT,
    "output"        TEXT,
    "createdAt"     TEXT,
    "start"         TEXT,
    "end"           TEXT,
    "generation"    TEXT,
    "showInput"     TEXT,
    "language"      TEXT,
    "indent"        INTEGER
);

CREATE TABLE IF NOT EXISTS elements (
    "id"           TEXT PRIMARY KEY,
    "threadId"     TEXT REFERENCES threads("id") ON DELETE CASCADE,
    "type"         TEXT,
    "chainlitKey"  TEXT,
    "url"          TEXT,
    "objectKey"    TEXT,
    "name"         TEXT NOT NULL,
    "display"      TEXT NOT NULL,
    "size"         TEXT,
    "language"     TEXT,
    "page"         INTEGER,
    "autoPlay"     INTEGER,
    "playerConfig" TEXT,
    "forId"        TEXT,
    "mime"         TEXT,
    "props"        TEXT
);

CREATE TABLE IF NOT EXISTS feedbacks (
    "id"      TEXT PRIMARY KEY,
    "forId"   TEXT NOT NULL,
    "value"   INTEGER NOT NULL,
    "comment" TEXT
);

CREATE TRIGGER IF NOT EXISTS threads_preserve_created_at
AFTER UPDATE OF "createdAt" ON threads
FOR EACH ROW
WHEN OLD."createdAt" IS NOT NULL
  AND NEW."createdAt" != OLD."createdAt"
BEGIN
    UPDATE threads SET "createdAt" = OLD."createdAt" WHERE id = OLD.id;
END;
"""


@cl.on_app_startup
async def on_startup():
    """Create the SQLite schema on first launch if tables don't exist yet."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    engine = create_async_engine(f"sqlite+aiosqlite:///{_DB_PATH}")
    async with engine.begin() as conn:
        for statement in _SCHEMA_SQL.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                await conn.execute(text(stmt))
    await engine.dispose()


# ---------------------------------------------------------------------------
# Auth — a single local user so Chainlit enables its built-in thread history
# sidebar (pencil icon, conversation list, hover-to-delete).
# This is a local-only tool; no real password enforcement is needed.
# ---------------------------------------------------------------------------
@cl.password_auth_callback
async def auth_callback(username: str, password: str):
    return cl.User(identifier=username, metadata={"role": "user"})


# Supported upload extensions (matches ingest.py)
_SUPPORTED_EXTS = {".pdf", ".txt", ".md", ".xml"}

# Build a base engine at startup to warm up the embedding model and hold a
# reference to the shared retriever needed for upload-fused queries.
_base_engine = build_chat_engine()
_base_retriever = _base_engine._retriever


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
    cl.user_session.set("engine", _base_engine)
    cl.user_session.set("uploaded_files", set())
    cl.user_session.set("has_user_message", False)
    welcome = cl.Message(
        content=(
            "**AMQ Broker Documentation Search** is ready.\n\n"
            "Search the indexed Red Hat AMQ Broker documentation for "
            "configuration, troubleshooting, HA setup, and performance tuning. "
            "Answers are grounded in official Red Hat docs with source citations.\n\n"
            "You can also **upload your own files** (`.pdf`, `.txt`, `.md`, `.xml`) "
            "and they will be included in the search for this session.\n\n"
            "_Try: \"What features are included in AMQ Broker 7.14?\"_"
        )
    )
    welcome.parent_id = None
    await welcome.send()


@cl.on_chat_end
async def on_end():
    """Delete the thread when the session closes with no user messages sent."""
    if cl.user_session.get("has_user_message"):
        return
    from chainlit.data import get_data_layer
    data_layer = get_data_layer()
    if data_layer:
        thread_id = cl.context.session.thread_id
        await data_layer.delete_thread(thread_id=thread_id)


@cl.on_chat_resume
async def on_resume(thread):
    """Restore the chat engine when a previous conversation is reopened."""
    cl.user_session.set("engine", _base_engine)
    cl.user_session.set("uploaded_files", set())
    cl.user_session.set("has_user_message", True)  # resumed threads always have messages


@cl.on_message
async def on_message(message: cl.Message):
    cl.user_session.set("has_user_message", True)
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
    elif not uploaded_files:
        # No uploads active — rebuild a version-scoped engine for this query so
        # that mentions of e.g. "7.14" filter retrieval to the right documents.
        engine = await cl.make_async(build_chat_engine)(message.content)
    else:
        engine = cl.user_session.get("engine")

    # Clear parent_id BEFORE send() so the initial create_step call never
    # writes a parentId to the DB.  The run-wrapper step (on_message / on_chat_start)
    # that Chainlit sets as parent is never persisted, so any response that
    # references it becomes invisible when the thread is resumed.
    msg = cl.Message(content="")
    msg.parent_id = None
    await msg.send()

    # Stream tokens into the message as they arrive
    stream = await cl.make_async(engine.stream_chat)(message.content)
    for token in stream.response_gen:
        await msg.stream_token(token)

    # Guard against blank responses (e.g. all retrieved chunks filtered out by
    # the SimilarityPostprocessor — the LLM receives no context and may emit
    # nothing rather than a proper "I don't know" reply).
    if not (msg.content or "").strip():
        msg.content = (
            "I wasn't able to find relevant information in the documentation "
            "to answer that question. Please try rephrasing, or ask something "
            "related to AMQ Broker / Artemis configuration and operations."
        )

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
