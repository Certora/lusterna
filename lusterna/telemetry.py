"""Session-wide token usage tracking."""
from __future__ import annotations
from typing import Any


class UsageCounter:
    def __init__(self) -> None:
        self.input_tokens       = 0
        self.output_tokens      = 0
        self.cache_read_tokens  = 0
        self.cache_write_tokens = 0

    def record(self, usage: Any) -> None:
        self.input_tokens       += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens      += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens  += getattr(usage, "cache_read_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_write_tokens", 0) or 0

    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def reset(self) -> None:
        self.__init__()

    def as_dict(self) -> dict:
        return {
            "input_tokens":       self.input_tokens,
            "output_tokens":      self.output_tokens,
            "cache_read_tokens":  self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_tokens":       self.total(),
        }


session = UsageCounter()
stage   = UsageCounter()
budget: int | None = None
