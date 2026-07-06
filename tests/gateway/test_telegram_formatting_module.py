"""Characterization tests for the extracted Telegram formatting module.

Phase 1 Autonomie V2 requires moving pure Telegram formatting behavior out of
``gateway/platforms/telegram.py`` without changing user-visible output.
"""

from gateway.platforms.telegram_formatting import (
    escape_mdv2,
    format_telegram_markdown,
    strip_mdv2,
    wrap_markdown_tables,
)


def test_format_telegram_markdown_preserves_core_markdownv2_behavior():
    text = "## Résultat\n\nThis is **bold** with [link](https://example.com/a_(b))."

    result = format_telegram_markdown(text)

    assert "*Résultat*" in result
    assert "*bold*" in result
    assert "[link](https://example.com/a_\\(b\\))" in result
    assert r"\." in result


def test_format_telegram_markdown_rewrites_tables_for_legacy_path():
    text = "| Item | Status |\n|---|---|\n| Build | OK |\n| Tests | Green |"

    result = format_telegram_markdown(text)

    assert "*Build*" in result
    assert "• Status: OK" in result
    assert "*Tests*" in result
    assert "• Status: Green" in result
    assert "\\|" not in result


def test_formatting_helpers_keep_existing_names_behavior():
    assert escape_mdv2("Hello (world)!") == "Hello \\(world\\)\\!"
    assert strip_mdv2("Hello \\(world\\)\\!") == "Hello (world)!"
    assert "**Build**" in wrap_markdown_tables(
        "| Item | Status |\n|---|---|\n| Build | OK |"
    )
