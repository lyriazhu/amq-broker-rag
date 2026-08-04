"""
ingest.py — Run once to build the ChromaDB vector index.
Uses local HuggingFace embeddings — no API key required.
Usage: python ingest.py
"""
import os
from dotenv import load_dotenv

load_dotenv()

import chromadb
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex, Settings, StorageContext
from llama_index.core.node_parser import SentenceSplitter
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.ollama import Ollama

# ---------- Configuration ----------
DOCS_PATH   = os.getenv("DOCS_PATH", "./docs")
CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
LLM_MODEL   = os.getenv("LLM_MODEL", "llama3.2")

# ---------- Global LlamaIndex settings ----------
# HuggingFace embedding model runs locally on CPU (downloads once, ~130MB)
Settings.embed_model = HuggingFaceEmbedding(model_name=EMBED_MODEL)
Settings.llm         = Ollama(model=LLM_MODEL, request_timeout=120.0)

# ---------- Load documents ----------
print(f"Loading documents from {DOCS_PATH} ...")
documents = SimpleDirectoryReader(
    DOCS_PATH,
    required_exts=[".pdf", ".xml", ".md", ".txt"],
    recursive=True,
).load_data()
print(f"Loaded {len(documents)} document pages/sections.")

# ---------- Chunk with sentence splitter ----------
node_parser = SentenceSplitter(chunk_size=512, chunk_overlap=64)
nodes = node_parser.get_nodes_from_documents(documents)
print(f"Created {len(nodes)} chunks.")

# ---------- Build / update ChromaDB index ----------
chroma_client     = chromadb.PersistentClient(path=CHROMA_PATH)
chroma_collection = chroma_client.get_or_create_collection("artemis_docs")
vector_store      = ChromaVectorStore(chroma_collection=chroma_collection)
storage_context   = StorageContext.from_defaults(vector_store=vector_store)

index = VectorStoreIndex(nodes, storage_context=storage_context, show_progress=True)
print("Index built and persisted to disk.")
print(f"Collection size: {chroma_collection.count()} vectors")
