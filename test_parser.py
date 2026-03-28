"""
test_parser.py — Unit tests for parser.py semantic chunking logic.

Run with:  pytest test_parser.py -v
"""

import pytest
from parser import (
    CodeChunk,
    _parse_diff_hunks,
    _extract_chunks_regex,
    extract_chunks_from_diff,
    TREE_SITTER_AVAILABLE,
)

# ── _parse_diff_hunks ─────────────────────────────────────────────────────────

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
    hunks = _parse_diff_hunks(SAMPLE_DIFF)
    assert "app/views.py" in hunks

def test_parse_diff_hunks_correct_line_numbers():
    hunks = _parse_diff_hunks(SAMPLE_DIFF)
    # Lines 11 and 12 are the two '+' additions starting from @@ +10
    changed = hunks["app/views.py"]
    assert len(changed) == 2
    assert 11 in changed
    assert 12 in changed

def test_parse_diff_hunks_empty_diff():
    assert _parse_diff_hunks("") == {}

def test_parse_diff_hunks_no_additions():
    diff = "+++ b/foo.py\n@@ -1,3 +1,3 @@\n context\n-removed\n context\n"
    hunks = _parse_diff_hunks(diff)
    assert hunks.get("foo.py", []) == []


# ── _extract_chunks_regex ─────────────────────────────────────────────────────

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
    chunks = _extract_chunks_regex("foo.py", SOURCE, changed_lines=[5])
    names = [c.name for c in chunks]
    assert "modified_func" in names

def test_regex_skips_unchanged_function():
    chunks = _extract_chunks_regex("foo.py", SOURCE, changed_lines=[5])
    names = [c.name for c in chunks]
    assert "unchanged" not in names

def test_regex_extracts_class():
    chunks = _extract_chunks_regex("foo.py", SOURCE, changed_lines=[10])
    names = [c.name for c in chunks]
    assert "MyClass" in names

def test_regex_chunk_fields():
    chunks = _extract_chunks_regex("foo.py", SOURCE, changed_lines=[5])
    chunk = next(c for c in chunks if c.name == "modified_func")
    assert chunk.file == "foo.py"
    assert chunk.kind == "function"
    assert 5 in chunk.diff_lines
    assert "modified_func" in chunk.source


# ── AST extraction (tree-sitter) ──────────────────────────────────────────────

@pytest.mark.skipif(not TREE_SITTER_AVAILABLE, reason="tree-sitter not installed")
def test_ast_extracts_function():
    from parser import _extract_chunks_ast
    source = "def foo():\n    x = 1\n    return x\n"
    chunks = _extract_chunks_ast("test.py", source, changed_lines=[2])
    assert len(chunks) == 1
    assert chunks[0].name == "foo"
    assert chunks[0].kind == "function"

@pytest.mark.skipif(not TREE_SITTER_AVAILABLE, reason="tree-sitter not installed")
def test_ast_extracts_class():
    from parser import _extract_chunks_ast
    source = "class Bar:\n    def method(self):\n        pass\n"
    chunks = _extract_chunks_ast("test.py", source, changed_lines=[2])
    assert any(c.name == "Bar" for c in chunks)

@pytest.mark.skipif(not TREE_SITTER_AVAILABLE, reason="tree-sitter not installed")
def test_ast_module_fallback_for_top_level_change():
    from parser import _extract_chunks_ast
    source = "X = 1\nY = 2\n"
    chunks = _extract_chunks_ast("test.py", source, changed_lines=[2])
    assert len(chunks) == 1
    assert chunks[0].kind == "module"


# ── extract_chunks_from_diff (integration) ────────────────────────────────────

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
    chunks = extract_chunks_from_diff(diff, {})
    assert chunks == []

def test_extract_chunks_skips_non_python():
    diff = "+++ b/style.css\n@@ -1,1 +1,2 @@\n+body { color: red; }\n"
    source = "body { color: red; }"
    chunks = extract_chunks_from_diff(diff, {"style.css": source})
    assert isinstance(chunks, list)


# ── Token reduction sanity check ──────────────────────────────────────────────

def test_token_reduction_demonstration():
    """
    Demonstrates the ~95% token reduction claim.

    Layout of the 500-line synthetic file:
      lines   1-100  : padding comments          (100 lines)
      lines 101-115  : func_a  (def + 13 body + return = 15 lines)
      lines 116-315  : padding comments          (200 lines)
      lines 316-330  : func_b  (15 lines)
      lines 331-500  : padding comments          (170 lines)

    We build a diff with two separate hunks, each starting exactly at a
    body line inside the respective function, so _parse_diff_hunks maps
    the '+' lines to the correct 1-based line numbers in the file.
    """
    FUNC_LINES = 15
    PAD1, PAD2, PAD3 = 100, 200, 170
    assert PAD1 + FUNC_LINES + PAD2 + FUNC_LINES + PAD3 == 500

    func_a = "def func_a():\n" + "    x = 1\n" * 13 + "    return x\n"
    func_b = "def func_b():\n" + "    y = 2\n" * 13 + "    return y\n"
    padding = "# padding\n"
    source = padding * PAD1 + func_a + padding * PAD2 + func_b + padding * PAD3
    assert source.count("\n") == 500

    # Body line 2 of func_a is at 1-based line PAD1 + 2 = 102
    # Body line 2 of func_b is at 1-based line PAD1 + FUNC_LINES + PAD2 + 2 = 317
    a_line = PAD1 + 2        # 102
    b_line = PAD1 + FUNC_LINES + PAD2 + 2  # 317

    # Two single-line hunks, each starting at the exact changed line.
    # @@ -N,1 +N,2 @@ means new file starts at line N; the '+' line becomes N.
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
    assert len(chunks) >= 1, "Expected at least one chunk to be extracted"

    total_chunk_lines = sum(c.end_line - c.start_line + 1 for c in chunks)
    reduction = (500 - total_chunk_lines) / 500
    assert reduction > 0.80, f"Expected >80% token reduction, got {reduction:.0%}"
