"""
Orchestrator / 调度器
Coordinates the multi-agent writing workflow
协调多智能体写作工作流
"""

from typing import Dict, Any, Optional, Callable
from enum import Enum
from app.llm_gateway import LLMGateway, get_gateway
from app.storage import CardStorage, CanonStorage, DraftStorage
from app.agents import ArchivistAgent, WriterAgent, ReviewerAgent, EditorAgent
from app.context_engine import ContextSelector


class SessionStatus(str, Enum):
    """Session status enum / 会话状态枚举"""
    IDLE = "idle"
    GENERATING_BRIEF = "generating_brief"
    WRITING_DRAFT = "writing_draft"
    REVIEWING = "reviewing"
    EDITING = "editing"
    WAITING_FEEDBACK = "waiting_feedback"
    COMPLETED = "completed"
    ERROR = "error"


class Orchestrator:
    """
    Orchestrates the multi-agent writing workflow
    协调多智能体写作工作流
    """
    
    def __init__(
        self,
        data_dir: str = "../data",
        progress_callback: Optional[Callable] = None
    ):
        """
        Initialize orchestrator
        
        Args:
            data_dir: Data directory path / 数据目录路径
            progress_callback: Optional callback for progress updates / 可选的进度更新回调
        """
        # Initialize storage / 初始化存储
        self.card_storage = CardStorage(data_dir)
        self.canon_storage = CanonStorage(data_dir)
        self.draft_storage = DraftStorage(data_dir)
        
        # Initialize LLM gateway / 初始化大模型网关
        self.gateway = get_gateway()
        
        # Initialize agents / 初始化智能体
        self.archivist = ArchivistAgent(
            self.gateway,
            self.card_storage,
            self.canon_storage,
            self.draft_storage
        )
        self.writer = WriterAgent(
            self.gateway,
            self.card_storage,
            self.canon_storage,
            self.draft_storage
        )
        self.reviewer = ReviewerAgent(
            self.gateway,
            self.card_storage,
            self.canon_storage,
            self.draft_storage
        )
        self.editor = EditorAgent(
            self.gateway,
            self.card_storage,
            self.canon_storage,
            self.draft_storage
        )
        
        # Initialize context engine / 初始化上下文引擎
        self.context_selector = ContextSelector(
            self.card_storage,
            self.canon_storage,
            self.draft_storage
        )
        
        # Progress callback / 进度回调
        self.progress_callback = progress_callback
        
        # Current session state / 当前会话状态
        self.current_status = SessionStatus.IDLE
        self.current_project_id: Optional[str] = None
        self.current_chapter: Optional[str] = None
        self.iteration_count = 0
        self.max_iterations = 5
    
    async def start_session(
        self,
        project_id: str,
        chapter: str,
        chapter_title: str,
        chapter_goal: str,
        target_word_count: int = 3000,
        character_names: Optional[list] = None
    ) -> Dict[str, Any]:
        """
        Start a new writing session
        开始新的写作会话
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            chapter_title: Chapter title / 章节标题
            chapter_goal: Chapter goal / 章节目标
            target_word_count: Target word count / 目标字数
            character_names: Optional character names / 可选的角色名称列表
            
        Returns:
            Session result / 会话结果
        """
        self.current_project_id = project_id
        self.current_chapter = chapter
        self.iteration_count = 0
        
        try:
            # Step 1: Archivist generates scene brief / 步骤1：资料管理员生成场景简报
            await self._update_status(SessionStatus.GENERATING_BRIEF, "资料管理员正在整理设定...")
            
            archivist_result = await self.archivist.execute(
                project_id=project_id,
                chapter=chapter,
                context={
                    "chapter_title": chapter_title,
                    "chapter_goal": chapter_goal,
                    "characters": character_names or []
                }
            )
            
            if not archivist_result["success"]:
                return await self._handle_error("Scene brief generation failed")
            
            scene_brief = archivist_result["scene_brief"]

            style_card = await self.card_storage.get_style_card(project_id)
            rules_card = await self.card_storage.get_rules_card(project_id)

            if character_names:
                _character_names = character_names
            else:
                _character_names = await self.card_storage.list_character_cards(project_id)

            character_cards = []
            for name in _character_names:
                card = await self.card_storage.get_character_card(project_id, name)
                if card:
                    character_cards.append(card)

            world_card_names = await self.card_storage.list_world_cards(project_id)
            world_cards = []
            for name in world_card_names:
                card = await self.card_storage.get_world_card(project_id, name)
                if card:
                    world_cards.append(card)

            facts = await self.canon_storage.get_all_facts(project_id)
            timeline = await self.canon_storage.get_all_timeline_events(project_id)
            character_states = await self.canon_storage.get_all_character_states(project_id)
            
            # Step 2: Writer generates draft / 步骤2：撰稿人生成草稿
            await self._update_status(SessionStatus.WRITING_DRAFT, "撰稿人正在撰写草稿...")
            
            writer_result = await self.writer.execute(
                project_id=project_id,
                chapter=chapter,
                context={
                    "scene_brief": scene_brief,
                    "chapter_goal": chapter_goal,
                    "target_word_count": target_word_count,
                    "style_card": style_card,
                    "rules_card": rules_card,
                    "character_cards": character_cards,
                    "world_cards": world_cards,
                    "facts": facts,
                    "timeline": timeline,
                    "character_states": character_states,
                }
            )
            
            if not writer_result["success"]:
                return await self._handle_error("Draft generation failed")
            
            draft = writer_result["draft"]
            
            # Step 3: Reviewer reviews draft / 步骤3：审稿人审核草稿
            await self._update_status(SessionStatus.REVIEWING, "审稿人正在审核草稿...")
            
            reviewer_result = await self.reviewer.execute(
                project_id=project_id,
                chapter=chapter,
                context={
                    "draft_version": "v1"
                }
            )
            
            if not reviewer_result["success"]:
                return await self._handle_error("Review failed")
            
            review = reviewer_result["review"]
            
            # Step 4: Editor revises draft / 步骤4：编辑修订草稿
            await self._update_status(SessionStatus.EDITING, "编辑正在修订草稿...")
            
            editor_result = await self.editor.execute(
                project_id=project_id,
                chapter=chapter,
                context={
                    "draft_version": "v1",
                    "user_feedback": ""
                }
            )
            
            if not editor_result["success"]:
                return await self._handle_error("Editing failed")
            
            revised_draft = editor_result["draft"]
            
            # Step 5: Wait for user feedback / 步骤5：等待用户反馈
            await self._update_status(SessionStatus.WAITING_FEEDBACK, "等待用户反馈...")
            
            return {
                "success": True,
                "status": SessionStatus.WAITING_FEEDBACK,
                "scene_brief": scene_brief,
                "draft_v1": draft,
                "review": review,
                "draft_v2": revised_draft,
                "iteration": self.iteration_count
            }
            
        except Exception as e:
            return await self._handle_error(f"Session error: {str(e)}")
    
    async def process_feedback(
        self,
        project_id: str,
        chapter: str,
        feedback: str,
        action: str = "revise"  # "revise" or "confirm"
    ) -> Dict[str, Any]:
        """
        Process user feedback
        处理用户反馈
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            feedback: User feedback / 用户反馈
            action: Action to take ("revise" or "confirm") / 要采取的行动
            
        Returns:
            Result / 结果
        """
        if action == "confirm":
            # User is satisfied, finalize the chapter / 用户满意，完成章节
            return await self._finalize_chapter(project_id, chapter)
        
        # User wants revisions / 用户要求修订
        self.iteration_count += 1
        
        if self.iteration_count >= self.max_iterations:
            return {
                "success": False,
                "error": "Maximum iterations reached",
                "message": "已达到最大迭代次数，建议确认当前版本或手动编辑"
            }
        
        try:
            # Get latest draft version / 获取最新草稿版本
            versions = await self.draft_storage.list_draft_versions(project_id, chapter)
            latest_version = versions[-1] if versions else "v1"
            
            # Re-review with feedback / 带反馈重新审核
            await self._update_status(SessionStatus.REVIEWING, "根据反馈重新审核...")
            
            reviewer_result = await self.reviewer.execute(
                project_id=project_id,
                chapter=chapter,
                context={
                    "draft_version": latest_version
                }
            )
            
            # Edit with user feedback / 根据用户反馈编辑
            await self._update_status(SessionStatus.EDITING, "根据反馈修订...")
            
            editor_result = await self.editor.execute(
                project_id=project_id,
                chapter=chapter,
                context={
                    "draft_version": latest_version,
                    "user_feedback": feedback
                }
            )
            
            if not editor_result["success"]:
                return await self._handle_error("Revision failed")
            
            # Wait for feedback again / 再次等待反馈
            await self._update_status(SessionStatus.WAITING_FEEDBACK, "等待用户反馈...")
            
            return {
                "success": True,
                "status": SessionStatus.WAITING_FEEDBACK,
                "draft": editor_result["draft"],
                "version": editor_result["version"],
                "iteration": self.iteration_count
            }
            
        except Exception as e:
            return await self._handle_error(f"Feedback processing error: {str(e)}")
    
    async def _finalize_chapter(
        self,
        project_id: str,
        chapter: str
    ) -> Dict[str, Any]:
        """
        Finalize chapter and save final draft
        完成章节并保存成稿
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            
        Returns:
            Result / 结果
        """
        try:
            # Get latest draft / 获取最新草稿
            versions = await self.draft_storage.list_draft_versions(project_id, chapter)
            if not versions:
                return await self._handle_error("No draft found to finalize")

            latest_version = versions[-1]
            draft = await self.draft_storage.get_draft(project_id, chapter, latest_version)
            if not draft:
                return await self._handle_error("No draft content found to finalize")

            # Save as final / 保存为成稿
            await self.draft_storage.save_final_draft(
                project_id=project_id,
                chapter=chapter,
                content=draft.content,
            )

            # Generate chapter summary (MVP-2 Week 5)
            # 生成章节摘要（MVP-2 第5周）
            try:
                # Prefer title from scene brief / 优先从场景简报获取标题
                scene_brief = await self.draft_storage.get_scene_brief(project_id, chapter)
                chapter_title = scene_brief.title if scene_brief and scene_brief.title else chapter

                summary = await self.archivist.generate_chapter_summary(
                    project_id=project_id,
                    chapter=chapter,
                    chapter_title=chapter_title,
                    final_draft=draft.content,
                )

                await self.draft_storage.save_chapter_summary(project_id, summary)
            except Exception as e:
                # Do not block finalization if summary fails
                # 摘要失败不阻塞章节完成（保证流程可继续）
                print(f"[Orchestrator] Failed to generate chapter summary: {e}")

            # Extract canon updates (MVP-2 Week 5)
            # 抽取并更新事实表（MVP-2 第5周）
            try:
                canon_updates = await self.archivist.extract_canon_updates(
                    project_id=project_id,
                    chapter=chapter,
                    final_draft=draft.content,
                )

                for fact in canon_updates.get("facts", []) or []:
                    await self.canon_storage.add_fact(project_id, fact)

                for event in canon_updates.get("timeline_events", []) or []:
                    await self.canon_storage.add_timeline_event(project_id, event)

                for state in canon_updates.get("character_states", []) or []:
                    await self.canon_storage.update_character_state(project_id, state)

                # Detect conflicts (MVP-2 Week 6)
                # 冲突检测（MVP-2 第6周）
                try:
                    report = await self.canon_storage.detect_conflicts(
                        project_id=project_id,
                        chapter=chapter,
                        new_facts=canon_updates.get("facts", []) or [],
                        new_timeline_events=canon_updates.get("timeline_events", []) or [],
                        new_character_states=canon_updates.get("character_states", []) or [],
                    )

                    await self.draft_storage.save_conflict_report(
                        project_id=project_id,
                        chapter=chapter,
                        report=report,
                    )
                except Exception as e:
                    # Do not block finalization if conflict detection fails
                    # 冲突检测失败不阻塞章节完成（保证流程可继续）
                    print(f"[Orchestrator] Failed to detect conflicts: {e}")
            except Exception as e:
                # Do not block finalization if canon update fails
                # Canon 更新失败不阻塞章节完成（保证流程可继续）
                print(f"[Orchestrator] Failed to update canon: {e}")

            await self._update_status(SessionStatus.COMPLETED, "章节完成！")

            return {
                "success": True,
                "status": SessionStatus.COMPLETED,
                "message": "Chapter finalized successfully",
                "final_draft": draft,
            }

        except Exception as e:
            return await self._handle_error(f"Finalization error: {str(e)}")
    
    async def _update_status(self, status: SessionStatus, message: str) -> None:
        """
        Update session status and notify callback
        更新会话状态并通知回调
        
        Args:
            status: New status / 新状态
            message: Status message / 状态消息
        """
        self.current_status = status
        
        if self.progress_callback:
            await self.progress_callback({
                "status": status.value,
                "message": message,
                "project_id": self.current_project_id,
                "chapter": self.current_chapter,
                "iteration": self.iteration_count
            })
    
    async def _handle_error(self, error_message: str) -> Dict[str, Any]:
        """
        Handle error and update status
        处理错误并更新状态
        
        Args:
            error_message: Error message / 错误消息
            
        Returns:
            Error result / 错误结果
        """
        self.current_status = SessionStatus.ERROR
        
        if self.progress_callback:
            await self.progress_callback({
                "status": SessionStatus.ERROR.value,
                "message": error_message,
                "project_id": self.current_project_id,
                "chapter": self.current_chapter
            })
        
        return {
            "success": False,
            "status": SessionStatus.ERROR,
            "error": error_message
        }
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current session status
        获取当前会话状态
        
        Returns:
            Status dict / 状态字典
        """
        return {
            "status": self.current_status.value,
            "project_id": self.current_project_id,
            "chapter": self.current_chapter,
            "iteration": self.iteration_count
        }
