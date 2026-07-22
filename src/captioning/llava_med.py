"""LLaVA-Med captioner for clinical photos and microscopy.

LLaVA-Med hard-pins transformers==4.36.2, incompatible with the main venv's
transformers (needed for Qwen2.5-VL). It runs in its own isolated venv at
vendor/LLaVA-Med/.venv and is invoked here as a subprocess.
"""

import subprocess
import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parents[2] / "vendor"
RUNNER_SCRIPT = VENDOR_DIR / "llava_med_runner.py"
VENV_PYTHON = VENDOR_DIR / "LLaVA-Med" / ".venv" / "bin" / "python"


class LlavaMedCaptioner:
    def __init__(self):
        if not VENV_PYTHON.exists():
            raise FileNotFoundError(
                f"LLaVA-Med venv not found at {VENV_PYTHON}. "
                "See vendor/README.md for setup."
            )

    def caption(self, image_path: str, prompt: str | None = None) -> str:
        cmd = [str(VENV_PYTHON), str(RUNNER_SCRIPT), str(image_path)]
        if prompt:
            cmd += ["--prompt", prompt]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"LLaVA-Med runner failed:\n{result.stderr}")

        return result.stdout.strip().splitlines()[-1]
