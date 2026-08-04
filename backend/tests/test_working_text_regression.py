"""Regression: chapter working text must reflect the user's current prose.

复现「中断后续写基于已被删除的旧正文」缺陷：AI 写作流会落 ``draft_v*.md``，
而用户在编辑器中的修改/删除保存到 ``final.md``。当写作/编辑/分析把「当前正文」
误取为最新的 ``draft_v*`` 版本时，用户已删除的内容会被当作真相反复喂回模型。

修复点：DraftStorage.get_working_text 在 final.md 与 draft_*.md 之间按修改时间取
最新（相等偏向 final.md），成为「当前正文」唯一 owner。
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from app.storage.drafts import DraftStorage


def _touch(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


def test_working_text_prefers_user_edited_final_over_stale_draft(tmp_path: Path):
    storage = DraftStorage(str(tmp_path))

    # AI 写作流产物：一份较早的 draft_v1（含后来被删除的内容）。
    asyncio.run(
        storage.save_draft("p", "V1C001", "v1", "第一版内容：审查报告与旧设定。", word_count=14)
    )
    # 用户在编辑器保存/删除后的真相：final.md（更晚，删掉了旧设定）。
    asyncio.run(storage.save_current_draft("p", "V1C001", "用户改定稿：只保留正文。"))

    draft_dir = tmp_path / "p" / "drafts" / "V1C001"
    base = time.time()
    _touch(draft_dir / "draft_v1.md", base)
    _touch(draft_dir / "final.md", base + 10)

    working_text, working_path = asyncio.run(storage.get_working_text("p", "V1C001"))
    assert working_path is not None and working_path.name == "final.md"
    assert working_text == "用户改定稿：只保留正文。"
    assert "旧设定" not in working_text


def test_working_text_uses_newer_ai_draft_when_final_is_older(tmp_path: Path):
    storage = DraftStorage(str(tmp_path))
    asyncio.run(storage.save_current_draft("p", "V1C001", "旧的 final。"))
    asyncio.run(storage.save_draft("p", "V1C001", "v1", "AI 刚写的新一版。", word_count=8))

    draft_dir = tmp_path / "p" / "drafts" / "V1C001"
    base = time.time()
    _touch(draft_dir / "final.md", base)
    _touch(draft_dir / "draft_v1.md", base + 10)

    working_text, working_path = asyncio.run(storage.get_working_text("p", "V1C001"))
    assert working_path is not None and working_path.name == "draft_v1.md"
    assert working_text == "AI 刚写的新一版。"


def test_working_text_empty_when_no_prose(tmp_path: Path):
    storage = DraftStorage(str(tmp_path))
    working_text, working_path = asyncio.run(storage.get_working_text("p", "V1C404"))
    assert working_text == ""
    assert working_path is None


def test_list_draft_versions_orders_v2_after_v10_numerically(tmp_path: Path):
    storage = DraftStorage(str(tmp_path))
    for version in ("v1", "v2", "v10"):
        asyncio.run(storage.save_draft("p", "V1C001", version, f"draft {version}", word_count=8))
    versions = asyncio.run(storage.list_draft_versions("p", "V1C001"))
    assert versions == ["v1", "v2", "v10"]
