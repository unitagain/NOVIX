# -*- coding: utf-8 -*-
"""H4 confirmed-only canon retrieval regression test.

验证 needs_review（AI 待确认）事实不会作为已确立 canon 进入模型检索上下文。
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


def test_needs_review_fact_is_excluded_from_canon_recall():
    engine = ContextSelectEngine()  # 纯词法
    results = asyncio.run(
        engine.retrieval_select(
            project_id="p", query="青云门", item_types=["fact"], storage=_StatusFactStorage(), top_k=2
        )
    )
    ids = [r.id for r in results]
    assert ids[0] == "FC"  # confirmed 排在 needs_review 之前
    assert "FN" not in ids
