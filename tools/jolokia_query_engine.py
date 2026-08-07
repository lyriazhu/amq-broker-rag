"""
tools/jolokia_query_engine.py
------------------------------
A LlamaIndex CustomQueryEngine that reads live JMX attributes from an AMQ
Broker via the Jolokia REST API (bundled with the Hawtio console on port 8161).

Jolokia exposes the full ActiveMQ Artemis MBean tree as HTTP+JSON.  This
engine lets the LLM ask for any broker attribute by parsing the question,
deciding which MBeans to read, and returning the raw attribute values.

Environment variables (set in .env):
    JOLOKIA_URL       — e.g. http://localhost:8161/console/jolokia
    JOLOKIA_USER      — Basic-auth username (default: admin)
    JOLOKIA_PASSWORD  — Basic-auth password (read from env, never hardcoded)
    Leave JOLOKIA_URL unset or blank to disable this tool.
"""

from __future__ import annotations

import os
import json
import logging
from typing import Optional

import requests
from llama_index.core.query_engine.custom import CustomQueryEngine
from llama_index.core.llms import LLM

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MBean selection prompt
# The LLM must return a JSON array of Jolokia "read" request objects.
# ---------------------------------------------------------------------------
_MBEAN_PROMPT = """\
You are an Apache ActiveMQ Artemis JMX expert.
Given the user question, produce a JSON array of Jolokia "read" request objects
that will answer it.  Reply with ONLY valid JSON — no explanation, no markdown.

Jolokia read request schema:
  {{"type": "read", "mbean": "<ObjectName>", "attribute": "<AttributeName>"}}

Useful MBean patterns (replace <broker>, <address>, <queue>, <routing-type>):
  org.apache.activemq.artemis:broker="<broker>"
    Attributes: Version, Uptime, AddressMemoryUsage, TotalMessageCount,
                TotalConsumerCount, HAPolicy, NodeID, ReplicaSync
  org.apache.activemq.artemis:broker="<broker>",component=addresses,address="<address>"
    Attributes: RoutingTypes, NumberOfMessages, AddressSize, NumberOfPages
  org.apache.activemq.artemis:broker="<broker>",component=addresses,address="<address>",subcomponent=queues,routing-type="<routing-type>",queue="<queue>"
    Attributes: MessageCount, ConsumerCount, DeliveringCount, MessagesAdded,
                MessagesAcknowledged, MessagesExpired, Paused, Durable

Use broker="0.0.0.0" if the broker name is unknown.
Use routing-type="anycast" for point-to-point queues; "multicast" for topics.
If the question mentions a specific address or queue name, include it.
If no name is mentioned, use the broker-level MBean only.

User question: {question}
JSON:"""


class JolokiaQueryEngine(CustomQueryEngine):
    """Query engine that fetches live JMX data from AMQ Broker via Jolokia."""

    jolokia_url: str
    jolokia_user: str = "admin"
    jolokia_password: str = ""
    llm: Optional[LLM] = None

    def custom_query(self, query_str: str) -> str:  # type: ignore[override]
        # 1. Ask the LLM which MBeans to read
        requests_payload = self._build_requests(query_str)
        if not requests_payload:
            return "[Jolokia] Could not determine which MBeans to query for this question."

        logger.debug("JolokiaQueryEngine: requests=%s", json.dumps(requests_payload))

        # 2. Execute a Jolokia bulk-read POST (single round trip for all MBeans)
        try:
            auth = (self.jolokia_user, self.jolokia_password) if self.jolokia_user else None
            resp = requests.post(
                self.jolokia_url,
                json=requests_payload,
                auth=auth,
                timeout=10,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            return f"[Jolokia] Request failed: {exc}"

        raw = resp.json()
        return self._format_result(raw)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_requests(self, question: str) -> list[dict]:
        """Use the LLM to translate the question into Jolokia read requests."""
        from llama_index.core import Settings

        llm = self.llm or Settings.llm
        prompt = _MBEAN_PROMPT.format(question=question)
        response = llm.complete(prompt)
        raw_text = response.text.strip()

        # Strip optional markdown code fences the LLM may add
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            logger.warning("JolokiaQueryEngine: LLM returned invalid JSON: %s", exc)
            return []

        # Accept either a single object or an array
        if isinstance(parsed, dict):
            parsed = [parsed]
        return parsed if isinstance(parsed, list) else []

    @staticmethod
    def _format_result(raw: object) -> str:
        """Convert a Jolokia JSON response (single or bulk) into readable text."""
        if isinstance(raw, dict):
            raw = [raw]

        if not isinstance(raw, list):
            return f"[Jolokia] Unexpected response format: {raw}"

        lines = ["Live JMX data from AMQ Broker (via Jolokia):", ""]

        for item in raw:
            status = item.get("status", 0)
            request = item.get("request", {})
            mbean = request.get("mbean", "?")
            attribute = request.get("attribute", "?")
            value = item.get("value")
            error = item.get("error")

            if status == 200 and value is not None:
                if isinstance(value, dict):
                    # Multiple attributes returned as a dict
                    lines.append(f"  {mbean}")
                    for k, v in value.items():
                        lines.append(f"    {k}: {v}")
                else:
                    lines.append(f"  {mbean} → {attribute}: {value}")
            else:
                lines.append(f"  {mbean} [{attribute}] — error (status {status}): {error}")

        return "\n".join(lines)


def build_jolokia_tool():
    """
    Return a QueryEngineTool wrapping JolokiaQueryEngine, or None if
    JOLOKIA_URL is not configured.
    """
    from llama_index.core.tools import QueryEngineTool

    url = os.getenv("JOLOKIA_URL", "").strip()
    if not url:
        logger.info("JOLOKIA_URL not set — Jolokia tool disabled.")
        return None

    engine = JolokiaQueryEngine(
        jolokia_url=url,
        jolokia_user=os.getenv("JOLOKIA_USER", "admin"),
        jolokia_password=os.getenv("JOLOKIA_PASSWORD", ""),
    )
    return QueryEngineTool.from_defaults(
        query_engine=engine,
        name="jolokia_jmx",
        description=(
            "Use this tool for questions about LIVE AMQ Broker state via JMX: "
            "broker uptime, version, HA replication sync status, NodeID, "
            "live queue message counts, consumer counts, delivering counts, "
            "address memory size, paging state, or whether a queue is paused. "
            "Do NOT use for documentation, configuration explanations, or conceptual questions."
        ),
    )
