import os
from pathlib import Path
import pytest

from src.diagram_processing.leader_line_detection.detector import LeaderLineEndpointDetector
from src.diagram_processing.pipeline import DiagramPipelineRunner

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_leader_line_detector_init():
    checkpoint_path = PROJECT_ROOT / "checkpoints" / "leader_line_detection.pth"
    assert checkpoint_path.exists(), f"Checkpoint missing at {checkpoint_path}"
    
    detector = LeaderLineEndpointDetector(checkpoint_path=str(checkpoint_path))
    assert detector.model is not None


def test_pipeline_runner_instantiation():
    runner = DiagramPipelineRunner()
    assert runner is not None
