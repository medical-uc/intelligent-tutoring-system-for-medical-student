"""
Reference-free quality metrics for MinerU extraction output.

No ground truth is required: metrics are derived from MinerU's own
per-block/per-span confidence scores, content shape, and simple text
statistics (garbage-char ratio, repetition ratio, coverage).

Usage:
    python -m src.evaluation.mineru_quality output/**/auto/*_middle.json
    python -m src.evaluation.mineru_quality output/ --recursive
"""

import argparse
import glob
import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

LOW_SCORE_THRESHOLD = 0.7
GARBAGE_CHAR_RE = re.compile(
    r"[^\w\s.,;:!?()\-'\"/%°²³<>{}\[\]\\&*•▪–—→←↑↓≤≥±×÷≈≠]",
    re.UNICODE,
)
REPEAT_RUN_RE = re.compile(r"(.)\1{4,}")


def _iter_spans(block: Dict[str, Any]):
    for line in block.get("lines", []) or []:
        for span in line.get("spans", []) or []:
            yield span


def _block_text(block: Dict[str, Any]) -> str:
    return "".join(span.get("content", "") for span in _iter_spans(block))


@dataclass
class PageMetrics:
    page_idx: int
    n_blocks: int
    n_empty_blocks: int
    n_low_conf_blocks: int
    min_block_score: float
    mean_block_score: float
    char_count: int
    garbage_ratio: float
    repeat_run_count: int
    block_type_counts: Dict[str, int] = field(default_factory=dict)
    low_conf_block_types: List[str] = field(default_factory=list)


@dataclass
class DocMetrics:
    source: str
    n_pages: int
    n_blocks: int
    n_empty_blocks: int
    n_low_conf_blocks: int
    mean_block_score: float
    min_block_score: float
    mean_garbage_ratio: float
    total_repeat_runs: int
    worst_pages: List[int]
    pages: List[PageMetrics]


def score_page(page: Dict[str, Any]) -> PageMetrics:
    blocks = page.get("para_blocks", []) or []
    scores: List[float] = []
    empty = 0
    low_conf = 0
    low_conf_types: List[str] = []
    type_counts: Counter = Counter()
    char_count = 0
    garbage_chars = 0
    repeat_runs = 0

    for b in blocks:
        btype = b.get("type", "?")
        type_counts[btype] += 1

        score = b.get("score")
        if score is not None:
            scores.append(score)
            if score < LOW_SCORE_THRESHOLD:
                low_conf += 1
                low_conf_types.append(btype)

        text = _block_text(b)
        if btype not in ("image", "chart") and not text.strip():
            empty += 1

        char_count += len(text)
        garbage_chars += len(GARBAGE_CHAR_RE.findall(text))
        repeat_runs += len(REPEAT_RUN_RE.findall(text))

    garbage_ratio = (garbage_chars / char_count) if char_count else 0.0

    return PageMetrics(
        page_idx=page.get("page_idx", -1),
        n_blocks=len(blocks),
        n_empty_blocks=empty,
        n_low_conf_blocks=low_conf,
        min_block_score=min(scores) if scores else 1.0,
        mean_block_score=(sum(scores) / len(scores)) if scores else 1.0,
        char_count=char_count,
        garbage_ratio=garbage_ratio,
        repeat_run_count=repeat_runs,
        block_type_counts=dict(type_counts),
        low_conf_block_types=low_conf_types,
    )


def score_document(middle_json_path: str) -> DocMetrics:
    with open(middle_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    pages_raw = data.get("pdf_info", [])
    pages = [score_page(p) for p in pages_raw]

    all_scores = [p.mean_block_score for p in pages if p.n_blocks]
    ranked = sorted(pages, key=lambda p: (p.mean_block_score, -p.garbage_ratio))
    worst_pages = [p.page_idx for p in ranked[:5]]

    return DocMetrics(
        source=middle_json_path,
        n_pages=len(pages),
        n_blocks=sum(p.n_blocks for p in pages),
        n_empty_blocks=sum(p.n_empty_blocks for p in pages),
        n_low_conf_blocks=sum(p.n_low_conf_blocks for p in pages),
        mean_block_score=(sum(all_scores) / len(all_scores)) if all_scores else 1.0,
        min_block_score=min((p.min_block_score for p in pages), default=1.0),
        mean_garbage_ratio=(sum(p.garbage_ratio for p in pages) / len(pages)) if pages else 0.0,
        total_repeat_runs=sum(p.repeat_run_count for p in pages),
        worst_pages=worst_pages,
        pages=pages,
    )


def write_report(doc: DocMetrics, out_path: str) -> None:
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(asdict(doc), f, indent=2)


def _report_path_for(middle_json_path: str) -> str:
    base = middle_json_path
    if base.endswith("_middle.json"):
        base = base[: -len("_middle.json")]
    return f"{base}_quality_report.json"


def print_summary(doc: DocMetrics) -> None:
    print(f"\n{doc.source}")
    print(f"  pages={doc.n_pages} blocks={doc.n_blocks} empty={doc.n_empty_blocks} "
          f"low_conf={doc.n_low_conf_blocks} (thr={LOW_SCORE_THRESHOLD})")
    print(f"  mean_block_score={doc.mean_block_score:.4f} min_block_score={doc.min_block_score:.4f}")
    print(f"  mean_garbage_ratio={doc.mean_garbage_ratio:.4%} total_repeat_runs={doc.total_repeat_runs}")
    print(f"  worst_pages(by score)={doc.worst_pages}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", help="_middle.json files, dirs, or globs")
    ap.add_argument("--recursive", action="store_true", help="recurse into dirs for *_middle.json")
    ap.add_argument("--no-report", action="store_true", help="skip writing *_quality_report.json")
    args = ap.parse_args()

    targets: List[str] = []
    for p in args.paths:
        if os.path.isdir(p):
            pattern = "**/*_middle.json" if args.recursive else "*_middle.json"
            targets.extend(glob.glob(os.path.join(p, pattern), recursive=args.recursive))
        else:
            targets.extend(glob.glob(p, recursive=True))

    if not targets:
        print("No *_middle.json files found.")
        return

    for path in sorted(set(targets)):
        doc = score_document(path)
        print_summary(doc)
        if not args.no_report:
            out_path = _report_path_for(path)
            write_report(doc, out_path)
            print(f"  report -> {out_path}")


if __name__ == "__main__":
    main()
