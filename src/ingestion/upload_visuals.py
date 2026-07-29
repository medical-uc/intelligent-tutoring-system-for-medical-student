"""Uploads filtered semantic-visual crops to the MinIO `semantic-images`
bucket, reading `{pdf_stem}_v1_captions.json` written by ingest_data.py
(already filtered to relevance == "semantic" — see ingest_data.py:86).

Object key: {pdf_stem}/{item_id}{ext}
Object metadata: content_type, caption (truncated to 2KB — S3 metadata
values are header-encoded and have a ~2KB practical limit).

Usage (needs the minio stack up: `make minio-up`):
    .venv/bin/python src/ingestion/upload_visuals.py --dir output/
    .venv/bin/python src/ingestion/upload_visuals.py --captions output/{pdf_stem}/auto/{pdf_stem}_v1_captions.json
"""

import argparse
import json
import logging
import os
from pathlib import Path

import boto3
from botocore.client import Config

PROJECT_ROOT = Path(__file__).resolve().parents[2]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("upload_visuals")

MAX_METADATA_CAPTION_CHARS = 2000


def _header_safe(text: str) -> str:
    """S3 object metadata is sent as raw HTTP headers: no newlines/control
    chars, and non-ASCII bytes break botocore's header encoding too."""
    text = " ".join(text.split())
    return text.encode("ascii", "ignore").decode("ascii")


def make_client():
    endpoint = os.environ.get("MLFLOW_S3_ENDPOINT_URL", "http://localhost:9000")
    access_key = os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("MINIO_ROOT_USER", "minioadmin")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY") or os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
    )


def upload_captions_file(client, bucket: str, captions_path: Path) -> int:
    pdf_stem = captions_path.name.removesuffix("_v1_captions.json")
    with open(captions_path) as f:
        captions = json.load(f)

    uploaded = 0
    for entry in captions:
        image_path = Path(entry["image_path"])
        if not image_path.exists():
            log.warning("[%s] missing image, skipping: %s", pdf_stem, image_path)
            continue

        key = f"{pdf_stem}/{entry['item_id']}{image_path.suffix}"
        caption = entry.get("caption") or ""
        client.upload_file(
            str(image_path),
            bucket,
            key,
            ExtraArgs={
                "Metadata": {
                    "content_type": entry.get("content_type", ""),
                    "caption": _header_safe(caption)[:MAX_METADATA_CAPTION_CHARS],
                },
            },
        )
        uploaded += 1

    log.info("[%s] uploaded %d/%d semantic visuals -> s3://%s/%s/", pdf_stem, uploaded, len(captions), bucket, pdf_stem)
    return uploaded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--captions", type=Path, help="Path to a single {pdf_stem}_v1_captions.json.")
    group.add_argument("--dir", type=Path, help="Root dir to search recursively for *_v1_captions.json.")
    parser.add_argument("--bucket", default=os.environ.get("SEMANTIC_IMAGES_BUCKET", "semantic-images"))
    args = parser.parse_args()

    if args.captions:
        assert args.captions.exists(), args.captions
        captions_paths = [args.captions]
    else:
        assert args.dir.is_dir(), args.dir
        captions_paths = sorted(args.dir.rglob("*_v1_captions.json"))
        assert captions_paths, f"No *_v1_captions.json found under {args.dir}"

    client = make_client()
    total = 0
    for captions_path in captions_paths:
        total += upload_captions_file(client, args.bucket, captions_path)

    log.info("Done: %d visuals uploaded across %d document(s)", total, len(captions_paths))


if __name__ == "__main__":
    main()
