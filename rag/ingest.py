"""
Ingest rag/corpus/ into a local Chroma vector store.

Sources:
  - *.md   -> regulation/standard summaries, split into overlapping chunks
  - synthetic_near_misses.json -> one chunk per near-miss report

Embeddings are computed locally (sentence-transformers), so ingestion
needs no paid API and no ANTHROPIC_API_KEY.
"""

import json
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_core.documents import Document

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
PERSIST_DIR = Path(__file__).resolve().parent / "chroma_db"
COLLECTION_NAME = "safety_knowledge"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def load_markdown_docs() -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(chunk_size=900, chunk_overlap=150)
    docs = []
    for path in sorted(CORPUS_DIR.glob("*.md")):
        text = path.read_text()
        for i, chunk in enumerate(splitter.split_text(text)):
            docs.append(
                Document(
                    page_content=chunk,
                    metadata={
                        "source": path.name,
                        "doc_type": "regulation",
                        "chunk_index": i,
                    },
                )
            )
    return docs


def load_near_miss_docs() -> list[Document]:
    path = CORPUS_DIR / "synthetic_near_misses.json"
    if not path.exists():
        return []
    entries = json.loads(path.read_text())
    docs = []
    for entry in entries:
        docs.append(
            Document(
                page_content=entry["summary"],
                metadata={
                    "source": "synthetic_near_misses.json",
                    "doc_type": "near_miss",
                    "near_miss_id": entry.get("id", ""),
                    "zone_hazard_class": entry.get("zone_hazard_class", ""),
                    "pattern_tags": ",".join(entry.get("pattern_tags", [])),
                },
            )
        )
    return docs


def build_index() -> Chroma:
    docs = load_markdown_docs() + load_near_miss_docs()
    if not docs:
        raise RuntimeError(f"No corpus documents found in {CORPUS_DIR}")

    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)

    if PERSIST_DIR.exists():
        import shutil

        shutil.rmtree(PERSIST_DIR)

    store = Chroma.from_documents(
        documents=docs,
        embedding=embeddings,
        collection_name=COLLECTION_NAME,
        persist_directory=str(PERSIST_DIR),
    )
    print(f"[ingest] Indexed {len(docs)} chunks ({len(list(CORPUS_DIR.glob('*.md')))} md files + near-misses) into {PERSIST_DIR}")
    return store


def load_index() -> Chroma:
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=str(PERSIST_DIR),
    )


if __name__ == "__main__":
    build_index()
