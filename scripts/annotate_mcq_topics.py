"""Annotates notebooks/master_mcq_with_explanations.json with "subject" and "topic" keys,
derived from the lecture-slide chunk files under output/*/auto/*_chunks.json.

The MCQ exam-paket PDFs (212 files, e.g. "Copy of Paket A31.pdf") and the chunk source
lecture PDFs (16 files, e.g. "2025 04 29 THYROID.2025-dr. Imam.pdf") are completely
disjoint documents — no filename overlap, so topic/subject can't be looked up directly.
This does semantic matching instead: embed every chunk's text and every question's
stem+explanation with Qwen3-Embedding-8B (multilingual, handles the Bahasa Indonesia
question text much better than the MiniLM model src/ingestion/semantic_chunk.py uses for
chunking — that model is English-centric and this corpus isn't), and assign each question
the nearest chunk's subject.

Qwen3-Embedding is trained for asymmetric query/passage retrieval: questions are encoded
with the model's built-in "query" instruction prompt (sentence-transformers applies it via
prompt_name="query"), chunks are encoded as plain passages with no prefix — mixing this up
(or embedding both sides identically) measurably hurts retrieval quality for this model.

  subject: derived from the chunk's source doc_id, cleaned up into a human label
           (e.g. "2025 04 29 THYROID.2025-dr. Imam.pdf_origin" -> "Thyroid").
  topic:   a short topic label for the matched chunk itself, generated once per unique
           chunk (not per-question) via local Qwen3-8B text generation, since chunks carry
           no free-text topic label of their own — only section_path (mostly just the
           doc title, uninformative) and raw text.

Output goes to a new file (notebooks/master_mcq_with_topics.json), leaving
master_mcq_with_explanations.json untouched.

Usage (run from repo root as a module):
    .venv/bin/python -m scripts.annotate_mcq_topics
    .venv/bin/python -m scripts.annotate_mcq_topics --dry-run
"""

import argparse
import gc
import glob
import json
import logging
import re
from pathlib import Path

import numpy as np
import torch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("annotate_mcq_topics")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "notebooks" / "master_mcq_with_explanations.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "notebooks" / "master_mcq_with_topics.json"
CHUNKS_GLOB = str(PROJECT_ROOT / "output" / "**" / "*_chunks.json")

EMBED_MODEL_PATH = "Qwen/Qwen3-Embedding-8B"
TOPIC_MODEL_PATH = "Qwen/Qwen3-8B"

TOPIC_PROMPT = """Below is an excerpt from a medical school lecture on the subject "{subject}".

Excerpt:
{text}

In 2-6 words, name the specific topic this excerpt covers (e.g. "Thyroid hormone synthesis", "Carbohydrate digestion enzymes", "Parathyroid hormone regulation"). Answer with ONLY the topic name, no punctuation, no explanation."""

_JUNK_RE = re.compile(
    r"\.pdf_origin$|^\d{4}(\s+\d{2}){0,2}\s*[-.]?\s*|-\s*dr\.?\s*\w+.*$|\s*-\s*Basic of DEMN.*$|\.ppt.*$",
    re.IGNORECASE,
)


def _clean_subject(doc_id: str) -> str:
    """"2025 04 29 THYROID.2025-dr. Imam.pdf_origin" -> "Thyroid"
    "Anatomy of Endocrine Glands - Basic of DEMN.pdf_origin" -> "Anatomy of Endocrine Glands" """
    cleaned = _JUNK_RE.sub("", doc_id).strip(" .-_")
    cleaned = re.sub(r"\.\d+$", "", cleaned)  # trailing ".2025" left over from some titles
    cleaned = re.sub(r"[_.]+", " ", cleaned).strip()
    return cleaned.title() if cleaned.isupper() else cleaned


def load_chunks() -> list[dict]:
    """Flattens every output/*/auto/*_chunks.json into [{doc_id, chunk_id, text, subject}]."""
    flattened = []
    for path in sorted(glob.glob(CHUNKS_GLOB, recursive=True)):
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        subject = _clean_subject(doc["doc_id"])
        for chunk in doc["chunks"]:
            flattened.append({
                "doc_id": doc["doc_id"],
                "chunk_id": chunk["chunk_id"],
                "text": chunk["text"],
                "subject": subject,
            })
    return flattened


