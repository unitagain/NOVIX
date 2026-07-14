"""Owned stages for the longform benchmark pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List

from app.llm_gateway.contracts import CandidateArtifact, JudgeArtifact, ProviderUsage


class CorpusSceneStage:
    def __init__(self, backend: Any) -> None:
        self.backend = backend

    def import_corpus(self, **kwargs: Any) -> Dict[str, Any]:
        return self.backend.import_corpus(**kwargs)

    def paths(self, benchmark_id: str) -> Any:
        return self.backend.paths(benchmark_id)

    async def generate_scenes(self, **kwargs: Any) -> Dict[str, Any]:
        return await self.backend.generate_candidates(**kwargs)

    def normalize_generated(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self.backend.normalize_generated(*args, **kwargs)

    def select_queries(self, rows: List[Dict[str, Any]], *, limit: int) -> List[Dict[str, Any]]:
        return self.backend.select_run_queries(rows, limit=limit)


class CandidateGenerationStage:
    def __init__(self, backend: Any) -> None:
        self.backend = backend

    async def writing_calibration(self, **kwargs: Any) -> Dict[str, Any]:
        return await self.backend.generate_writing_calibration(**kwargs)

    async def strategy_ab(self, **kwargs: Any) -> Dict[str, Any]:
        return await self.backend.generate_strategy_ab(**kwargs)

    async def p12_context_ab(self, **kwargs: Any) -> Dict[str, Any]:
        return await self.backend.generate_p12_context_ab(**kwargs)

    @staticmethod
    def artifact(*, artifact_id: str, response: Dict[str, Any], content: str) -> CandidateArtifact:
        return CandidateArtifact(
            id=artifact_id,
            provider=str(response.get("provider") or ""),
            model=str(response.get("model") or ""),
            content_fingerprint=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            usage=ProviderUsage.from_mapping(response.get("usage"), requests=1),
        )

    def resolve_strategy(self, strategy: Any) -> Any:
        return self.backend.resolve_retrieval_strategy(strategy)

    def create_strategy_engine(self, spec: Any) -> Any:
        return self.backend.create_strategy_engine(spec)

    async def select_strategy_context(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return await self.backend.select_writer_strategy_context(*args, **kwargs)


class JudgeStage:
    def __init__(self, backend: Any) -> None:
        self.backend = backend

    async def calibration(self, **kwargs: Any) -> Dict[str, Any]:
        return await self.backend.score_calibration(**kwargs)

    async def strategy_ab(self, **kwargs: Any) -> Dict[str, Any]:
        return await self.backend.score_strategy_ab(**kwargs)

    async def p12_context_ab(self, **kwargs: Any) -> Dict[str, Any]:
        return await self.backend.score_p12_context_ab(**kwargs)

    async def pairwise_with_retries(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return await self.backend.score_pairwise_with_retries(*args, **kwargs)

    @staticmethod
    def artifact(
        *,
        artifact_id: str,
        provider: str,
        model: str,
        pair_fingerprint: str,
        usage_rows: List[Dict[str, Any]],
        comparable: bool,
    ) -> JudgeArtifact:
        usage = ProviderUsage()
        for row in usage_rows:
            usage = usage.merge(ProviderUsage.from_mapping(row, requests=1))
        return JudgeArtifact(
            id=artifact_id,
            provider=provider,
            model=model,
            pair_fingerprint=pair_fingerprint,
            usage=usage,
            comparable=comparable,
        )


class StatisticsStage:
    def __init__(self, backend: Any) -> None:
        self.backend = backend

    async def retrieval(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return await self.backend.run_retrieval_stage(*args, **kwargs)

    async def run_suite(self, **kwargs: Any) -> Dict[str, Any]:
        return await self.backend.run_suite(**kwargs)

    async def no_context_probe(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return await self.backend.run_no_context_probe(*args, **kwargs)

    def analyze_strategy_ab(self, **kwargs: Any) -> Dict[str, Any]:
        return self.backend.analyze_strategy_ab(**kwargs)

    def analyze_calibration(self, **kwargs: Any) -> Dict[str, Any]:
        return self.backend.analyze_calibration(**kwargs)


class LedgerRecoveryStage:
    def __init__(self, backend: Any) -> None:
        self.backend = backend

    def promote_failures(self, **kwargs: Any) -> Dict[str, Any]:
        return self.backend.promote_failures(**kwargs)

    def strategy_pair_fingerprint(self, first: Dict[str, Any], second: Dict[str, Any]) -> str:
        return self.backend.strategy_ab_pair_fingerprint(first, second)

    def calibration_pairs(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self.backend.calibration_context_pairs(rows)

    def calibration_pair_fingerprint(self, first: Dict[str, Any], second: Dict[str, Any]) -> str:
        return self.backend.calibration_pair_fingerprint(first, second)


class ReportStage:
    def __init__(self, backend: Any) -> None:
        self.backend = backend

    def render(self, **kwargs: Any) -> Dict[str, Any]:
        return self.backend.report(**kwargs)


@dataclass(frozen=True)
class LongformBenchmarkPipeline:
    corpus: CorpusSceneStage
    generation: CandidateGenerationStage
    judge: JudgeStage
    statistics: StatisticsStage
    ledger: LedgerRecoveryStage
    report: ReportStage

    @classmethod
    def build(cls, backend: Any) -> "LongformBenchmarkPipeline":
        return cls(
            corpus=CorpusSceneStage(backend),
            generation=CandidateGenerationStage(backend),
            judge=JudgeStage(backend),
            statistics=StatisticsStage(backend),
            ledger=LedgerRecoveryStage(backend),
            report=ReportStage(backend),
        )
