"""Tests for FileWrite/FileEdit derived events and the files_modified accumulator."""

from claudestream._protocol import flatten_event, _resolve_path
from claudestream.events import (
    AssistantMessage,
    AssistantText,
    FileEdit,
    FileWrite,
    TextBlock,
    ToolUse,
    ToolUseBlock,
)


class TestResolvePathHelper:
    def test_absolute_path_unchanged(self):
        assert _resolve_path("/home/user/file.py", "/some/cwd") == "/home/user/file.py"

    def test_relative_path_resolved_with_cwd(self):
        assert _resolve_path("src/main.py", "/home/user/project") == "/home/user/project/src/main.py"

    def test_relative_path_without_cwd_unchanged(self):
        assert _resolve_path("src/main.py", None) == "src/main.py"

    def test_empty_path_returns_empty(self):
        assert _resolve_path("", "/some/cwd") == ""

    def test_normpath_applied(self):
        result = _resolve_path("/home/user/../user/file.py", "/cwd")
        assert result == "/home/user/file.py"

    def test_normpath_applied_to_relative(self):
        result = _resolve_path("src/../lib/file.py", "/home/user")
        assert result == "/home/user/lib/file.py"


class TestFileWriteDerived:
    def test_write_tool_emits_file_write(self):
        event = AssistantMessage(
            type="assistant",
            session_id="s1",
            uuid="u1",
            content=[
                ToolUseBlock(id="t1", name="Write", input={
                    "file_path": "/home/user/file.py",
                    "content": "hello world",
                }),
            ],
        )
        flat = flatten_event(event)
        assert len(flat) == 2
        assert isinstance(flat[0], ToolUse)
        assert flat[0].name == "Write"
        assert isinstance(flat[1], FileWrite)
        assert flat[1].type == "file_write"
        assert flat[1].path == "/home/user/file.py"
        assert flat[1].content_length == 11
        assert flat[1].session_id == "s1"
        assert flat[1].uuid == "u1"

    def test_write_tool_with_relative_path_and_cwd(self):
        event = AssistantMessage(
            type="assistant",
            content=[
                ToolUseBlock(id="t1", name="Write", input={
                    "file_path": "src/main.py",
                    "content": "x",
                }),
            ],
        )
        flat = flatten_event(event, cwd="/home/user/project")
        file_write = [e for e in flat if isinstance(e, FileWrite)][0]
        assert file_write.path == "/home/user/project/src/main.py"

    def test_write_tool_empty_content(self):
        event = AssistantMessage(
            type="assistant",
            content=[
                ToolUseBlock(id="t1", name="Write", input={
                    "file_path": "/tmp/empty.txt",
                }),
            ],
        )
        flat = flatten_event(event)
        file_write = [e for e in flat if isinstance(e, FileWrite)][0]
        assert file_write.content_length == 0
        assert file_write.path == "/tmp/empty.txt"


class TestFileEditDerived:
    def test_edit_tool_emits_file_edit(self):
        event = AssistantMessage(
            type="assistant",
            session_id="s1",
            uuid="u1",
            content=[
                ToolUseBlock(id="t1", name="Edit", input={
                    "file_path": "/home/user/file.py",
                    "old_string": "foo",
                    "new_string": "bar",
                }),
            ],
        )
        flat = flatten_event(event)
        assert len(flat) == 2
        assert isinstance(flat[0], ToolUse)
        assert isinstance(flat[1], FileEdit)
        assert flat[1].type == "file_edit"
        assert flat[1].path == "/home/user/file.py"

    def test_edit_tool_with_relative_path_and_cwd(self):
        event = AssistantMessage(
            type="assistant",
            content=[
                ToolUseBlock(id="t1", name="Edit", input={
                    "file_path": "lib/util.py",
                }),
            ],
        )
        flat = flatten_event(event, cwd="/project")
        file_edit = [e for e in flat if isinstance(e, FileEdit)][0]
        assert file_edit.path == "/project/lib/util.py"


