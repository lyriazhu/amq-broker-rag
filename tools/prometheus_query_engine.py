"""
tools/prometheus_query_engine.py
---------------------------------
A LlamaIndex CustomQueryEngine that translates a natural-language question
about live AMQ Broker metrics into a PromQL expression, executes it against
the Prometheus HTTP API, and returns a formatted plain-text result.

Environment variables (set in .env):
    PROMETHEUS_URL   — base URL, e.g. http://localhost:9090
                       Leave unset or blank to disable this tool.
"""

from __future__ import annotations

import os
import logging
from typing import Optional

import requests
from llama_index.core.query_engine.custom import CustomQueryEngine
from llama_index.core.base.response.schema import Response
from llama_index.core.llms import LLM

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PromQL translation prompt
# The LLM receives this prompt and must reply with *only* a PromQL expression
# on a single line — no explanation, no markdown.
# ---------------------------------------------------------------------------
_PROMQL_PROMPT = """\
You are a Prometheus expert for Apache ActiveMQ Artemis / Red Hat AMQ Broker.
Translate the user question into a single PromQL instant-query expression.
Reply with ONLY the PromQL expression — no explanation, no backticks.

Useful metric names:
  artemis_message_count{address="<name>",broker="<name>",queue="<name>"}
  artemis_consumer_count{address="<name>",broker="<name>",queue="<name>"}
  artemis_address_memory_usage{address="<name>",broker="<name>"}
  artemis_address_memory_usage_percentage{address="<name>",broker="<name>"}
  artemis_routed_message_count{address="<name>",broker="<name>"}
  artemis_unrouted_message_count{address="<name>",broker="<name>"}
  artemis_disk_store_usage{broker="<name>"}
  artemis_number_of_pages{address="<name>",broker="<name>"}
  artemis_delivering_message_count{address="<name>",broker="<name>",queue="<name>"}

Omit label selectors if the question does not specify a particular address/queue/broker.

User question: {question}
PromQL:"""


class PrometheusQueryEngine(CustomQueryEngine):
    """Query engine that fetches live metrics from the Prometheus HTTP API."""

    prometheus_url: str
    llm: Optional[LLM] = None

    def custom_query(self, query_str: str) -> str:  # type: ignore[override]
        # 1. Translate natural language → PromQL via the LLM
        promql = self._nl_to_promql(query_str)
        logger.debug("PrometheusQueryEngine: PromQL=%s", promql)

        # 2. Execute the instant query against Prometheus
        try:
            resp = requests.get(
                f"{self.prometheus_url}/api/v1/query",
                params={"query": promql},
                timeout=10,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            return f"[Prometheus] Request failed: {exc}"

        data = resp.json()
        if data.get("status") != "success":
            return f"[Prometheus] API error: {data.get('error', 'unknown error')}"

        return self._format_result(promql, data.get("data", {}))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _nl_to_promql(self, question: str) -> str:
        """Use the LLM to translate a natural-language question into PromQL."""
        from llama_index.core import Settings

        llm = self.llm or Settings.llm
        prompt = _PROMQL_PROMPT.format(question=question)
        response = llm.complete(prompt)
        return response.text.strip().strip("`")

    @staticmethod
    def _format_result(promql: str, data: dict) -> str:
        """Convert Prometheus JSON result into a readable text block."""
        result_type = data.get("resultType", "unknown")
        results = data.get("result", [])

        if not results:
            return (
                f"[Prometheus] No data returned for query `{promql}`.\n"
                "The metric may not exist or the time series may be empty."
            )

        lines = [f"Prometheus query: `{promql}`", f"Result type: {result_type}", ""]

        for item in results:
            metric_labels = item.get("metric", {})
            label_str = ", ".join(f'{k}="{v}"' for k, v in metric_labels.items())

            if result_type == "vector":
                # [timestamp, value]
                _ts, value = item.get("value", [None, "?"])
                lines.append(f"  {{{label_str}}} → {value}")
            elif result_type == "matrix":
                values = item.get("values", [])
                last_ts, last_val = values[-1] if values else (None, "?")
                lines.append(f"  {{{label_str}}} → {last_val} (latest of {len(values)} samples)")
            else:
                lines.append(f"  {item}")

        return "\n".join(lines)


def build_prometheus_tool():
    """
    Return a QueryEngineTool wrapping PrometheusQueryEngine, or None if
    PROMETHEUS_URL is not configured.
    """
    from llama_index.core.tools import QueryEngineTool

    url = os.getenv("PROMETHEUS_URL", "").strip()
    if not url:
        logger.info("PROMETHEUS_URL not set — Prometheus tool disabled.")
        return None

    engine = PrometheusQueryEngine(prometheus_url=url)
    return QueryEngineTool.from_defaults(
        query_engine=engine,
        name="prometheus_metrics",
        description=(
            "Use this tool for questions about LIVE, REAL-TIME AMQ Broker metrics: "
            "current message counts, queue depths, consumer counts, address memory usage, "
            "disk store usage, paging state, delivering message counts, or message rates. "
            "Do NOT use for documentation, configuration explanations, or conceptual questions."
        ),
    )