def embed_texts(texts: list[str], is_query: bool = False) -> np.ndarray:
    """is_query=True applies Qwen3-Embedding's built-in "query" instruction prompt (for the
    MCQ questions being matched); chunks/passages are embedded with no prefix, per the
    model's asymmetric retrieval training (see module docstring)."""
    from sentence_transformers import SentenceTransformer

    device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
    model = SentenceTransformer(EMBED_MODEL_PATH, device=device)
    encode_kwargs = {"prompt_name": "query"} if is_query else {}
    embeddings = model.encode(
        texts, convert_to_numpy=True, show_progress_bar=True, batch_size=8, **encode_kwargs
    )
    del model
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    elif torch.cuda.is_available():
        torch.cuda.empty_cache()
    return embeddings


def nearest_chunk_indices(question_embeddings: np.ndarray, chunk_embeddings: np.ndarray) -> np.ndarray:
    q_norm = question_embeddings / np.linalg.norm(question_embeddings, axis=1, keepdims=True)
    c_norm = chunk_embeddings / np.linalg.norm(chunk_embeddings, axis=1, keepdims=True)
    similarities = q_norm @ c_norm.T
    return np.argmax(similarities, axis=1)


class TopicLabeler:
    """Text-only Qwen3-8B wrapper for per-chunk topic naming — a plain causal LM (no
    vision tower), unlike src/captioning/qwen_vl.py's QwenVLCaptioner which wraps the
    image-only Qwen2.5-VL classes."""

    def __init__(self, model_path: str = TOPIC_MODEL_PATH):
        self.model_path = model_path
        self.device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
        self.model = None
        self.tokenizer = None

    def load(self):
        if self.model is not None:
            return
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_path)
        self.model = AutoModelForCausalLM.from_pretrained(self.model_path, torch_dtype=torch.bfloat16)
        self.model.to(self.device)
        self.model.eval()

    def unload(self):
        self.model = None
        self.tokenizer = None
        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        elif torch.cuda.is_available():
            torch.cuda.empty_cache()

    def label(self, subject: str, text: str, max_new_tokens: int = 20) -> str:
        self.load()
        prompt = TOPIC_PROMPT.format(subject=subject, text=text[:2000])
        messages = [{"role": "user", "content": prompt}]
        chat_text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
        inputs = self.tokenizer([chat_text], return_tensors="pt").to(self.device)

        with torch.inference_mode():
            generated_ids = self.model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        trimmed = [out[len(inp):] for inp, out in zip(inputs.input_ids, generated_ids)]
        result = self.tokenizer.batch_decode(trimmed, skip_special_tokens=True)[0]
        return result.strip().strip('"').strip(".")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--dry-run", action="store_true", help="load/embed/match but don't run the topic-labeling LLM or write output")
    args = ap.parse_args()

    chunks = load_chunks()
    log.info("loaded %d chunks from %d source documents", len(chunks), len({c['doc_id'] for c in chunks}))

    with open(args.input, encoding="utf-8") as f:
        questions = json.load(f)
    log.info("loaded %d questions from %s", len(questions), args.input)

    log.info("embedding chunks (passages, no instruction prefix)...")
    chunk_embeddings = embed_texts([c["text"] for c in chunks], is_query=False)

    log.info("embedding questions (query-prefixed)...")
    question_texts = [
        "\n\n".join(p for p in (q.get("background"), q.get("question"), q.get("explanation")) if p)
        for q in questions
    ]
    question_embeddings = embed_texts(question_texts, is_query=True)

    log.info("matching each question to its nearest chunk...")
    nearest = nearest_chunk_indices(question_embeddings, chunk_embeddings)

    if args.dry_run:
        from collections import Counter
        subject_counts = Counter(chunks[i]["subject"] for i in nearest)
        log.info("dry run — subject distribution: %s", dict(subject_counts))
        return

    labeler = TopicLabeler()
    topic_cache: dict[str, str] = {}
    for q, chunk_idx in zip(questions, nearest):
        chunk = chunks[chunk_idx]
        cache_key = f"{chunk['doc_id']}::{chunk['chunk_id']}"
        if cache_key not in topic_cache:
            topic_cache[cache_key] = labeler.label(chunk["subject"], chunk["text"])
            log.info("labeled chunk %s -> %r", cache_key, topic_cache[cache_key])
        q["subject"] = chunk["subject"]
        q["topic"] = topic_cache[cache_key]

    labeler.unload()

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)
    log.info("wrote %d annotated questions to %s (%d unique topics labeled)",
              len(questions), args.output, len(topic_cache))


if __name__ == "__main__":
    main()
