"""Versioned contracts for resumable real-API evaluation campaigns."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from app.llm_gateway.contracts import ProviderUsage


CAMPAIGN_SCHEMA_VERSION = 2


def stable_fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CampaignCorpus:
    benchmark_id: str
    enabled_experiments: List[str] = field(default_factory=lambda: ["suite", "retrieval_ab", "p12_context_ab"])
    scene_ids: List[str] = field(default_factory=list)
    data_classification: str = "private"
    allow_external_api: bool = False


@dataclass(frozen=True)
class CampaignBudget:
    max_requests: int = 1000
    max_tokens: int = 2_000_000
    max_elapsed_seconds: int = 86400
    batch_scenes: int = 5
    max_concurrency: int = 1


@dataclass(frozen=True)
class CampaignStopRules:
    min_pairs: int = 20
    min_scenes: int = 10
    win_ci_lower: float = 0.55
    loss_ci_upper: float = 0.45
    futility_ci_width: float = 0.08
    comparable_rate: float = 0.90
    position_consistency: float = 0.95
    judge_agreement: float = 0.80


@dataclass(frozen=True)
class CampaignPrivacy:
    allow_private_egress: bool = False
    allow_trace_content_export: bool = False
    redact_fields: List[str] = field(
        default_factory=lambda: ["content", "prompt", "messages", "candidate_text", "chapter_text", "body"]
    )


@dataclass(frozen=True)
class EvalCampaign:
    id: str
    corpora: List[CampaignCorpus]
    writer_providers: List[str]
    judge_providers: List[str]
    retrieval_strategy_a: str = "bm25"
    retrieval_strategy_b: str = "jit_hybrid"
    trials: int = 2
    suite: str = "smoke"
    budget: CampaignBudget = field(default_factory=CampaignBudget)
    stop: CampaignStopRules = field(default_factory=CampaignStopRules)
    privacy: CampaignPrivacy = field(default_factory=CampaignPrivacy)
    retry_provider_errors: bool = False
    pairwise_retries: int = 2
    schema_version: int = CAMPAIGN_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "EvalCampaign":
        corpora = [CampaignCorpus(**item) for item in value.get("corpora") or []]
        if not corpora:
            raise ValueError("campaign_requires_corpus")
        writers = [str(item) for item in value.get("writer_providers") or [] if str(item)]
        judges = [str(item) for item in value.get("judge_providers") or [] if str(item)]
        if not writers or not judges:
            raise ValueError("campaign_requires_writer_and_judge_providers")
        return cls(
            id=str(value.get("id") or "campaign").strip() or "campaign",
            corpora=corpora,
            writer_providers=writers,
            judge_providers=judges,
            retrieval_strategy_a=str(value.get("retrieval_strategy_a") or "bm25"),
            retrieval_strategy_b=str(value.get("retrieval_strategy_b") or "jit_hybrid"),
            trials=max(1, int(value.get("trials") or 1)),
            suite=str(value.get("suite") or "smoke"),
            budget=CampaignBudget(**dict(value.get("budget") or {})),
            stop=CampaignStopRules(**dict(value.get("stop") or {})),
            privacy=CampaignPrivacy(**dict(value.get("privacy") or {})),
            retry_provider_errors=bool(value.get("retry_provider_errors", False)),
            pairwise_retries=max(
                0,
                int(
                    value.get("pairwise_retries")
                    if value.get("pairwise_retries") is not None
                    else (1 if value.get("retry_provider_errors", False) else 0)
                ),
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        return stable_fingerprint(self.to_dict())


@dataclass
class CampaignUsage:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0

    def add(self, value: Dict[str, Any]) -> None:
        usage = ProviderUsage.from_mapping(value)
        self.requests += usage.requests
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.total_tokens += usage.total_tokens
        self.elapsed_seconds += usage.elapsed_seconds

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def campaign_job_id(*parts: Any) -> str:
    return "job_" + stable_fingerprint([str(part) for part in parts])[:24]
