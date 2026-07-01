"""Shared data shapes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

Tone = Literal["good", "bad", "neutral"]
Confidence = Literal["low", "medium", "high"]


@dataclass
class NewsItem:
    """One normalized news article from any source."""
    ticker: str
    headline: str
    body_text: str
    published_at: Optional[datetime]
    source_name: str
    url: Optional[str] = None

    def search_text(self) -> str:
        return f"{self.headline}\n{self.body_text}".lower()


@dataclass
class NewsClassification:
    """The model's read on one article: directional tone + confidence."""
    tone: Tone
    confidence: Confidence
    reason: str
    insufficient_data: bool
