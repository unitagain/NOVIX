"""
Projects Router / 项目路由
Project management endpoints
项目管理端点
"""

from fastapi import APIRouter, HTTPException
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List
from app.schemas.project import Project, ProjectCreate, ProjectStats
from app.storage import CardStorage, CanonStorage, DraftStorage

router = APIRouter(prefix="/projects", tags=["projects"])

# Storage instances / 存储实例
card_storage = CardStorage()
canon_storage = CanonStorage()
draft_storage = DraftStorage()


@router.get("")
async def list_projects():
    """
    List all projects
    列出所有项目
    
    Returns:
        List of projects / 项目列表
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
                projects.append({
                    "id": project_dir.name,
                    "name": data.get("name", project_dir.name),
                    "description": data.get("description", ""),
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", "")
                })
    
    return projects


@router.post("")
async def create_project(project: ProjectCreate):
    """
    Create a new project
    创建新项目
    
    Args:
        project: Project creation data / 项目创建数据
        
    Returns:
        Created project / 创建的项目
    """
    # Generate project ID from name / 从名称生成项目ID
    project_id = project.name.lower().replace(" ", "_")
    
    project_dir = Path(card_storage.data_dir) / project_id
    
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
    project_data = {
        "name": project.name,
        "description": project.description,
        "created_at": now,
        "updated_at": now
    }
    
    await card_storage.write_yaml(project_dir / "project.yaml", project_data)
    
    return {
        "id": project_id,
        "name": project.name,
        "description": project.description,
        "created_at": now,
        "updated_at": now
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
    project_file = Path(card_storage.data_dir) / project_id / "project.yaml"
    
    if not project_file.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    
    data = await card_storage.read_yaml(project_file)
    
    return {
        "id": project_id,
        "name": data.get("name", project_id),
        "description": data.get("description", ""),
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", "")
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
        "fact_count": fact_count
    }


@router.get("/{project_id}/dashboard")
async def get_project_dashboard(project_id: str) -> Dict[str, Any]:
    """Get project dashboard data / 获取项目仪表盘数据

    This endpoint is designed for MVP-2 Week7 frontend dashboard.
    It aggregates per-chapter status and canon summary to avoid many API calls.

    该端点面向 MVP-2 第7周前端仪表盘。
    用于聚合章节状态与事实表概览，避免前端多次请求。
    """

    project_path = draft_storage.get_project_path(project_id)
    if not project_path.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    # Base stats / 基础统计
    character_names = await card_storage.list_character_cards(project_id)
    facts = await canon_storage.get_all_facts(project_id)
    timeline_events = await canon_storage.get_all_timeline_events(project_id)
    character_states = await canon_storage.get_all_character_states(project_id)
    chapters = await draft_storage.list_chapters(project_id)

    total_word_count = 0
    completed_chapters = 0
    chapter_items: List[Dict[str, Any]] = []

    for chapter in sorted(chapters):
        draft_dir = project_path / "drafts" / chapter
        final_path = draft_dir / "final.md"
        conflicts_path = draft_dir / "conflicts.yaml"
        summary_path = project_path / "summaries" / f"{chapter}_summary.yaml"

        has_final = final_path.exists()
        final_word_count = 0
        if has_final:
            try:
                content = await draft_storage.read_text(final_path)
                final_word_count = len(content)
            except Exception:
                final_word_count = 0

            total_word_count += final_word_count
            completed_chapters += 1

        has_summary = summary_path.exists()
        summary_brief = ""
        summary_title = ""
        summary_word_count = 0
        if has_summary:
            try:
                summary = await draft_storage.get_chapter_summary(project_id, chapter)
                if summary:
                    summary_title = summary.title
                    summary_word_count = summary.word_count
                    summary_brief = summary.brief_summary
            except Exception:
                pass

        has_conflicts = conflicts_path.exists()
        conflict_count = 0
        conflict_preview: List[str] = []
        if has_conflicts:
            try:
                report = await draft_storage.read_yaml(conflicts_path)
                conflicts = report.get("conflicts", []) if isinstance(report, dict) else []
                if isinstance(conflicts, list):
                    conflict_count = len(conflicts)
                    conflict_preview = [str(x) for x in conflicts[:5]]
            except Exception:
                conflict_count = 0

        chapter_items.append(
            {
                "chapter": chapter,
                "has_final": has_final,
                "final_word_count": final_word_count,
                "has_summary": has_summary,
                "summary_title": summary_title,
                "summary_word_count": summary_word_count,
                "summary_brief": summary_brief,
                "has_conflicts": has_conflicts,
                "conflict_count": conflict_count,
                "conflict_preview": conflict_preview,
            }
        )

    stats = {
        "total_word_count": total_word_count,
        "completed_chapters": completed_chapters,
        "in_progress_chapters": len(chapters) - completed_chapters,
        "character_count": len(character_names),
        "fact_count": len(facts),
        "timeline_event_count": len(timeline_events),
        "character_state_count": len(character_states),
    }

    recent = {
        "facts": [f.model_dump() for f in facts[-5:]],
        "timeline_events": [e.model_dump() for e in timeline_events[-5:]],
    }

    return {
        "project_id": project_id,
        "stats": stats,
        "chapters": chapter_items,
        "recent": recent,
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
    
    project_dir = Path(card_storage.data_dir) / project_id
    
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    
    shutil.rmtree(project_dir)
    
    return {"success": True, "message": "Project deleted"}
