import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.tools.filesystem import (
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    ListDirectoryTool,
    GlobTool,
    SearchCodeTool,
    _resolve_safe_path,
)
from src.tools.registry import ToolRegistry, ToolResult, Tool


@pytest.fixture
def tmp_workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


async def test_read_file_tool_success(tmp_workdir):
    test_file = tmp_workdir / "test.txt"
    test_file.write_text("Hello, World!", encoding="utf-8")

    tool = ReadFileTool()
    result = await tool.execute(path="test.txt")

    assert result.success is True
    assert result.output == "Hello, World!"
    assert result.error == ""


async def test_read_file_tool_not_found(tmp_workdir):
    tool = ReadFileTool()
    result = await tool.execute(path="nonexistent.txt")

    assert result.success is False
    assert "File not found" in result.error


async def test_read_file_tool_directory(tmp_workdir):
    os.makedirs(tmp_workdir / "subdir", exist_ok=True)

    tool = ReadFileTool()
    result = await tool.execute(path="subdir")

    assert result.success is False
    assert "Not a file" in result.error


async def test_write_file_tool_success(tmp_workdir):
    tool = WriteFileTool()
    result = await tool.execute(path="output.txt", content="Test content")

    assert result.success is True
    assert "written successfully" in result.output
    assert (tmp_workdir / "output.txt").read_text(encoding="utf-8") == "Test content"


async def test_write_file_tool_nested_directory(tmp_workdir):
    tool = WriteFileTool()
    result = await tool.execute(path="a/b/c.txt", content="nested")

    assert result.success is True
    assert (tmp_workdir / "a" / "b" / "c.txt").read_text(encoding="utf-8") == "nested"


async def test_edit_file_tool_success(tmp_workdir):
    test_file = tmp_workdir / "edit.txt"
    test_file.write_text("Hello, old text!", encoding="utf-8")

    tool = EditFileTool()
    result = await tool.execute(path="edit.txt", old_text="old text", new_text="new text")

    assert result.success is True
    assert result.data["replacements"] == 1
    assert test_file.read_text(encoding="utf-8") == "Hello, new text!"


async def test_edit_file_tool_multiple_replacements(tmp_workdir):
    test_file = tmp_workdir / "multi.txt"
    test_file.write_text("foo foo foo", encoding="utf-8")

    tool = EditFileTool()
    result = await tool.execute(path="multi.txt", old_text="foo", new_text="bar")

    assert result.success is True
    assert result.data["replacements"] == 3
    assert test_file.read_text(encoding="utf-8") == "bar bar bar"


async def test_edit_file_tool_old_text_not_found(tmp_workdir):
    test_file = tmp_workdir / "edit2.txt"
    test_file.write_text("Hello, World!", encoding="utf-8")

    tool = EditFileTool()
    result = await tool.execute(path="edit2.txt", old_text="nonexistent", new_text="replaced")

    assert result.success is False
    assert "old_text not found" in result.error


async def test_edit_file_tool_file_not_found(tmp_workdir):
    tool = EditFileTool()
    result = await tool.execute(path="noexist.txt", old_text="a", new_text="b")

    assert result.success is False
    assert "File not found" in result.error


async def test_list_directory_tool_success(tmp_workdir):
    (tmp_workdir / "file1.txt").write_text("a", encoding="utf-8")
    (tmp_workdir / "file2.txt").write_text("b", encoding="utf-8")
    os.makedirs(tmp_workdir / "subdir", exist_ok=True)

    tool = ListDirectoryTool()
    result = await tool.execute(path=".")

    assert result.success is True
    assert "file1.txt" in result.output
    assert "file2.txt" in result.output
    assert "subdir/" in result.output
    assert len(result.data["entries"]) == 3


async def test_list_directory_tool_empty(tmp_workdir):
    os.makedirs(tmp_workdir / "empty_dir", exist_ok=True)

    tool = ListDirectoryTool()
    result = await tool.execute(path="empty_dir")

    assert result.success is True
    assert result.output == "(empty directory)"
    assert result.data["entries"] == []


async def test_list_directory_tool_not_found(tmp_workdir):
    tool = ListDirectoryTool()
    result = await tool.execute(path="no_dir")

    assert result.success is False
    assert "Directory not found" in result.error


async def test_glob_tool_match_files(tmp_workdir):
    (tmp_workdir / "test1.py").write_text("", encoding="utf-8")
    (tmp_workdir / "test2.py").write_text("", encoding="utf-8")
    (tmp_workdir / "readme.md").write_text("", encoding="utf-8")

    tool = GlobTool()
    result = await tool.execute(pattern="*.py")

    assert result.success is True
    assert "test1.py" in result.output
    assert "test2.py" in result.output
    assert "readme.md" not in result.output
    assert len(result.data["matches"]) == 2


async def test_glob_tool_no_matches(tmp_workdir):
    tool = GlobTool()
    result = await tool.execute(pattern="*.xyz")

    assert result.success is True
    assert result.output == "(no matches)"
    assert result.data["matches"] == []


async def test_glob_tool_nested_pattern(tmp_workdir):
    os.makedirs(tmp_workdir / "src", exist_ok=True)
    (tmp_workdir / "src" / "main.py").write_text("", encoding="utf-8")
    (tmp_workdir / "src" / "utils.py").write_text("", encoding="utf-8")

    tool = GlobTool()
    result = await tool.execute(pattern="src/*.py")

    assert result.success is True
    assert len(result.data["matches"]) == 2


