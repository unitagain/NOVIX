"""
Editor Agent / 编辑
Revises drafts based on review feedback
根据审稿反馈修订草稿
"""

import yaml
import re
from typing import Dict, Any, List, Tuple
from app.agents.base import BaseAgent


class EditorAgent(BaseAgent):
    """
    Editor agent responsible for revising drafts
    编辑，负责修订草稿
    """
    
    def get_agent_name(self) -> str:
        """Get agent name / 获取 Agent 名称"""
        return "editor"
    
    def get_system_prompt(self) -> str:
        """Get system prompt / 获取系统提示词"""
        return """You are an Editor agent for novel writing.

Your responsibilities:
1. Fix issues identified in the review
2. Polish the prose while maintaining the author's voice
3. Ensure smooth pacing and flow
4. Preserve all good elements from the original draft

Priorities:
- Fix critical issues first (contradictions, boundary violations)
- Then fix moderate issues (style, minor inconsistencies)
- Consider minor issues if they improve the draft
- DO NOT introduce new issues while fixing old ones

Output: Revised draft with clear, engaging prose

你是一个小说编辑。

职责：
1. 修复审稿中识别的问题
2. 润色文字，同时保持作者的声音
3. 确保流畅的节奏和流动性
4. 保留原稿中所有优秀的元素

优先级：
- 首先修复严重问题（矛盾、违反边界）
- 然后修复中等问题（文风、轻微不一致）
- 如果能改进草稿则考虑轻微问题
- 修复旧问题时不要引入新问题

输出：清晰、引人入胜的修订稿"""
    
    async def execute(
        self,
        project_id: str,
        chapter: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Revise draft based on review
        根据审稿意见修订草稿
        
        Args:
            project_id: Project ID / 项目ID
            chapter: Chapter ID / 章节ID
            context: Context with draft_version, user_feedback / 包含草稿版本和用户反馈的上下文
            
        Returns:
            Result with revised draft / 包含修订稿的结果
        """
        # Load draft / 加载草稿
        draft_version = context.get("draft_version", "v1")
        draft = await self.draft_storage.get_draft(project_id, chapter, draft_version)
        
        if not draft:
            return {
                "success": False,
                "error": f"Draft {draft_version} not found"
            }
        
        # Load review / 加载审稿意见
        review = await self.draft_storage.get_review(project_id, chapter)
        
        if not review:
            return {
                "success": False,
                "error": "Review not found"
            }
        
        # Load style card / 加载文风卡
        style_card = await self.card_storage.get_style_card(project_id)
        
        # Get user feedback if provided / 获取用户反馈（如果有）
        user_feedback = context.get("user_feedback", "")
        
        # Generate edit instructions / 生成编辑指令
        edit_instructions_raw = await self._generate_edit_instructions(
            original_draft=draft.content,
            review=review,
            style_card=style_card,
            user_feedback=user_feedback
        )

        # Parse and apply edit instructions / 解析并应用编辑指令
        try:
            edit_instructions = self._parse_edit_instructions(edit_instructions_raw)
            revised_content, change_rate = self._apply_edit_instructions(
                original_draft=draft.content,
                instructions=edit_instructions
            )
            
            # Validate change magnitude / 验证改动幅度
            critical_moderate_count = len([i for i in review.issues if i.severity in ['critical', 'moderate']])
            if critical_moderate_count > 0 and change_rate < 0.02:
                # Change rate too low with outstanding issues, reject and retry
                # 改动幅度过低且仍有严重/中等问题，拒绝并重试
                print(f"[Editor] Change rate {change_rate:.2%} too low with {critical_moderate_count} critical/moderate issues, requesting more substantial edits")
                # For now, accept but log warning; future: implement retry logic
                # 目前接受但记录警告；未来：实现重试逻辑
        except Exception as e:
            print(f"[Editor] Failed to parse/apply edit instructions: {e}, falling back to direct revision")
            # Fallback: try to extract revised_draft directly
            # 回退：尝试直接提取 revised_draft
            revised_content = self._extract_revised_draft_fallback(edit_instructions_raw)
        
        # Calculate new version number / 计算新版本号
        new_version = self._increment_version(draft_version)
        
        # Calculate word count / 计算字数
        word_count = len(revised_content)
        
        # Save revised draft / 保存修订稿
        revised_draft = await self.draft_storage.save_draft(
            project_id=project_id,
            chapter=chapter,
            version=new_version,
            content=revised_content,
            word_count=word_count,
            pending_confirmations=[]
        )
        
        return {
            "success": True,
            "draft": revised_draft,
            "version": new_version,
            "word_count": word_count
        }

    def _parse_edit_instructions(self, content: str) -> List[Dict[str, Any]]:
        """Parse edit instructions from YAML response."""
        text = content
        if "```yaml" in text:
            yaml_start = text.find("```yaml") + 7
            yaml_end = text.find("```", yaml_start)
            text = text[yaml_start:yaml_end].strip()
        elif "```" in text:
            yaml_start = text.find("```") + 3
            yaml_end = text.find("```", yaml_start)
            text = text[yaml_start:yaml_end].strip()

        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("Invalid YAML structure")
        
        instructions = data.get("edit_instructions", [])
        if not isinstance(instructions, list):
            raise ValueError("edit_instructions must be a list")
        
        return instructions
    
    def _extract_revised_draft_fallback(self, content: str) -> str:
        """Fallback: extract revised_draft from YAML if edit_instructions parsing fails."""
        try:
            text = content
            if "```yaml" in text:
                yaml_start = text.find("```yaml") + 7
                yaml_end = text.find("```", yaml_start)
                text = text[yaml_start:yaml_end].strip()
            elif "```" in text:
                yaml_start = text.find("```") + 3
                yaml_end = text.find("```", yaml_start)
                text = text[yaml_start:yaml_end].strip()

            data = yaml.safe_load(text)
            revised = data.get("revised_draft") if isinstance(data, dict) else None
            if isinstance(revised, str) and revised.strip():
                return revised.strip()
        except Exception:
            pass

        return content.strip()
    
    def _split_into_paragraphs(self, text: str) -> List[str]:
        """Split text into paragraphs (by double newline or single newline for Chinese)."""
        # Split by double newline first
        paragraphs = re.split(r'\n\s*\n', text)
        # Further split single-newline separated lines if they're substantial
        result = []
        for para in paragraphs:
            para = para.strip()
            if para:
                result.append(para)
        return result
    
    def _apply_edit_instructions(
        self,
        original_draft: str,
        instructions: List[Dict[str, Any]]
    ) -> Tuple[str, float]:
        """Apply edit instructions to original draft and return (revised_draft, change_rate).
        
        Supported operations:
        - replace: replace paragraph at index with new_text
        - insert: insert new_text before paragraph at index
        - delete: delete paragraph at index
        - rewrite: rewrite paragraph at index with new_text (alias for replace)
        """
        paragraphs = self._split_into_paragraphs(original_draft)
        original_char_count = len(original_draft)
        changed_char_count = 0
        
        # Sort instructions by index descending to avoid index shifting issues
        sorted_instructions = sorted(
            instructions,
            key=lambda x: x.get('paragraph_index', 0),
            reverse=True
        )
        
        for instr in sorted_instructions:
            op = instr.get('operation', '').lower()
            idx = instr.get('paragraph_index', 0)
            new_text = instr.get('new_text', '').strip()
            
            if idx < 0 or idx >= len(paragraphs):
                print(f"[Editor] Invalid paragraph_index {idx}, skipping")
                continue
            
            if op in ['replace', 'rewrite']:
                old_para = paragraphs[idx]
                paragraphs[idx] = new_text
                changed_char_count += max(len(old_para), len(new_text))
            elif op == 'insert':
                paragraphs.insert(idx, new_text)
                changed_char_count += len(new_text)
            elif op == 'delete':
                old_para = paragraphs[idx]
                del paragraphs[idx]
                changed_char_count += len(old_para)
            else:
                print(f"[Editor] Unknown operation {op}, skipping")
        
        revised_draft = '\n\n'.join(paragraphs)
        change_rate = changed_char_count / original_char_count if original_char_count > 0 else 0
        
        return revised_draft, change_rate
    
    async def _generate_edit_instructions(
        self,
        original_draft: str,
        review: Any,
        style_card: Any,
        user_feedback: str
    ) -> str:
        """
        Generate edit instructions using LLM
        使用大模型生成编辑指令
        
        Args:
            original_draft: Original draft content / 原始草稿内容
            review: Review result / 审稿结果
            style_card: Style card / 文风卡
            user_feedback: User feedback / 用户反馈
            
        Returns:
            Edit instructions in YAML format / YAML 格式的编辑指令
        """
        # Build context / 构建上下文
        context_items = []
        
        # Split draft into numbered paragraphs for reference / 将草稿分段并编号以供引用
        paragraphs = self._split_into_paragraphs(original_draft)
        numbered_draft = []
        for idx, para in enumerate(paragraphs):
            numbered_draft.append(f"[Para {idx}]\n{para}")
        
        context_items.append("Original Draft (numbered by paragraph):\n" + "\n\n".join(numbered_draft))
        
        # Add review issues / 添加审稿问题
        if review.issues:
            issues_text = ["Review Issues to Fix:"]
            
            # Group by severity / 按严重程度分组
            critical = [i for i in review.issues if i.severity == "critical"]
            moderate = [i for i in review.issues if i.severity == "moderate"]
            minor = [i for i in review.issues if i.severity == "minor"]
            
            if critical:
                issues_text.append("\nCRITICAL (must fix):")
                for idx, issue in enumerate(critical, 1):
                    issues_text.append(
                        f"\n{idx}. [{issue.category}] at {issue.location}"
                    )
                    issues_text.append(f"   Problem: {issue.problem}")
                    issues_text.append(f"   Suggestion: {issue.suggestion}")
            
            if moderate:
                issues_text.append("\n\nMODERATE (should fix):")
                for idx, issue in enumerate(moderate, 1):
                    issues_text.append(
                        f"\n{idx}. [{issue.category}] at {issue.location}"
                    )
                    issues_text.append(f"   Problem: {issue.problem}")
                    issues_text.append(f"   Suggestion: {issue.suggestion}")
            
            if minor:
                issues_text.append("\n\nMINOR (optional):")
                for idx, issue in enumerate(minor, 1):
                    issues_text.append(
                        f"\n{idx}. [{issue.category}] at {issue.location}"
                    )
                    issues_text.append(f"   Problem: {issue.problem}")
                    issues_text.append(f"   Suggestion: {issue.suggestion}")
            
            context_items.append("".join(issues_text))
        
        # Add overall assessment / 添加总体评价
        if review.overall_assessment:
            context_items.append(f"Overall Assessment:\n{review.overall_assessment}")
        
        # Add user feedback / 添加用户反馈
        if user_feedback:
            context_items.append(f"User Feedback:\n{user_feedback}")
        
        # Add style requirements / 添加文风要求
        if style_card:
            context_items.append(f"""Style Requirements:
- Narrative Distance: {style_card.narrative_distance}
- Pacing: {style_card.pacing}
- Sentence Structure: {style_card.sentence_structure}""")
        
        # Build user prompt / 构建用户提示
        user_prompt = """Revise the draft by providing structured edit instructions.

Instructions:
1. Fix all CRITICAL issues - these are mandatory
2. Fix MODERATE issues where possible
3. Consider MINOR issues if they improve the draft
4. Incorporate user feedback if provided
5. For each issue, specify the paragraph index and the edit operation
6. Operations: 'replace' (rewrite paragraph), 'insert' (add new paragraph before index), 'delete' (remove paragraph)
7. You MUST make substantial changes for CRITICAL/MODERATE issues - no synonym swaps
8. Each edit must be concrete and verifiable

Output ONLY in this YAML format:
```yaml
edit_instructions:
  - operation: replace|insert|delete
    paragraph_index: <number>
    reason: "fix [severity] issue: [brief description]"
    new_text: |
      <the new/replacement paragraph text, omit for delete>
  - operation: ...
    paragraph_index: ...
    reason: ...
    new_text: |
      ...
```

Example:
```yaml
edit_instructions:
  - operation: replace
    paragraph_index: 3
    reason: "fix critical logic issue: protagonist's motivation unclear"
    new_text: |
      许昊龙握紧拳头，指甲深掐进掌心。他知道自己只是个凡人，但阿梓是他唯一的亲人。就算前路是刀山火海，他也要去闯一闯。
  - operation: insert
    paragraph_index: 5
    reason: "fix moderate pacing issue: add transition"
    new_text: |
      夜色渐深，山风呼啸。
  - operation: delete
    paragraph_index: 8
    reason: "fix minor style issue: redundant description"
```

修订草稿，提供结构化编辑指令。

指示：
1. 修复所有严重问题 - 这是强制性的
2. 尽可能修复中等问题
3. 如果能改进草稿则考虑轻微问题
4. 如果提供了用户反馈则纳入考虑
5. 对每个问题，指定段落索引和编辑操作
6. 操作类型：'replace'（重写段落）、'insert'（在索引前插入新段落）、'delete'（删除段落）
7. 对严重/中等问题必须做实质性修改 - 不能只换同义词
8. 每次编辑都必须具体且可验证

只允许输出以下 YAML 格式：
```yaml
edit_instructions:
  - operation: replace|insert|delete
    paragraph_index: <数字>
    reason: "修复 [严重程度] 问题：[简要描述]"
    new_text: |
      <新段落/替换段落文本，delete 操作时省略>
```"""
        
        # Call LLM / 调用大模型
        messages = self.build_messages(
            system_prompt=self.get_system_prompt(),
            user_prompt=user_prompt,
            context_items=context_items
        )
        
        return await self.call_llm(messages)
    
    def _increment_version(self, current_version: str) -> str:
        """
        Increment version number
        递增版本号
        
        Args:
            current_version: Current version (e.g., "v1") / 当前版本
            
        Returns:
            New version (e.g., "v2") / 新版本
        """
        try:
            num = int(current_version.replace("v", ""))
            return f"v{num + 1}"
        except:
            return "v2"
