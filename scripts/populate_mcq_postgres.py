"""Loads notebooks/master_mcq_with_topics.json (flat, A-E keyed) into a flat
`mcq_questions` table in the postgres instance defined by docker-compose.yml
(the same POSTGRES_* creds mlflow's backend store uses).

uid/topic_tag/options normalization mirrors scripts/import_master_mcq.py so both
scripts treat the raw data identically -- this one just lands it in postgres
instead of the nested question_bank.json src/quiz/bank.py serves.

Usage:
    .venv/bin/python scripts/populate_mcq_postgres.py
    .venv/bin/python scripts/populate_mcq_postgres.py --input notebooks/master_mcq_with_topics.json
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("populate_mcq_postgres")

DOC_ID = "master_mcq"
FALLBACK_TOPIC_TAG = ["Master MCQ"]  # used only if subject/topic are missing on a question

_SLUG_RE = re.compile(r"[^a-z0-9]+")

DDL = """
CREATE TABLE IF NOT EXISTS mcq_questions (
    uid              TEXT PRIMARY KEY,
    doc_id           TEXT NOT NULL,
    subject          TEXT,
    topic            TEXT,
    stem             TEXT NOT NULL,
    options          JSONB NOT NULL,
    correct_index    INTEGER NOT NULL,
    difficulty       INTEGER NOT NULL DEFAULT 1,
    explanation      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_mcq_questions_subject_topic
    ON mcq_questions (subject, topic);
"""

UPSERT = """
INSERT INTO mcq_questions
    (uid, doc_id, subject, topic, stem, options, correct_index, difficulty, explanation)
VALUES
    (%(uid)s, %(doc_id)s, %(subject)s, %(topic)s, %(stem)s, %(options)s,
     %(correct_index)s, %(difficulty)s, %(explanation)s)
ON CONFLICT (uid) DO UPDATE SET
    doc_id = EXCLUDED.doc_id,
    subject = EXCLUDED.subject,
    topic = EXCLUDED.topic,
    stem = EXCLUDED.stem,
    options = EXCLUDED.options,
    correct_index = EXCLUDED.correct_index,
    difficulty = EXCLUDED.difficulty,
    explanation = EXCLUDED.explanation;
"""


def _slug(text: str) -> str:
    return _SLUG_RE.sub("_", text.lower()).strip("_")


def build_rows(raw_questions: list[dict]) -> list[dict]:
    rows = []
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

        uid = f"{DOC_ID}::{_slug(filename)}::{number:04d}"
        if uid in seen_uids:
            skipped += 1
            log.warning("skipping duplicate uid=%s", uid)
            continue
        seen_uids.add(uid)

        subject = (rq.get("subject") or "").strip()
        topic = (rq.get("topic") or "").strip()
        topic_tag = [subject, topic] if subject and topic else FALLBACK_TOPIC_TAG

        option_keys = list(options.keys())
        correct_index = option_keys.index(answer)

        rows.append({
            "uid": uid,
            "doc_id": DOC_ID,
            "subject": topic_tag[0] if len(topic_tag) > 1 else None,
            "topic": topic_tag[1] if len(topic_tag) > 1 else None,
            "stem": "\n\n".join(stem_parts),
            "options": json.dumps([
                {"text": text, "correct": key == answer}
                for key, text in options.items()
            ]),
            "correct_index": correct_index,
            "difficulty": 1,
            "explanation": explanation,
        })

    log.info("built %d rows (%d skipped)", len(rows), skipped)
    return rows


def dsn_from_env() -> str:
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    db = os.environ["POSTGRES_DB"]
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--input",
        default=str(PROJECT_ROOT / "notebooks" / "master_mcq_with_topics.json"),
    )
    ap.add_argument("--dsn", default=None, help="override DSN instead of building from .env")
    args = ap.parse_args(argv)

    with open(args.input, encoding="utf-8") as f:
        raw_questions = json.load(f)
    log.info("read %d raw questions from %s", len(raw_questions), args.input)

    rows = build_rows(raw_questions)

    dsn = args.dsn or dsn_from_env()
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            cur.executemany(UPSERT, rows)
        conn.commit()

    log.info("upserted %d rows into mcq_questions", len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
