"""Shared runtime and benchmark provider contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ProviderUsage:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None, *, requests: int = 0) -> "ProviderUsage":
        data = value or {}
        prompt = int(data.get("prompt_tokens") or data.get("input_tokens") or 0)
        completion = int(data.get("completion_tokens") or data.get("output_tokens") or 0)
        total = int(data.get("total_tokens") or prompt + completion)
        return cls(
            requests=int(data.get("requests") or requests),
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            elapsed_seconds=float(data.get("elapsed_seconds") or 0.0),
        )

    def merge(self, other: "ProviderUsage") -> "ProviderUsage":
        return ProviderUsage(
            requests=self.requests + other.requests,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            elapsed_seconds=self.elapsed_seconds + other.elapsed_seconds,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateArtifact:
    id: str
    provider: str
    model: str
    content_fingerprint: str
    usage: ProviderUsage

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["usage"] = self.usage.to_dict()
        return value


@dataclass(frozen=True)
class JudgeArtifact:
    id: str
    provider: str
    model: str
    pair_fingerprint: str
    usage: ProviderUsage
    comparable: bool

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["usage"] = self.usage.to_dict()
        return value
