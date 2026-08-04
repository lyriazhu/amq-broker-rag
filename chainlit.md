# AMQ Broker RAG

A local Retrieval-Augmented Generation (RAG) assistant for Red Hat AMQ Broker operations. Ask questions about broker configuration, troubleshooting, HA setup, OpenShift deployment, and performance tuning — and get answers grounded in the official Red Hat documentation, with source citations.

No API keys or internet connection required at query time. Everything runs locally using Ollama and a HuggingFace embedding model.

---

## What it does

The assistant indexes the official Red Hat AMQ Broker PDF documentation (versions 7.10 – 7.14, plus AMQ Clients and Qpid JMS/Proton guides) into a local ChromaDB vector store. When you ask a question, it retrieves the most relevant document chunks and passes them to a locally running LLM (via Ollama) alongside a focused system prompt that keeps answers concrete and operational.

Answers include:

- CLI (`artemis`) commands
- `broker.xml` / `bootstrap.xml` config XML snippets
- Hawtio console and JMX MBean steps
- OpenShift deployment guidance
- HA replication and paging/journal diagnostics
- Citations back to the source document section

---

## Indexed documentation

| Document | Versions |
|----------|---------|
| Red Hat AMQ Broker — Getting Started | 7.10 – 7.14 |
| Red Hat AMQ Broker — Configuring AMQ Broker | 7.10 – 7.14 |
| Red Hat AMQ Broker — Managing AMQ Broker | 7.10 – 7.14 |
| Red Hat AMQ Broker — Deploying on OpenShift | 7.10 – 7.14 |
| Red Hat AMQ Broker — Release Notes | 7.10 – 7.14 |
| AMQ Spring Boot Starter | 3.5.6 |
| AMQ Clients Overview | 2026.Q1 |
| Red Hat build of Apache Qpid JMS | 2.10 |
| Red Hat build of Apache Qpid ProtonJ2 | 1.1.0 |
| Red Hat build of Apache Qpid Proton .NET | 1.0.0 |
