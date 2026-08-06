import os
from typing import Dict, Any
from PIL import Image
import mlx_vlm
from mlx_vlm import load, generate
from mlx_vlm.prompt_utils import apply_chat_template

DEFAULT_MODEL_PATH = "mlx-community/medgemma-4b-it-4bit"

PROMPT = """You are a precision medical visual document analyzer.

CLASSIFY THE IMAGE INTO EXACTLY ONE OF THESE TWO CATEGORIES:
1. "labeled diagram": Anatomical drawings, tissue structures, organ schemata, cellular models, or physical figures WITH text labels or callout lines pointing directly to physical structural parts or anatomical structures.
2. "not labeled diagram": Any image that is NOT a labeled diagram.

CRITICAL MANDATORY RULE:
- IF THERE IS TEXT POINTING TO TEXT (such as metabolic pathways, chemical reaction chains, enzyme reaction steps, process boxes, or flowcharts connected by arrows), IT IS ALWAYS A FLOWCHART AND MUST BE CLASSIFIED AS "not labeled diagram".
- Text pointing to text = "not labeled diagram" (flowchart).
- Pointer lines pointing from text to physical anatomical/structural parts of a drawing or photo = "labeled diagram".

KEY DECISION RULE:
- Does this image feature text pointing to text (e.g., pathway names, chemical steps, reaction arrows)?
  -> YES: "not labeled diagram"
- Do pointer lines point from text to physical parts of an anatomical drawing or photo?
  -> YES: "labeled diagram"
  -> NO: "not labeled diagram"

Response: Output ONLY either "labeled diagram" or "not labeled diagram"."""


def parse_category(raw_text: str) -> str:
    """
    Parses VLM raw output string into standardized binary categories:
    'labeled diagram' or 'not labeled diagram'.
    """
    raw = raw_text.strip().lower()
    if any(k in raw for k in ["not labeled diagram", "not labeled", "unlabeled", "flowchart", "pathway", "reaction"]):
        return "not labeled diagram"
    if "labeled diagram" in raw or "labeled" in raw:
        return "labeled diagram"
    if "diagram" in raw and not any(k in raw for k in ["not", "unlabeled", "no"]):
        return "labeled diagram"
    return "not labeled diagram"


class MedGemmaClassifier:
    """
    VLM Classifier using MedGemma 4B (4-bit quantized) via MLX-VLM.
    """
    def __init__(self, model_path: str = DEFAULT_MODEL_PATH):
        self.model_path = model_path
        self.model = None
        self.processor = None
        self.formatted_prompt = None

    def _ensure_loaded(self):
        if self.model is None:
            print(f"Loading 4-bit quantized MedGemma VLM model from '{self.model_path}'...")
            self.model, self.processor = load(self.model_path)
            self.formatted_prompt = apply_chat_template(
                self.processor,
                config=self.model.config,
                prompt=PROMPT,
                num_images=1
            )
            print("MedGemma VLM loaded successfully.")

    def classify_image(self, image_path: str) -> Dict[str, Any]:
        """
        Classifies an input image file as 'labeled diagram' or 'not labeled diagram'.
        Returns detailed result dict including width, height, raw output, parsed category, and flags.
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        with Image.open(image_path) as img:
            w, h = img.size

        # Check micro dimensions threshold (<35px)
        if w < 35 or h < 35:
            return {
                "category": "not labeled diagram",
                "raw_output": "Micro-dimensions (<35px)",
                "width": w,
                "height": h,
                "is_micro_dimension": True
            }

        self._ensure_loaded()
        out_obj = generate(
            self.model,
            self.processor,
            self.formatted_prompt,
            image=image_path,
            verbose=False,
            max_tokens=32
        )
        raw_out = out_obj.text if hasattr(out_obj, "text") else str(out_obj)
        cat = parse_category(raw_out)

        return {
            "category": cat,
            "raw_output": raw_out.strip(),
            "width": w,
            "height": h,
            "is_micro_dimension": False
        }
