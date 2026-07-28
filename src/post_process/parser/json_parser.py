import json
import os
from typing import Dict, Any, List

def parse_json(json_path: str) -> List[Dict[str, Any]]:
    """
    Stage 1 — Parse JSON
    Load MinerU middle JSON and return raw page objects.
    Responsibilities:
    - Load pages
    - Preserve original ordering
    - Validate schema
    """
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"File not found: {json_path}")

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("Invalid MinerU JSON: root structure must be a JSON object.")

    pdf_info = data.get("pdf_info")
    if pdf_info is None or not isinstance(pdf_info, list):
        raise ValueError("Invalid MinerU JSON: missing or invalid 'pdf_info' array.")

    pages = []
    for idx, page_data in enumerate(pdf_info):
        if not isinstance(page_data, dict):
            continue
        page_dict = dict(page_data)
        if "page_idx" not in page_dict:
            page_dict["page_idx"] = idx
        pages.append(page_dict)

    return pages
