"""
Outline Router / 大纲路由

全文规划大纲的读写与设置。大纲是一等创作规划资产，不是章节：
- 内容不参与事实/摘要提取（结构性隔离，见 plan.md §7.2）。
- 用户与 AI 均可编辑，走 revision 乐观并发。
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.control_plane.store import RevisionConflict
from app.dependencies import get_card_storage, get_outline_storage
from app.error_contract import safe_error_code
from app.services.outline_settings import resolve_outline_settings
from app.utils.logger import get_logger
from app.utils.path_safety import validate_path_within

logger = get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/outline", tags=["outline"])
outline_storage = get_outline_storage()
card_storage = get_card_storage()  # 复用其 read_yaml/write_yaml 读写 project.yaml


class OutlineSaveRequest(BaseModel):
    content: str = Field("", max_length=2_000_000, description="大纲全文（markdown）；上限极高，正常规划不受限")
    expected_revision: Optional[int] = Field(None, description="乐观并发校验：期望的当前 revision（自动保存不传=末次写入生效）")


class OutlineSettingsRequest(BaseModel):
    enabled: Optional[bool] = Field(None, description="是否启用大纲")
    require_consult: Optional[bool] = Field(None, description="是否要求 AI 每次撰写前必查大纲")


async def _project_meta(project_id: str) -> dict:
    data_dir = Path(card_storage.data_dir)
    project_dir = data_dir / project_id
    try:
        validate_path_within(project_dir, data_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    project_file = project_dir / "project.yaml"
    if not project_file.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    return await card_storage.read_yaml(project_file) or {}


@router.get("")
async def get_outline(project_id: str):
    """读取大纲正文、revision 与当前设置。"""
    meta = await _project_meta(project_id)
    outline = await outline_storage.get_outline(project_id)
    return {"success": True, "outline": outline, "settings": resolve_outline_settings(meta)}


@router.put("")
async def save_outline(project_id: str, request: OutlineSaveRequest):
    """覆盖写大纲正文（用户编辑入口）。"""
    await _project_meta(project_id)
    try:
        outline = await outline_storage.save_outline(
            project_id, request.content, expected_revision=request.expected_revision
        )
    except RevisionConflict as exc:
        # 不把内部异常字符串泄漏进 HTTP 响应；只回稳定错误码与安全提示。
        raise HTTPException(
            status_code=409,
            detail={"code": safe_error_code(exc), "message": "大纲已被其他修改更新，请刷新后重试。"},
        )
    return {"success": True, "outline": outline}


@router.get("/settings")
async def get_outline_settings(project_id: str):
    meta = await _project_meta(project_id)
    return {"success": True, "settings": resolve_outline_settings(meta)}


@router.put("/settings")
async def update_outline_settings(project_id: str, request: OutlineSettingsRequest):
    """更新项目级大纲设置（写入 project.yaml 的 outline 键，覆盖全局默认）。"""
    data_dir = Path(card_storage.data_dir)
    project_dir = data_dir / project_id
    try:
        validate_path_within(project_dir, data_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    project_file = project_dir / "project.yaml"
    if not project_file.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    meta = await card_storage.read_yaml(project_file) or {}
    override = dict(meta.get("outline") or {})
    if request.enabled is not None:
        override["enabled"] = bool(request.enabled)
    if request.require_consult is not None:
        override["require_consult"] = bool(request.require_consult)
    meta["outline"] = override
    await card_storage.write_yaml(project_file, meta)
    return {"success": True, "settings": resolve_outline_settings(meta)}
