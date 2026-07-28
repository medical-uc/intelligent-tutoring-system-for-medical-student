from dataclasses import dataclass

@dataclass
class Feature:
    width: float
    height: float
    center_x: float
    center_y: float
    font_size: float
    indentation: float
    num_words: int
    num_lines: int
    is_all_caps: bool
    starts_with_bullet: bool
    starts_with_number: bool
    ends_with_colon: bool
    top_ratio: float
    left_ratio: float
    
    # Slide-level features
    largest_font: float = 0.0
    average_font: float = 0.0
    num_text_blocks: int = 0
    num_images: int = 0
    num_tables: int = 0
