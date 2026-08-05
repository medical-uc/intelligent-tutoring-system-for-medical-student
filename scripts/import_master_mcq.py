"""Convert notebooks/master_mcq_with_topics.json (flat, A-E keyed) into the nested
{doc_id: {topic_path: [question, ...]}} shape src/quiz/bank.py serves.

`subject`/`topic` (see scripts/annotate_mcq_topics.py -- semantic-matched against the
lecture-slide chunk files, since the exam-paket source PDFs carry no topic taxonomy of
their own) become topic_tag = [subject, topic], e.g.
["Anatomy of Endocrine Glands", "IgA secretion mechanism"], replacing the old flat
"Master MCQ" placeholder bucket every question used to share. This is what makes
bank.topics()/the /quiz/topics endpoint list real subjects instead of one bucket, and
gives the Neo4j Topic/:SUB_TOPIC_OF tree (built per-attempt in src/quiz/attempts.py) an
actual two-level hierarchy to work with.

`has_image` items (217/955) carry no image asset in the source data, so the flag is
dropped rather than wired to a field the client can't render.

`explanation` is carried straight through -- shown to the student only after they answer
(see app/routers/quiz.py's /check endpoint and src/flashcards/cards.py), never alongside
the question itself.

Usage:
    .venv/bin/python scripts/import_master_mcq.py
    .venv/bin/python scripts/import_master_mcq.py --input notebooks/master_mcq_with_topics.json \\
        --output notebooks/mcq_output/question_bank.json
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("import_master_mcq")

DOC_ID = "master_mcq"
FALLBACK_TOPIC_TAG = ["Master MCQ"]  # used only if subject/topic are missing on a question

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("_", text.lower()).strip("_")


def convert(raw_questions: list[dict]) -> dict:
    by_topic_path: dict[str, list[dict]] = {}
    skipped = 0
    seen_uids: set[str] = set()

    for rq in raw_questions:
        number = rq.get("number")
        filename = rq.get("filename")
        options = rq.get("options") or {}
        answer = rq.get("answer")
        explanation = (rq.get("explanation") or "").strip()
        stem_parts = [p for p in (rq.get("background"), rq.get("question")) if p]

        if (
            number is None
            or not filename
            or not options
            or answer not in options
            or not stem_parts
        ):
            skipped += 1
            log.warning(
                "skipping question number=%s filename=%r: missing "
                "number/filename/options/answer/stem",
                number,
                filename,
            )
            continue

        # `number` alone resets per exam-paket (only 12 distinct values across
        # 955 questions); filename+number is the actual near-unique key.
        uid = f"{DOC_ID}::{_slug(filename)}::{number:04d}"
        if uid in seen_uids:
            skipped += 1
            log.warning("skipping duplicate uid=%s", uid)
            continue
        seen_uids.add(uid)

        subject = (rq.get("subject") or "").strip()
        topic = (rq.get("topic") or "").strip()
        topic_tag = [subject, topic] if subject and topic else FALLBACK_TOPIC_TAG
        topic_path = " > ".join(topic_tag)

        by_topic_path.setdefault(topic_path, []).append({
            "uid": uid,
            "stem": "\n\n".join(stem_parts),
            "options": [
                {"text": text, "correct": key == answer}
                for key, text in options.items()
            ],
            "topic_tag": topic_tag,
            "difficulty": 1,
            "explanation": explanation,
        })

    n_questions = sum(len(qs) for qs in by_topic_path.values())
    log.info("converted %d questions across %d topics (%d skipped)",
              n_questions, len(by_topic_path), skipped)
    return {DOC_ID: by_topic_path}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--input",
        default=str(PROJECT_ROOT / "notebooks" / "master_mcq_with_topics.json"),
    )
    ap.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "notebooks" / "mcq_output" / "question_bank.json"),
    )
    args = ap.parse_args(argv)

    with open(args.input, encoding="utf-8") as f:
        raw_questions = json.load(f)
    log.info("read %d raw questions from %s", len(raw_questions), args.input)

    bank = convert(raw_questions)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bank, f, ensure_ascii=False, indent=2)
    log.info("wrote %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
