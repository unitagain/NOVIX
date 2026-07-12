"""P10 correctness tests for turn isolation and storage transactions."""

from __future__ import annotations

import asyncio
import multiprocessing
from datetime import datetime

import pytest

from app.context_engine.context_plan import ContextPlanV2
from app.context_engine.select_engine import ContextSelectEngine
from app.context_engine.trace_collector import TraceCollector, TraceEventType
from app.context_engine.turn_scope import bind_turn_scope, new_turn_scope
from app.schemas.draft import Draft
from app.storage.creative_memory import CreativeMemoryStorage
from app.storage.drafts import DraftStorage
from app.storage.session_history import SessionHistoryStorage


def _append_messages_in_process(data_dir: str, prefix: str, count: int) -> None:
    storage = SessionHistoryStorage(data_dir)

    async def run() -> None:
        for index in range(count):
            await storage.append("p", {"role": "user", "content": f"{prefix}-{index}"})

    asyncio.run(run())


def _plan(turn_id: str, *, tools: tuple[str, ...] = ()) -> ContextPlanV2:
    return ContextPlanV2(
        plan_id=f"plan_{turn_id}",
        turn_id=turn_id,
        project_id="p",
        chapter_id="V1C001",
        intent="write",
        route_path="agentic_writer",
        context_epoch=turn_id,
        budget={"input_tokens": 1000, "output_reserve_tokens": 100},
        tool_loadout=[{"name": name} for name in tools],
    )


def test_turn_scoped_trace_does_not_mix_interleaved_events():
    tracer = TraceCollector()

    async def run_one(label: str):
        scope = new_turn_scope(project_id="p", turn_id=f"turn_{label}")
        scope.activate_plan(_plan(scope.turn_id))
        with bind_turn_scope(scope):
            await tracer.record(TraceEventType.LLM_REQUEST, label, {"label": label})
            await asyncio.sleep(0)
            await tracer.record(TraceEventType.LLM_RESPONSE, label, {"label": label})
        return scope

    async def run_all():
        return await asyncio.gather(*(run_one(str(index)) for index in range(100)))

    scopes = asyncio.run(run_all())
    assert len({scope.trace_id for scope in scopes}) == 100
    for scope in scopes:
        assert len(scope.trace_events) == 2
        assert {event.trace_id for event in scope.trace_events} == {scope.trace_id}
        assert tracer.summarize_turn(scope)["event_count"] == 2


def test_same_agent_name_has_independent_agent_traces_per_turn():
    tracer = TraceCollector()

    async def run_one(label: str):
        scope = new_turn_scope(project_id="p", turn_id=f"turn_agent_{label}")
        with bind_turn_scope(scope):
            await tracer.start_agent_trace("writer", label)
            await asyncio.sleep(0)
            await tracer.end_agent_trace("writer")
        return scope

    async def run_all():
        return await asyncio.gather(run_one("a"), run_one("b"))

    first, second = asyncio.run(run_all())
    first_traces = tracer.get_all_traces(trace_id=first.trace_id)
    second_traces = tracer.get_all_traces(trace_id=second.trace_id)
    assert len(first_traces) == 1 and first_traces[0]["session_id"] == "a"
    assert len(second_traces) == 1 and second_traces[0]["session_id"] == "b"


def test_context_plan_rejects_unplanned_tools_and_cancelled_turn():
    scope = new_turn_scope(project_id="p", turn_id="turn_guard")
    scope.activate_plan(_plan(scope.turn_id, tools=("lookup_card",)))
    with bind_turn_scope(scope):
        with pytest.raises(PermissionError, match="disallowed_tools"):
            scope.prepare_model_request(
                messages=[{"role": "user", "content": "x"}],
                provider="deepseek",
                temperature=0.1,
                max_tokens=50,
                tools=[{"type": "function", "function": {"name": "write_content"}}],
            )
        scope.cancel()
        with pytest.raises(RuntimeError, match="turn_cancelled"):
            scope.prepare_model_request(
                messages=[{"role": "user", "content": "x"}],
                provider="deepseek",
                temperature=0.1,
                max_tokens=50,
                tools=None,
            )


def test_ranking_trace_is_context_local_when_tasks_interleave():
    engine = ContextSelectEngine()

    async def run_one(label: str):
        engine._set_ranking_trace({"query": label})
        await asyncio.sleep(0)
        return engine.get_last_ranking_trace()

    async def run_all():
        return await asyncio.gather(run_one("a"), run_one("b"))

    first, second = asyncio.run(run_all())
    assert first["query"] == "a"
    assert second["query"] == "b"


def test_compact_preserves_append_that_arrives_while_summarizing(tmp_path):
    storage = SessionHistoryStorage(str(tmp_path))

    async def scenario():
        for index in range(30):
            await storage.append("p", {"role": "user", "content": f"m{index}"})
        started = asyncio.Event()
        release = asyncio.Event()

        async def summarizer(_messages):
            started.set()
            await release.wait()
            return "summary"

        compact_task = asyncio.create_task(storage.compact("p", summarizer, keep_recent=10, trigger_at=20))
        await started.wait()
        await storage.append("p", {"role": "user", "content": "concurrent"})
        release.set()
        result = await compact_task
        return result, await storage.load("p")

    result, items = asyncio.run(scenario())
    assert result["compacted"] is True
    assert result["preserved_concurrent_appends"] == 1
    assert items[-1]["content"] == "concurrent"


def test_cross_process_jsonl_appends_are_not_lost(tmp_path):
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_append_messages_in_process, args=(str(tmp_path), prefix, 30))
        for prefix in ("a", "b")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    items = asyncio.run(SessionHistoryStorage(str(tmp_path)).load("p"))
    assert len(items) == 60
    assert len({item["content"] for item in items}) == 60


def test_draft_read_repairs_stale_metadata_from_authoritative_prose(tmp_path):
    storage = DraftStorage(str(tmp_path))

    async def scenario():
        await storage.save_draft("p", "V1C001", "v1", "old", 3)
        draft_path = tmp_path / "p" / "drafts" / "V1C001" / "draft_v1.md"
        await storage.write_text(draft_path, "new authoritative prose")
        return await storage.get_draft("p", "V1C001", "v1")

    draft = asyncio.run(scenario())
    assert isinstance(draft, Draft)
    assert draft.content == "new authoritative prose"
    assert draft.word_count == len(draft.content)


def test_concurrent_memory_writes_keep_rebuildable_index(tmp_path):
    first = CreativeMemoryStorage(str(tmp_path))
    second = CreativeMemoryStorage(str(tmp_path))

    async def scenario():
        await asyncio.gather(
            *(
                (first if index % 2 else second).write_memory(
                    "p",
                    f"memory-{index}",
                    f"desc {index}",
                    f"body {index}",
                    updated_at=datetime.now().astimezone().isoformat(),
                )
                for index in range(20)
            )
        )
        return await first.read_index("p"), await first.list_headers("p")

    index, headers = asyncio.run(scenario())
    assert len(headers) == 20
    assert all(f"memory-{number}" in index for number in range(20))
