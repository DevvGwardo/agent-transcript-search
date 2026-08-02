from .base import Extractor
from .claude import ClaudeExtractor
from .hermes import HermesExtractor
from .cursor import CursorExtractor
from .codex import CodexExtractor

ALL_EXTRACTORS: list[type[Extractor]] = [
    ClaudeExtractor,
    HermesExtractor,
    CursorExtractor,
    CodexExtractor,
]

__all__ = [
    "Extractor",
    "ClaudeExtractor",
    "HermesExtractor",
    "CursorExtractor",
    "CodexExtractor",
    "ALL_EXTRACTORS",
]
