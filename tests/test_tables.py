"""Tests for markdown table conversion to MAX-compatible format."""

import pytest

import adapter


class TestConvertMarkdownTables:
    """Tests for _convert_markdown_tables."""

    def test_simple_table(self):
        text = """Some text before.

| Name | Value | Status |
|------|-------|--------|
| foo  | 42    | ok     |
| bar  | 99    | fail   |

After text."""

        result = adapter.MaxAdapter._convert_markdown_tables(text)

        # Should render as aligned text with pipe separators
        assert "Name" in result
        assert "Value" in result
        assert "Status" in result
        assert "foo" in result
        assert "bar" in result
        # Original text preserved
        assert "Some text before" in result
        assert "After text" in result
        # No code fences
        assert "```" not in result
        # Original pipe table syntax should be gone
        assert "|------|" not in result

    def test_no_table_unchanged(self):
        text = "Just plain text without any tables."
        result = adapter.MaxAdapter._convert_markdown_tables(text)
        assert result == text

    def test_multiple_tables(self):
        text = """First table:

| A | B |
|---|---|
| 1 | 2 |

Middle text.

| X | Y |
|---|---|
| 3 | 4 |"""

        result = adapter.MaxAdapter._convert_markdown_tables(text)

        # Both tables converted — each has a top and bottom separator line
        assert result.count("-------") >= 4  # 2 tables × (top + bottom)
        assert "1" in result
        assert "2" in result
        assert "3" in result
        assert "4" in result
        assert "Middle text" in result
        assert "First table" in result
        # No code fences
        assert "```" not in result

    def test_wide_columns_capped(self):
        text = """| VeryLongColumnNameThatExceeds | Short |
|-------------------------------|-------|
| very_long_value_here_too      | ok    |"""

        result = adapter.MaxAdapter._convert_markdown_tables(text)

        # Columns capped at 25 chars
        assert "VeryLongColumnNameThatE" in result  # truncated
        assert "very_long_value_here_to" in result  # truncated
        assert "Short" in result
        assert "ok" in result

    def test_single_column_table(self):
        text = """| Item |
|------|
| one  |
| two  |"""

        result = adapter.MaxAdapter._convert_markdown_tables(text)

        # Separator present (not code fence)
        assert "-------" in result
        assert "Item" in result
        assert "one" in result
        assert "two" in result

    def test_markdown_formatting_in_cells(self):
        text = """| Feature | Status |
|---------|--------|
| **Bold** | ✅ |
| *Italic* | ❌ |"""

        result = adapter.MaxAdapter._convert_markdown_tables(text)

        # Markdown formatting in cells is preserved
        assert "**Bold**" in result
        assert "*Italic*" in result
        assert "✅" in result

    def test_empty_cells(self):
        text = """| A | B | C |
|---|---|---|---|
| 1 |   | 3 |
|   | 2 |   |"""

        result = adapter.MaxAdapter._convert_markdown_tables(text)

        # Separator present (not code fence)
        assert "-------" in result
        assert "1" in result
        assert "3" in result
        assert "2" in result
