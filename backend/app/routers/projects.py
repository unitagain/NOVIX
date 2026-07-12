# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  项目路由 - 项目管理端点
  Projects Router - Project management endpoints including list, create,
  delete and project statistics operations.
"""

from fastapi import APIRouter, HTTPException
from pathlib import Path
from datetime import datetime
from app.schemas.project import ProjectCreate
from app.dependencies import get_card_storage, get_canon_storage, get_draft_storage
from app.utils.path_safety import sanitize_id, validate_path_within
from app.utils.language import normalize_language
from pydantic import BaseModel, Field

router = APIRouter(prefix="/projects", tags=["projects"])

# ========================================================================
# 存储实例 / Storage instances
# ========================================================================

card_storage = get_card_storage()
canon_storage = get_canon_storage()
draft_storage = get_draft_storage()


@router.get("")
async def list_projects():
    """
    列出所有项目 / List all projects

    Retrieves a list of all projects with metadata.

    Returns:
        项目列表 / List of projects with id, name, description, timestamps.
    """
    data_dir = Path(card_storage.data_dir)

    if not data_dir.exists():
        return []

    projects = []
    for project_dir in data_dir.iterdir():
        if project_dir.is_dir():
            project_file = project_dir / "project.yaml"
            if project_file.exists():
                data = await card_storage.read_yaml(project_file)
                language = normalize_language(data.get("language"), default="zh")
                projects.append(
                    {
                        "id": project_dir.name,
                        "name": data.get("name", project_dir.name),
                        "description": data.get("description", ""),
                        "language": language,
                        "created_at": data.get("created_at", ""),
                        "updated_at": data.get("updated_at", ""),
                    }
                )

    return projects


@router.post("")
async def create_project(project: ProjectCreate):
    """
    创建新项目 / Create a new project

    Initializes a new project with directory structure and metadata.

    Args:
        project: 项目创建数据 / Project creation data.

    Returns:
        创建的项目 / Created project with generated ID and metadata.
    """
    # Generate project ID from name / 从名称生成项目ID
    try:
        project_id = sanitize_id(project.name.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project name")

    data_dir = Path(card_storage.data_dir)
    project_dir = data_dir / project_id
    validate_path_within(project_dir, data_dir)

    if project_dir.exists():
        raise HTTPException(status_code=400, detail="Project already exists")

    # Create project structure / 创建项目结构
    card_storage.ensure_dir(project_dir / "cards" / "characters")
    card_storage.ensure_dir(project_dir / "cards" / "world")
    card_storage.ensure_dir(project_dir / "canon")
    card_storage.ensure_dir(project_dir / "outline")
    card_storage.ensure_dir(project_dir / "drafts")
    card_storage.ensure_dir(project_dir / "summaries")
    card_storage.ensure_dir(project_dir / "traces")

    # Save project metadata / 保存项目元数据
    now = datetime.now().isoformat()
    lang_value = project.language if isinstance(project.language, str) else project.language.value
    project_data = {
        "name": project.name,
        "description": project.description,
        "language": lang_value,
        "created_at": now,
        "updated_at": now,
    }

    await card_storage.write_yaml(project_dir / "project.yaml", project_data)

    return {
        "id": project_id,
        "name": project.name,
        "description": project.description,
        "language": lang_value,
        "created_at": now,
        "updated_at": now,
    }


@router.get("/{project_id}")
async def get_project(project_id: str):
    """
    Get project details
    获取项目详情

    Args:
        project_id: Project ID / 项目ID

    Returns:
        Project details / 项目详情
    """
    data_dir = Path(card_storage.data_dir)
    project_dir = data_dir / project_id
    try:
        validate_path_within(project_dir, data_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    project_file = project_dir / "project.yaml"

    if not project_file.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    data = await card_storage.read_yaml(project_file)
    language = normalize_language(data.get("language"), default="zh")

    return {
        "id": project_id,
        "name": data.get("name", project_id),
        "description": data.get("description", ""),
        "language": language,
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", ""),
    }


@router.get("/{project_id}/stats")
async def get_project_stats(project_id: str):
    """
    Get project statistics
    获取项目统计信息

    Args:
        project_id: Project ID / 项目ID

    Returns:
        Project statistics / 项目统计信息
    """
    # Count characters / 统计角色数
    character_names = await card_storage.list_character_cards(project_id)
    character_count = len(character_names)

    # Count facts / 统计事实数
    facts = await canon_storage.get_all_facts(project_id)
    fact_count = len(facts)

    # Count chapters / 统计章节数
    chapters = await draft_storage.list_chapters(project_id)
    chapter_count = len(chapters)

    # Calculate total word count / 计算总字数
    total_word_count = 0
    completed_chapters = 0

    for chapter in chapters:
        final_draft = await draft_storage.get_final_draft(project_id, chapter)
        if final_draft:
            total_word_count += len(final_draft)
            completed_chapters += 1

    return {
        "total_word_count": total_word_count,
        "completed_chapters": completed_chapters,
        "in_progress_chapters": chapter_count - completed_chapters,
        "character_count": character_count,
        "fact_count": fact_count,
    }


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    """
    Delete a project
    删除项目

    Args:
        project_id: Project ID / 项目ID

    Returns:
        Deletion result / 删除结果
    """
    import shutil

    data_dir = Path(card_storage.data_dir)
    project_dir = data_dir / project_id
    try:
        validate_path_within(project_dir, data_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project ID")

    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    shutil.rmtree(project_dir)

    return {"success": True, "message": "Project deleted"}


class ProjectRenameRequest(BaseModel):
    """Request body for renaming a project."""

    name: str = Field(..., min_length=1, max_length=200, description="New project name / 新项目名称")


@router.patch("/{project_id}")
async def rename_project(project_id: str, request: ProjectRenameRequest):
    """Rename a project (display name only)."""
    data_dir = Path(card_storage.data_dir)
    project_dir = data_dir / project_id
    try:
        validate_path_within(project_dir, data_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project ID")

    project_file = project_dir / "project.yaml"
    if not project_file.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    new_name = (request.name or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Project name is required")

    data = await card_storage.read_yaml(project_file) or {}
    data["name"] = new_name
    data["updated_at"] = datetime.now().isoformat()
    await card_storage.write_yaml(project_file, data)

    language = normalize_language(data.get("language"), default="zh")
    return {
        "success": True,
        "project": {
            "id": project_id,
            "name": data.get("name", project_id),
            "description": data.get("description", ""),
            "language": language,
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
        },
    }
