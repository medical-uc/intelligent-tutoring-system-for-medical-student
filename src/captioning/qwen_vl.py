"""Qwen2.5-VL captioner: single model for all MinerU2.5-extracted visuals
(photos, diagrams, charts, tables).

Loaded lazily and released after use to keep the memory footprint down on
24GB unified memory (MPS backend).

Uses bfloat16 (not float16) and loads to CPU before moving to MPS: torch's
MPS backend has recurring, unresolved SIGSEGVs in its fp16 cast kernel
(pytorch/pytorch#95409, #96113) and in device_map-based loading on MPS
(huggingface/transformers#36413, #41908) as of torch 2.13. This sidesteps
both known crash paths.
"""

import gc
import os

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import torch
from PIL import Image
from qwen_vl_utils import process_vision_info
from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

MODEL_PATH = "Qwen/Qwen2.5-VL-7B-Instruct"
DEFAULT_PROMPT = (
    "Describe strictly what is visually present in this image, in detail. "
    "For any text or labels, transcribe them exactly and describe where "
    "each one is located in the image (e.g. top-left, pointing to the "
    "center-right region) and, if a leader line or arrow connects it to a "
    "part of the image, what it points to. For diagrams, describe shapes, "
    "regions, and their spatial layout as drawn. For tables, describe the "
    "rows and columns as they visually appear. "
    "Do not explain, interpret, or add outside knowledge about what any "
    "label or structure means or is used for — describe only what is "
    "visible in the image itself."
)


def get_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class QwenVLCaptioner:
    def __init__(self, model_path: str = MODEL_PATH):
        self.model_path = model_path
        self.device = get_device()
        self.model = None
        self.processor = None

    def load(self):
        if self.model is not None:
            return
        self.processor = AutoProcessor.from_pretrained(self.model_path)
        self.model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            self.model_path,
            torch_dtype=torch.bfloat16,
        )
        self.model.to(self.device)
        self.model.eval()

    def unload(self):
        self.model = None
        self.processor = None
        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        elif torch.cuda.is_available():
            torch.cuda.empty_cache()

    def caption(self, image_path: str, prompt: str = DEFAULT_PROMPT, labels: str | None = None) -> str:
        """labels: OCR'd text labels from MinerU2.5 (content_list "content"
        field), passed as grounding context for labeled diagrams so the
        model transcribes existing labels instead of re-reading the image
        from scratch."""
        self.load()

        if labels:
            prompt = (
                f"{prompt}\n\nThe following labels were extracted from "
                f"this image via OCR, in reading order (top to bottom may "
                f"not match visually, use the image to place each "
                f"correctly):\n{labels}"
            )

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": Image.open(image_path).convert("RGB")},
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        text = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = self.processor(
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        )
        inputs = inputs.to(self.device)
        if "pixel_values" in inputs:
            inputs["pixel_values"] = inputs["pixel_values"].to(torch.bfloat16)

        with torch.inference_mode():
            generated_ids = self.model.generate(**inputs, max_new_tokens=512, do_sample=False)

        generated_ids_trimmed = [
            out_ids[len(in_ids):]
            for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
        ]
        caption = self.processor.batch_decode(
            generated_ids_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
        )[0].strip()
        return caption
