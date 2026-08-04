"""
app.py — Chainlit chat UI for the Artemis RAG assistant.
Usage: chainlit run app.py
Opens at http://127.0.0.1:8000
"""
from engine import build_chat_engine
import chainlit as cl

# Build the engine once when the server starts (index stays in RAM)
_engine = build_chat_engine()


@cl.on_chat_start
async def on_start():
    cl.user_session.set("engine", _engine)
    await cl.Message(
        content=(
            "**Artemis Ops Expert** is ready.\n\n"
            "Ask me anything about AMQ Broker configuration, "
            "troubleshooting, HA setup, or performance tuning.\n\n"
            "_Try: \"Why is queue orders.incoming backing up?\"_"
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
