"""Contracts for the Writer clarification-tool policy."""

import asyncio
from types import SimpleNamespace

import yaml

from app.routers import session as session_router
from app.services.clarify_settings import MAX_CLARIFICATION_QUESTIONS, resolve_clarify_settings


def test_question_count_is_a_fixed_protocol_limit_not_a_project_setting():
    settings = resolve_clarify_settings(
        {
            "writer": {"clarification": {"auto_trigger": "always", "max_questions": 1}},
            "clarify": {"max_questions": 9},
        }
    )

    assert settings["auto_trigger"] == "always"
    assert settings["max_questions"] == MAX_CLARIFICATION_QUESTIONS == 3


def test_clarification_mode_only_changes_writer_policy():
    assert resolve_clarify_settings({"writer": {"clarification": {"auto_trigger": "off"}}})["mode"] == "off"


def test_namespaced_writer_policy_overrides_legacy_project_alias():
    settings = resolve_clarify_settings(
        {"clarify": {"auto_trigger": "off"}, "writer": {"clarification": {"auto_trigger": "always"}}}
    )

    assert settings["auto_trigger"] == "always"


def test_settings_endpoint_persists_only_the_writer_trigger_policy(monkeypatch, tmp_path):
    project_file = tmp_path / "project.yaml"
    project_file.write_text("name: demo\nclarify:\n  auto_trigger: 'off'\n", encoding="utf-8")

    class _CardStorage:
        @staticmethod
        def get_project_path(_project_id):
            return tmp_path

        @staticmethod
        async def read_yaml(path):
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        @staticmethod
        async def write_yaml(path, data):
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        session_router,
        "get_orchestrator",
        lambda _project_id: SimpleNamespace(card_storage=_CardStorage()),
    )

    result = asyncio.run(
        session_router.update_clarify_settings(
            "demo",
            session_router.ClarifySettingsRequest(auto_trigger="always"),
        )
    )
    saved = yaml.safe_load(project_file.read_text(encoding="utf-8"))

    assert result["settings"]["auto_trigger"] == "always"
    assert saved["name"] == "demo"
    assert saved["clarify"]["auto_trigger"] == "off"
    assert saved["writer"]["clarification"] == {"auto_trigger": "always"}
