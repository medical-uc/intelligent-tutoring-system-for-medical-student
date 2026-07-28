from typing import Dict, Any

from .parser import parse_json
from .layout.flatten import flatten_layout
from .layout.features import extract_features
from .layout.header_footer import remove_headers_footers
from .layout.reading_order import reconstruct_reading_order
from .classification.classifier import classify_blocks
from .tree.builder import build_semantic_tree
from .renderer.json_render import render_json

def process_mineru_json(json_path: str, confidence_threshold: float = 0.7) -> Dict[str, Any]:
    """
    Executes the full 8-stage pipeline converting MinerU JSON to a structured JSON object.
    """
    # Stage 1: Parse JSON
    pages = parse_json(json_path)

    # Stage 2: Flatten Layout Tree
    blocks = flatten_layout(pages)

    # Stage 3: Feature Extraction
    blocks_with_features = extract_features(blocks, pages)

    # Stage 4: Header/Footer Detection
    content_blocks = remove_headers_footers(blocks_with_features)

    # Stage 5: Block Classification
    classified_blocks = classify_blocks(content_blocks, confidence_threshold=confidence_threshold)

    # Stage 6: Reading Order Reconstruction
    ordered_blocks = reconstruct_reading_order(classified_blocks)

    # Stage 7: Semantic Tree Construction
    semantic_tree = build_semantic_tree(ordered_blocks)

    # Stage 8: Structured JSON Rendering
    json_output = render_json(semantic_tree, total_pages=len(pages))

    return json_output


def main():
    import json
    from pathlib import Path

    json_path = Path("output/Anatomy of Neck - Basic of DEMN.pdf_origin/auto/Anatomy of Neck - Basic of DEMN.pdf_origin_middle.json")
    result = process_mineru_json(str(json_path))

    output_path = json_path.with_name(json_path.stem.replace("_middle", "") + "_post_processed.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()

