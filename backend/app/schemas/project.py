"""
Project Data Models / 项目数据模型
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ProjectBase(BaseModel):
    """Base project model / 项目基础模型"""
    name: str = Field(..., description="Project name / 项目名称")
    description: Optional[str] = Field(None, description="Project description / 项目描述")


class ProjectCreate(ProjectBase):
    """Model for creating a project / 创建项目的模型"""
    pass


class Project(ProjectBase):
    """Complete project model / 完整项目模型"""
    id: str = Field(..., description="Project ID / 项目ID")
    created_at: datetime = Field(..., description="Creation timestamp / 创建时间")
    updated_at: datetime = Field(..., description="Last update timestamp / 最后更新时间")
    chapter_count: int = Field(0, description="Number of chapters / 章节数量")
    total_word_count: int = Field(0, description="Total word count / 总字数")
    
    class Config:
        from_attributes = True


class ProjectStats(BaseModel):
    """Project statistics / 项目统计信息"""
    total_word_count: int = Field(..., description="Total words / 总字数")
    completed_chapters: int = Field(..., description="Completed chapters / 已完成章节")
    in_progress_chapters: int = Field(..., description="In-progress chapters / 进行中章节")
    character_count: int = Field(..., description="Number of characters / 角色数量")
    fact_count: int = Field(..., description="Number of facts / 事实条目数")
