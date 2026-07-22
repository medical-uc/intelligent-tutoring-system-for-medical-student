# vendor/

Third-party code vendored as git checkouts (not pip packages) due to
dependency conflicts with the main project environment.

## LLaVA-Med

- Source: https://github.com/microsoft/LLaVA-Med
- Has its own isolated venv at `vendor/LLaVA-Med/.venv` (Python 3.11,
  `transformers==4.36.2`) because it hard-pins deps incompatible with
  the main project's `transformers` (needed for Qwen2.5-VL).
- Run via subprocess from the main notebook/env — see
  `notebooks/image_understanding.ipynb`.

### Setup

```bash
git clone https://github.com/microsoft/LLaVA-Med.git vendor/LLaVA-Med
cd vendor/LLaVA-Med
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install torch torchvision "transformers==4.36.2" "tokenizers>=0.15.0" \
  "sentencepiece==0.1.99" accelerate "einops==0.6.1" einops-exts \
  "pydantic<2,>=1" timm==0.9.12 protobuf shortuuid
patch -p1 < ../patches/llava_med_builder_low_cpu_mem_usage.patch
```

### Why the patch

`vendor/LLaVA-Med` is gitignored (fetched code, not ours to track), so this
fix must be reapplied after every fresh clone.

`llava/model/builder.py` hardcodes `low_cpu_mem_usage=False` while also
setting `device_map` for any non-cuda device (our `device="mps"` case).
transformers 4.36.2 raises `ValueError: Passing along a device_map requires
low_cpu_mem_usage=True` for that combination. The patch flips the flag to
`True`.
