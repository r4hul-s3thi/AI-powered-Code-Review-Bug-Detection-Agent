"""
test_parser.py — Unit tests for parser.py semantic chunking logic.
"""

import pytest
from src.parser import (
    CodeChunk,
    _parse_diff_hunks,
    _extract_chunks_regex,
    extract_chunks_from_diff,
    TREE_SITTER_AVAILABLE,
)

SAMPLE_DIFF = """\
diff --git a/app/views.py b/app/views.py
index abc..def 100644
--- a/app/views.py
+++ b/app/views.py
@@ -10,6 +10,8 @@ def old_func():
     x = 1
+    y = 2
+    z = x + y
     return x
"""

def test_parse_diff_hunks_returns_correct_file():
    assert "app/views.py" in _parse_diff_hunks(SAMPLE_DIFF)

def test_parse_diff_hunks_correct_line_numbers():
    changed = _parse_diff_hunks(SAMPLE_DIFF)["app/views.py"]
    assert len(changed) == 2
    assert 11 in changed
    assert 12 in changed

def test_parse_diff_hunks_empty_diff():
    assert _parse_diff_hunks("") == {}

def test_parse_diff_hunks_no_additions():
    diff = "+++ b/foo.py\n@@ -1,3 +1,3 @@\n context\n-removed\n context\n"
    assert _parse_diff_hunks(diff).get("foo.py", []) == []


SOURCE = """\
def unchanged():
    pass

def modified_func():
    x = 1
    y = 2
    return x + y

class MyClass:
    def method(self):
        pass
"""

def test_regex_extracts_changed_function():
    names = [c.name for c in _extract_chunks_regex("foo.py", SOURCE, changed_lines=[5])]
    assert "modified_func" in names

def test_regex_skips_unchanged_function():
    names = [c.name for c in _extract_chunks_regex("foo.py", SOURCE, changed_lines=[5])]
    assert "unchanged" not in names

def test_regex_extracts_class():
    names = [c.name for c in _extract_chunks_regex("foo.py", SOURCE, changed_lines=[10])]
    assert "MyClass" in names

def test_regex_chunk_fields():
    chunks = _extract_chunks_regex("foo.py", SOURCE, changed_lines=[5])
    chunk = next(c for c in chunks if c.name == "modified_func")
    assert chunk.file == "foo.py"
    assert chunk.kind == "function"
    assert 5 in chunk.diff_lines
    assert "modified_func" in chunk.source


@pytest.mark.skipif(not TREE_SITTER_AVAILABLE, reason="tree-sitter not installed")
def test_ast_extracts_function():
    from src.parser import _extract_chunks_ast
    chunks = _extract_chunks_ast("test.py", "def foo():\n    x = 1\n    return x\n", [2])
    assert len(chunks) == 1
    assert chunks[0].name == "foo"
    assert chunks[0].kind == "function"

@pytest.mark.skipif(not TREE_SITTER_AVAILABLE, reason="tree-sitter not installed")
def test_ast_extracts_class():
    from src.parser import _extract_chunks_ast
    chunks = _extract_chunks_ast("test.py", "class Bar:\n    def method(self):\n        pass\n", [2])
    assert any(c.name == "Bar" for c in chunks)

@pytest.mark.skipif(not TREE_SITTER_AVAILABLE, reason="tree-sitter not installed")
def test_ast_module_fallback_for_top_level_change():
    from src.parser import _extract_chunks_ast
    chunks = _extract_chunks_ast("test.py", "X = 1\nY = 2\n", [2])
    assert len(chunks) == 1
    assert chunks[0].kind == "module"


def test_extract_chunks_from_diff_full_flow():
    diff = (
        "+++ b/sample.py\n"
        "@@ -1,3 +1,5 @@\n"
        " def foo():\n"
        "+    secret = 'abc123'\n"
        "+    return secret\n"
    )
    source = "def foo():\n    secret = 'abc123'\n    return secret\n"
    chunks = extract_chunks_from_diff(diff, {"sample.py": source})
    assert len(chunks) >= 1
    assert chunks[0].file == "sample.py"

def test_extract_chunks_skips_missing_source():
    diff = "+++ b/missing.py\n@@ -1,1 +1,2 @@\n+x = 1\n"
    assert extract_chunks_from_diff(diff, {}) == []

def test_extract_chunks_skips_non_python():
    diff = "+++ b/style.css\n@@ -1,1 +1,2 @@\n+body { color: red; }\n"
    assert isinstance(extract_chunks_from_diff(diff, {"style.css": "body {}"}), list)


def test_token_reduction_demonstration():
    FUNC_LINES = 15
    PAD1, PAD2, PAD3 = 100, 200, 170
    assert PAD1 + FUNC_LINES + PAD2 + FUNC_LINES + PAD3 == 500

    func_a = "def func_a():\n" + "    x = 1\n" * 13 + "    return x\n"
    func_b = "def func_b():\n" + "    y = 2\n" * 13 + "    return y\n"
    source = "# padding\n" * PAD1 + func_a + "# padding\n" * PAD2 + func_b + "# padding\n" * PAD3
    assert source.count("\n") == 500

    a_line = PAD1 + 2
    b_line = PAD1 + FUNC_LINES + PAD2 + 2

    diff = (
        "+++ b/big_file.py\n"
        f"@@ -{a_line},1 +{a_line},2 @@\n"
        " context\n"
        "+    x = 99\n"
        f"@@ -{b_line},1 +{b_line},2 @@\n"
        " context\n"
        "+    y = 99\n"
    )

    chunks = extract_chunks_from_diff(diff, {"big_file.py": source})
    assert len(chunks) >= 1
    total = sum(c.end_line - c.start_line + 1 for c in chunks)
    assert (500 - total) / 500 > 0.80
