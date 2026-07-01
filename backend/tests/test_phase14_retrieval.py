# -*- coding: utf-8 -*-
"""Phase 14 自审补 · needs_review 事实检索降权回归测试。

验证：检索中 needs_review（AI 待确认）事实被降权，confirmed 优先——
"不污染主 canon" 的检索层落地。两条事实词法分相同，排序由 status 降权决定。无网络 / 无 key。
"""

import asyncio

from app.context_engine.select_engine import ContextSelectEngine
from app.schemas.canon import Fact


class _StatusFactStorage:
    async def get_all_facts(self, project_id):
        return [
            Fact(id="FC", statement="青云门是正道领袖", source="V1C001", introduced_in="V1C001", status="confirmed"),
            Fact(
                id="FN", statement="青云门暗中勾结魔教", source="V1C001", introduced_in="V1C001", status="needs_review"
            ),
        ]


def test_needs_review_fact_ranked_below_confirmed():
    engine = ContextSelectEngine()  # 纯词法
    results = asyncio.run(
        engine.retrieval_select(
            project_id="p", query="青云门", item_types=["fact"], storage=_StatusFactStorage(), top_k=2
        )
    )
    ids = [r.id for r in results]
    assert ids[0] == "FC"  # confirmed 排在 needs_review 之前
    assert "FN" in ids  # needs_review 仍可被检索（降权而非剔除）
