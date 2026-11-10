from pathlib import Path

__version__ = (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()