class TestMultiEditDerived:
    def test_multiedit_emits_one_per_unique_file(self):
        event = AssistantMessage(
            type="assistant",
            session_id="s1",
            content=[
                ToolUseBlock(id="t1", name="MultiEdit", input={
                    "edits": [
                        {"file_path": "/home/user/a.py", "old_string": "x", "new_string": "y"},
                        {"file_path": "/home/user/b.py", "old_string": "x", "new_string": "y"},
                        {"file_path": "/home/user/a.py", "old_string": "z", "new_string": "w"},
                    ],
                }),
            ],
        )
        flat = flatten_event(event)
        assert isinstance(flat[0], ToolUse)
        file_edits = [e for e in flat if isinstance(e, FileEdit)]
        assert len(file_edits) == 2
        paths = {e.path for e in file_edits}
        assert paths == {"/home/user/a.py", "/home/user/b.py"}

    def test_multiedit_falls_back_to_toplevel_file_path(self):
        event = AssistantMessage(
            type="assistant",
            content=[
                ToolUseBlock(id="t1", name="MultiEdit", input={
                    "file_path": "/home/user/single.py",
                    "edits": [
                        {"old_string": "x", "new_string": "y"},
                    ],
                }),
            ],
        )
        flat = flatten_event(event)
        file_edits = [e for e in flat if isinstance(e, FileEdit)]
        assert len(file_edits) == 1
        assert file_edits[0].path == "/home/user/single.py"

    def test_multiedit_no_edits_with_toplevel_path(self):
        event = AssistantMessage(
            type="assistant",
            content=[
                ToolUseBlock(id="t1", name="MultiEdit", input={
                    "file_path": "/home/user/fallback.py",
                }),
            ],
        )
        flat = flatten_event(event)
        file_edits = [e for e in flat if isinstance(e, FileEdit)]
        assert len(file_edits) == 1
        assert file_edits[0].path == "/home/user/fallback.py"

    def test_multiedit_with_relative_paths_and_cwd(self):
        event = AssistantMessage(
            type="assistant",
            content=[
                ToolUseBlock(id="t1", name="MultiEdit", input={
                    "edits": [
                        {"file_path": "src/a.py", "old_string": "x", "new_string": "y"},
                        {"file_path": "src/b.py", "old_string": "x", "new_string": "y"},
                    ],
                }),
            ],
        )
        flat = flatten_event(event, cwd="/project")
        file_edits = [e for e in flat if isinstance(e, FileEdit)]
        assert len(file_edits) == 2
        paths = {e.path for e in file_edits}
        assert paths == {"/project/src/a.py", "/project/src/b.py"}


class TestToolUseStillEmitted:
    """Verify that ToolUse events are still emitted alongside derived file events."""

    def test_write_emits_tooluse_and_filewrite(self):
        event = AssistantMessage(
            type="assistant",
            content=[
                ToolUseBlock(id="t1", name="Write", input={
                    "file_path": "/f.py", "content": "abc",
                }),
            ],
        )
        flat = flatten_event(event)
        types = [type(e) for e in flat]
        assert types == [ToolUse, FileWrite]

    def test_edit_emits_tooluse_and_fileedit(self):
        event = AssistantMessage(
            type="assistant",
            content=[
                ToolUseBlock(id="t1", name="Edit", input={"file_path": "/f.py"}),
            ],
        )
        flat = flatten_event(event)
        types = [type(e) for e in flat]
        assert types == [ToolUse, FileEdit]

    def test_non_file_tool_no_derived_events(self):
        event = AssistantMessage(
            type="assistant",
            content=[
                ToolUseBlock(id="t1", name="Bash", input={"command": "ls"}),
            ],
        )
        flat = flatten_event(event)
        assert len(flat) == 1
        assert isinstance(flat[0], ToolUse)

    def test_mixed_content_blocks(self):
        """Text, Write tool, and Bash tool in one message."""
        event = AssistantMessage(
            type="assistant",
            content=[
                TextBlock(text="I will write a file"),
                ToolUseBlock(id="t1", name="Write", input={
                    "file_path": "/f.py", "content": "code",
                }),
                ToolUseBlock(id="t2", name="Bash", input={"command": "ls"}),
            ],
        )
        flat = flatten_event(event)
        types = [type(e) for e in flat]
        assert types == [AssistantText, ToolUse, FileWrite, ToolUse]


class TestSessionAccumulator:
    """Test the _files_modified accumulator on AsyncSession."""

    def test_files_modified_accumulates(self):
        from tests.conftest import make_test_session

        session = make_test_session()

        # Simulate what _read_turn does when it encounters file events
        session._files_modified.add("/home/user/a.py")
        session._files_modified.add("/home/user/b.py")
        session._files_modified.add("/home/user/a.py")  # duplicate

        result = session.files_modified
        assert result == {"/home/user/a.py", "/home/user/b.py"}
        # Returns a copy
        assert result is not session._files_modified

    def test_files_modified_empty_initially(self):
        from tests.conftest import make_test_session

        session = make_test_session()
        assert session.files_modified == set()
