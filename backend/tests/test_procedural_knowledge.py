# -*- coding: utf-8 -*-
"""P8 procedural knowledge loadout tests."""

from app.context_engine.procedural_knowledge import ProceduralSkill, plan_skill_loadout


def test_skill_loadout_selects_matching_skills_under_budget():
    loadout = plan_skill_loadout("write", max_context_cost=800)
    names = [item["name"] for item in loadout["skills"]]
    assert loadout["mode"] == "jit_skill_loadout"
    assert loadout["resident"] is False
    assert "style_guide" in names
    assert loadout["context_cost"] <= 800


def test_skill_loadout_skips_non_matching_and_over_budget_skills():
    skills = [
        ProceduralSkill("expensive", "Too large", "memory/skills/large.md", ("write",), context_cost=900),
        ProceduralSkill("plan_only", "Plan only", "memory/skills/plan.md", ("plan",), context_cost=100),
    ]
    loadout = plan_skill_loadout("write", available_skills=skills, max_context_cost=500)
    assert loadout["skills"] == []