async def test_search_code_tool_matches(tmp_workdir):
    test_file = tmp_workdir / "code.py"
    test_file.write_text("def hello():\n    print('hello world')\n    return True\n", encoding="utf-8")

    tool = SearchCodeTool()
    result = await tool.execute(pattern="hello", path="code.py")

    assert result.success is True
    assert len(result.data["matches"]) == 2
    assert "code.py:1:" in result.output
    assert "code.py:2:" in result.output


async def test_search_code_tool_directory(tmp_workdir):
    os.makedirs(tmp_workdir / "proj", exist_ok=True)
    (tmp_workdir / "proj" / "a.py").write_text("FOO = 1\n", encoding="utf-8")
    (tmp_workdir / "proj" / "b.py").write_text("BAR = 2\n", encoding="utf-8")
    (tmp_workdir / "proj" / "c.txt").write_text("FOO in text\n", encoding="utf-8")

    tool = SearchCodeTool()
    result = await tool.execute(pattern="FOO", path="proj")

    assert result.success is True
    assert len(result.data["matches"]) == 2


async def test_search_code_tool_no_matches(tmp_workdir):
    test_file = tmp_workdir / "code.py"
    test_file.write_text("def test():\n    pass\n", encoding="utf-8")

    tool = SearchCodeTool()
    result = await tool.execute(pattern="nonexistent_pattern_xyz", path="code.py")

    assert result.success is True
    assert result.output == "(no matches)"
    assert result.data["matches"] == []


async def test_search_code_tool_invalid_regex(tmp_workdir):
    tool = SearchCodeTool()
    result = await tool.execute(pattern="[invalid", path=".")

    assert result.success is False
    assert "Invalid regex pattern" in result.error


async def test_search_code_tool_skips_hidden_files(tmp_workdir):
    os.makedirs(tmp_workdir / ".git", exist_ok=True)
    (tmp_workdir / ".git" / "config").write_text("SECRET=foo\n", encoding="utf-8")
    (tmp_workdir / ".hidden_file").write_text("foo\n", encoding="utf-8")
    (tmp_workdir / "visible.py").write_text("foo\n", encoding="utf-8")

    tool = SearchCodeTool()
    result = await tool.execute(pattern="foo", path=".")

    assert result.success is True
    assert len(result.data["matches"]) == 1
    assert "visible.py" in result.output


def test_tool_result_to_str_success():
    result = ToolResult(success=True, output="all good")
    assert result.to_str() == "all good"


def test_tool_result_to_str_error():
    result = ToolResult(success=False, error="something wrong")
    assert result.to_str() == "Error: something wrong"


def test_tool_registry_register_and_get():
    registry = ToolRegistry()
    tool = ReadFileTool()
    registry.register(tool)

    assert registry.get("read_file") is tool
    assert registry.get("nonexistent") is None


def test_tool_registry_list_tools():
    registry = ToolRegistry()
    t1 = ReadFileTool()
    t2 = WriteFileTool()
    registry.register(t1)
    registry.register(t2)

    assert len(registry.list_tools()) == 2
    assert set(registry.list_tool_names()) == {"read_file", "write_file"}


def test_tool_registry_unregister():
    registry = ToolRegistry()
    tool = ReadFileTool()
    registry.register(tool)
    assert registry.get("read_file") is not None

    registry.unregister("read_file")
    assert registry.get("read_file") is None


def test_tool_registry_unregister_nonexistent():
    registry = ToolRegistry()
    registry.unregister("no_such_tool")


async def test_tool_registry_execute_success(tmp_workdir):
    (tmp_workdir / "f.txt").write_text("content", encoding="utf-8")

    registry = ToolRegistry()
    registry.register(ReadFileTool())

    result = await registry.execute("read_file", {"path": "f.txt"})
    assert result.success is True
    assert result.output == "content"


async def test_tool_registry_execute_tool_not_found():
    registry = ToolRegistry()
    result = await registry.execute("nonexistent", {})

    assert result.success is False
    assert "Tool 'nonexistent' not found" in result.error


async def test_tool_registry_execute_exception():
    class BadTool(Tool):
        name: str = "bad"
        description: str = ""
        parameters: dict = {}
        category: str = "general"

        async def execute(self, **kwargs):
            raise RuntimeError("boom")

    registry = ToolRegistry()
    registry.register(BadTool())
    result = await registry.execute("bad", {})

    assert result.success is False
    assert "boom" in result.error


def test_tool_to_definition():
    tool = ReadFileTool()
    definition = tool.to_definition()

    assert definition["type"] == "function"
    assert definition["function"]["name"] == "read_file"
    assert "description" in definition["function"]
    assert "parameters" in definition["function"]


def test_resolve_safe_path_within_base(tmp_workdir, monkeypatch):
    monkeypatch.chdir(tmp_workdir)
    (tmp_workdir / "sub").mkdir()
    (tmp_workdir / "sub" / "file.txt").write_text("test", encoding="utf-8")

    result = _resolve_safe_path("sub/file.txt")
    assert result.name == "file.txt"
    assert str(tmp_workdir) in str(result)


def test_resolve_safe_path_traversal_raises(tmp_workdir, monkeypatch):
    monkeypatch.chdir(tmp_workdir)

    with pytest.raises(ValueError, match="Path traversal detected"):
        _resolve_safe_path("../outside.txt")


def test_resolve_safe_path_absolute_traversal(tmp_workdir, monkeypatch):
    monkeypatch.chdir(tmp_workdir)

    with pytest.raises(ValueError, match="Path traversal detected"):
        _resolve_safe_path("/etc/passwd")
