from typing import Dict, Any

from post_process.parser import parse_json
from post_process.layout.flatten import flatten_layout
from post_process.layout.features import extract_features
from post_process.layout.header_footer import remove_headers_footers
from post_process.layout.reading_order import reconstruct_reading_order
from post_process.classification.classifier import classify_blocks
from post_process.tree.builder import build_semantic_tree
from post_process.renderer.json_render import render_json

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

    json_path = "output/Anatomy of Neck - Basic of DEMN.pdf_origin/auto/Anatomy of Neck - Basic of DEMN.pdf_origin_content_list_v2.json"
    result = process_mineru_json(json_path)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

