"""
Standalone captioning script executed with vendor/LLaVA-Med/.venv's interpreter
(NOT the main project venv) via subprocess, since LLaVA-Med pins
transformers==4.36.2 which conflicts with the main env's transformers.

Usage: python llava_med_runner.py <image_path> [--prompt "..."]
Prints the caption to stdout as the last line.
"""

import argparse
import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).parent / "LLaVA-Med"
sys.path.insert(0, str(VENDOR_DIR))

import torch
from PIL import Image

from llava.constants import IMAGE_TOKEN_INDEX, DEFAULT_IMAGE_TOKEN
from llava.conversation import conv_templates
from llava.model.builder import load_pretrained_model
from llava.mm_utils import tokenizer_image_token, process_images

MODEL_PATH = "microsoft/llava-med-v1.5-mistral-7b"
DEFAULT_PROMPT = (
    "Describe this medical image in detail, including any visible "
    "anatomical structures, abnormalities, or clinical findings."
)


def get_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def caption_image(image_path: str, prompt: str = DEFAULT_PROMPT) -> str:
    device = get_device()
    tokenizer, model, image_processor, _ = load_pretrained_model(
        model_path=MODEL_PATH,
        model_base=None,
        model_name="llava-med-v1.5-mistral-7b",
        device=device,
    )
    model.eval()

    image = Image.open(image_path).convert("RGB")
    image_tensor = process_images([image], image_processor, model.config)
    image_tensor = image_tensor.to(device=device, dtype=torch.float16)

    conv = conv_templates["mistral_instruct"].copy()
    full_prompt = DEFAULT_IMAGE_TOKEN + "\n" + prompt
    conv.append_message(conv.roles[0], full_prompt)
    conv.append_message(conv.roles[1], None)
    prompt_text = conv.get_prompt()

    input_ids = tokenizer_image_token(
        prompt_text, tokenizer, IMAGE_TOKEN_INDEX, return_tensors="pt"
    ).unsqueeze(0).to(device)
    attention_mask = torch.ones_like(input_ids)

    with torch.inference_mode():
        output_ids = model.generate(
            input_ids,
            attention_mask=attention_mask,
            images=image_tensor,
            image_sizes=[image.size],
            do_sample=False,
            max_new_tokens=512,
            use_cache=True,
            pad_token_id=tokenizer.eos_token_id,
        )

    caption = tokenizer.batch_decode(output_ids, skip_special_tokens=True)[0].strip()
    return caption


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("image_path")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    args = parser.parse_args()

    result = caption_image(args.image_path, args.prompt)
    print(result)
