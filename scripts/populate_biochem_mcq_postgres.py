"""Converts the KG-generated biochem MCQs (src/domain_kg/data/docs/*/questions.json,
produced by src/domain_kg/mcq.py -- untouched by this script) into rows for the
`mcq_questions` postgres table src/quiz/bank.py serves questions from.

Also deletes any doc_id='master_mcq' rows before upserting -- the quiz bank is
biochem-only now, so scripts/populate_mcq_postgres.py (the old master_mcq/anatomy
dataset loader) should no longer be run. This script is the only populator to use.

Source shape per chapter (src.domain_kg.mcq.Item, list[dict]):
    item_id, stem, options: list[str], answer: str (must equal one options[] entry),
    rationale, citation ("{doc_id} — {Subject} > {Topic...}"), relation, confidence, ...
-- no `correct` bool, no subject/topic columns, no difficulty. This script derives:

  uid:        f"{chapter_dir}::{item_id}", distinct from the "master_mcq::" namespace
              populate_mcq_postgres.py uses, so the two datasets never collide and
              re-running after regenerating a chapter's questions.json upserts in
              place (ON CONFLICT uid) instead of duplicating.
  subject:    the citation's first section segment after "{doc_id} — "
              (e.g. "The Biochemical Roles of Transition Metals", "Overview") --
              this is the book's own top-level section name, not the chapter dir,
              so identically named sections in different chapters (many chapters
              have an "Overview") intentionally merge under one bank.subjects()
              entry rather than being kept apart by chapter.
  topic:      the remaining citation segments joined by " > " (e.g.
              "BIOMEDICAL IMPORTANCE > Humans Require Minute Quantities...");
              falls back to "General" when the citation has no segment beyond
              the subject.
  options / correct_index: `answer` is matched against options[] by exact string
              equality (the mcq.py contract) to find the correct index.
  difficulty: not produced by mcq.py -- defaults to 1, same as master_mcq rows.
  explanation: mcq.py's `rationale` (the verbatim source sentence).

Usage:
    .venv/bin/python scripts/populate_biochem_mcq_postgres.py
    .venv/bin/python scripts/populate_biochem_mcq_postgres.py --docs-dir src/domain_kg/data/docs
    .venv/bin/python scripts/populate_biochem_mcq_postgres.py --dry-run
    .venv/bin/python scripts/populate_biochem_mcq_postgres.py --keep-master-mcq
"""

import argparse
import json
import logging
import os
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
log = logging.getLogger("populate_biochem_mcq_postgres")

DEFAULT_DOCS_DIR = PROJECT_ROOT / "src" / "domain_kg" / "data" / "docs"

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

DELETE_MASTER_MCQ = "DELETE FROM mcq_questions WHERE doc_id = 'master_mcq';"

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


def parse_citation(citation: str) -> tuple[str, str]:
    """"{doc_id} — Subject > Topic > Leaf" -> ("Subject", "Topic > Leaf").

    "{doc_id} — Subject" (no topic segment) -> ("Subject", "General").
    Empty/malformed citation -> ("General", "General").
    """
    _, _, rest = (citation or "").partition("—")
    segments = [s.strip() for s in rest.strip().split(">") if s.strip()]
    if not segments:
        return "General", "General"
    subject = segments[0]
    topic = " > ".join(segments[1:]) if len(segments) > 1 else "General"
    return subject, topic


def convert_chapter(doc_id: str, items: list[dict]) -> tuple[list[dict], int]:
    rows, skipped = [], 0
    seen_uids: set[str] = set()

    for item in items:
        item_id = item.get("item_id")
        stem = item.get("stem")
        options = item.get("options") or []
        answer = item.get("answer")

        if not item_id or not stem or not options or answer not in options:
            skipped += 1
            log.warning(
                "skipping item_id=%s in %s: missing item_id/stem/options/answer",
                item_id, doc_id,
            )
            continue

        uid = f"{doc_id}::{item_id}"
        if uid in seen_uids:
            skipped += 1
            log.warning("skipping duplicate uid=%s", uid)
            continue
        seen_uids.add(uid)

        subject, topic = parse_citation(item.get("citation", ""))
        correct_index = options.index(answer)
        rows.append({
            "uid": uid,
            "doc_id": doc_id,
            "subject": subject,
            "topic": topic,
            "stem": stem,
            "options": json.dumps([
                {"text": opt, "correct": i == correct_index}
                for i, opt in enumerate(options)
            ]),
            "correct_index": correct_index,
            "difficulty": 1,
            "explanation": (item.get("rationale") or "").strip(),
        })

    return rows, skipped


def build_rows(docs_dir: Path) -> list[dict]:
    rows = []
    total_skipped = 0
    n_chapters = 0
    for chapter_dir in sorted(docs_dir.iterdir()):
        if not chapter_dir.is_dir():
            continue
        qfile = chapter_dir / "questions.json"
        if not qfile.exists():
            continue
        n_chapters += 1
        with open(qfile, encoding="utf-8") as f:
            items = json.load(f)
        chapter_rows, skipped = convert_chapter(chapter_dir.name, items)
        rows.extend(chapter_rows)
        total_skipped += skipped

    log.info("converted %d rows across %d chapter(s) (%d skipped)",
              len(rows), n_chapters, total_skipped)
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
    ap.add_argument("--docs-dir", default=str(DEFAULT_DOCS_DIR))
    ap.add_argument("--dsn", default=None, help="override DSN instead of building from .env")
    ap.add_argument(
        "--dry-run", action="store_true",
        help="convert and log counts only, skip the postgres upsert/delete",
    )
    ap.add_argument(
        "--keep-master-mcq", action="store_true",
        help="don't delete doc_id='master_mcq' rows (default: delete them, "
             "since the quiz bank is biochem-only)",
    )
    args = ap.parse_args(argv)

    docs_dir = Path(args.docs_dir)
    rows = build_rows(docs_dir)
    if not rows:
        log.warning("no rows built, nothing to upsert")
        return 0
    if args.dry_run:
        log.info("dry run: would upsert %d rows%s", len(rows),
                  "" if args.keep_master_mcq else " and delete doc_id='master_mcq' rows")
        return 0

    dsn = args.dsn or dsn_from_env()
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            if not args.keep_master_mcq:
                cur.execute(DELETE_MASTER_MCQ)
                log.info("deleted %d doc_id='master_mcq' row(s)", cur.rowcount)
            cur.executemany(UPSERT, rows)
        conn.commit()

    log.info("upserted %d rows into mcq_questions", len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
