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
PAGE_TRANSCRIPTION_PROMPT = (
    "Transcribe only the main body text on this page — headings, "
    "paragraphs, and bullet points — as clean Markdown, in natural "
    "reading order (top to bottom, left to right across columns). Use "
    "'#'/'##' etc. for headings based on visual prominence, and '-' for "
    "bullet points. "
    "Do not transcribe text that appears inside a photo, diagram, chart, "
    "or table graphic, and do not transcribe figure captions, legends, "
    "or labels describing those visuals — skip those regions entirely "
    "and do not describe them, they are handled separately. Do not add "
    "commentary, explanation, or content that is not literally printed "
    "on the page."
    "Stop immediately after the last visible text on the page. "
    "Do not continue beyond what is printed."
)
IMAGE_RELEVANCE_PROMPT = (
    "The following is the text content of a slide this image appears on:\n\n"
    "{slide_text}\n\n"
    "Does this image directly support or illustrate the slide's content "
    "(e.g. a diagram, chart, photo, or figure relevant to the subject "
    "matter), or is it purely decorative (e.g. a background, icon, logo, "
    "or stylistic element unrelated to the content)?\n\n"
    "Answer with exactly one word: SEMANTIC or DECORATIVE."
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

    def caption(self, image_path: str, prompt: str = DEFAULT_PROMPT, labels: str | None = None, max_new_tokens: int = 512) -> str:
        """labels: OCR'd text labels from MinerU2.5 (content_list "content"
        field), passed as grounding context for labeled diagrams so the
        model transcribes existing labels instead of re-reading the image
        from scratch."""
        if labels:
            prompt = (
                f"{prompt}\n\nThe following labels were extracted from "
                f"this image via OCR, in reading order (top to bottom may "
                f"not match visually, use the image to place each "
                f"correctly):\n{labels}"
            )
        return self._generate(image_path, prompt, max_new_tokens=max_new_tokens)

    def transcribe_page(self, image_path: str, prompt: str = PAGE_TRANSCRIPTION_PROMPT, max_new_tokens: int = 2048) -> str:
        """Transcribes body text from a full rendered PDF page image
        (Thread B of the dual-pipeline architecture). Visuals on the page
        are deliberately skipped — they're captioned separately from
        MinerU-extracted crops.

        max_pixels caps the image at ~1.28M px (1280x1000-ish) before
        tokenization — qwen_vl_utils' default max is 16384 vision tokens
        (~12.8M px), so an uncapped full-page render at 200 DPI (e.g.
        2667x1500 = ~4M px) burns thousands of vision tokens on prefill
        alone, which is what made this take hours across 68 pages on MPS
        (no flash-attention / optimized kernels there)."""
        return self._generate(image_path, prompt, max_new_tokens=max_new_tokens, max_pixels=1_280_000)

    def classify_image_relevance(self, img_path: str, slide_text: str) -> str:
        """Classifies whether an image is content-relevant or purely
        decorative, using the slide's text as context. Forces a
        single-word response (max_new_tokens=5) and a small max_pixels
        since this is a coarse classification, not a transcription."""
        prompt = IMAGE_RELEVANCE_PROMPT.format(slide_text=slide_text[:500])
        result = self._generate(img_path, prompt, max_new_tokens=5, max_pixels=480_000)
        return "decorative" if "DECORATIVE" in result.upper() else "semantic"

    def _generate(
        self,
        image_path: str,
        prompt: str,
        max_new_tokens: int,
        max_pixels: int | None = None,
    ) -> str:
        self.load()

        image_content = {"type": "image", "image": Image.open(image_path).convert("RGB")}
        if max_pixels is not None:
            image_content["max_pixels"] = max_pixels

        messages = [
            {
                "role": "user",
                "content": [
                    image_content,
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
            generated_ids = self.model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)

        generated_ids_trimmed = [
            out_ids[len(in_ids):]
            for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
        ]
        caption = self.processor.batch_decode(
            generated_ids_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
        )[0].strip()
        return caption
