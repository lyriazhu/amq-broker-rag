"""
app.py — Chainlit chat UI for AMQ Broker documentation search.
Retrieves indexed Red Hat documentation and answers questions with source citations.
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

from engine import build_chat_engine
import chainlit as cl

# Build the engine once when the server starts (index stays in RAM)
_engine = build_chat_engine()


@cl.on_chat_start
async def on_start():
    cl.user_session.set("engine", _engine)
    await cl.Message(
        content=(
            "**AMQ Broker Documentation Search** is ready.\n\n"
            "Search the indexed Red Hat AMQ Broker documentation for "
            "configuration, troubleshooting, HA setup, and performance tuning. "
            "Answers are grounded in official Red Hat docs with source citations.\n\n"
            "_Try: \"How do I configure persistent storage?\"_"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    engine = cl.user_session.get("engine")

    resp = await cl.make_async(engine.chat)(message.content)

    msg = cl.Message(content=str(resp))

    # Attach retrieved source documents as collapsible elements
    sources = []
    if hasattr(resp, "source_nodes"):
        for i, node in enumerate(resp.source_nodes, 1):
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
    await msg.send()
