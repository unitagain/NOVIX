"""Basic storage read/write tests using tmp_path."""

import pytest
from app.storage.base import BaseStorage


@pytest.fixture
def storage(tmp_path):
    return BaseStorage(data_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_write_and_read_yaml(storage, tmp_path):
    project_dir = tmp_path / "test_proj"
    project_dir.mkdir()
    filepath = project_dir / "test.yaml"
    data = {"name": "test", "value": 42}
    await storage.write_yaml(filepath, data)
    assert filepath.exists()
    result = await storage.read_yaml(filepath)
    assert result["name"] == "test"
    assert result["value"] == 42


@pytest.mark.asyncio
async def test_read_yaml_missing_raises(storage, tmp_path):
    filepath = tmp_path / "nonexistent.yaml"
    with pytest.raises(FileNotFoundError):
        await storage.read_yaml(filepath)


@pytest.mark.asyncio
async def test_write_and_read_text(storage, tmp_path):
    project_dir = tmp_path / "test_proj"
    project_dir.mkdir()
    filepath = project_dir / "chapter.md"
    content = "# Chapter 1\n\nHello world."
    await storage.write_text(filepath, content)
    assert filepath.exists()
    result = await storage.read_text(filepath)
    assert "Hello world" in result
