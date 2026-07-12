# -*- coding: utf-8 -*-
"""P8 procedural knowledge / skill loadout planner.

Skills are project knowledge files such as style guides, genre conventions, or
writing checklists. This planner only selects metadata for JIT loading; it does
not execute skills or keep them resident in every prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence


@dataclass(frozen=True)
class ProceduralSkill:
    """Metadata for one JIT-loadable procedural knowledge file."""

    name: str
    description: str
    path: str
    applies_to: Sequence[str]
    context_cost: int = 300

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "applies_to": list(self.applies_to),
            "context_cost": self.context_cost,
        }


DEFAULT_PROCEDURAL_SKILLS: List[ProceduralSkill] = [
    ProceduralSkill(
        name="style_guide",
        description="Project prose style guide and voice constraints.",
        path="memory/skills/style_guide.md",
        applies_to=("write", "edit", "continue", "review"),
        context_cost=420,
    ),
    ProceduralSkill(
        name="continuity_checklist",
        description="Continuity checklist for character state, timeline, and foreshadowing.",
        path="memory/skills/continuity_checklist.md",
        applies_to=("write", "edit", "review"),
        context_cost=360,
    ),
    ProceduralSkill(
        name="genre_conventions",
        description="Genre conventions and scene-level expectations.",
        path="memory/skills/genre_conventions.md",
        applies_to=("plan", "write", "continue"),
        context_cost=320,
    ),
]


def plan_skill_loadout(
    intent: str,
    *,
    available_skills: Iterable[ProceduralSkill] | None = None,
    max_context_cost: int = 800,
) -> Dict[str, Any]:
    """Select skills for one task under a context budget."""

    normalized = str(intent or "write").strip().lower()
    selected: List[ProceduralSkill] = []
    total_cost = 0
    for skill in available_skills or DEFAULT_PROCEDURAL_SKILLS:
        if normalized not in {str(item).lower() for item in skill.applies_to}:
            continue
        if total_cost + int(skill.context_cost) > max_context_cost:
            continue
        selected.append(skill)
        total_cost += int(skill.context_cost)
    return {
        "mode": "jit_skill_loadout",
        "intent": normalized,
        "max_context_cost": int(max_context_cost),
        "context_cost": total_cost,
        "skills": [skill.to_dict() for skill in selected],
        "resident": False,
    }
