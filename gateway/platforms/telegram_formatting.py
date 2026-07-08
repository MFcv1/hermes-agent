"""Pure Telegram formatting helpers.

Extracted for Autonomie V2 Phase 1 so ``gateway/platforms/telegram.py`` stops
owning MarkdownV2/table rendering details. Keep this module side-effect free:
no bot objects, no config, no network, no persistence.
"""

from __future__ import annotations

import re

# Matches every character that MarkdownV2 requires to be backslash-escaped
# when it appears outside a code span or fenced code block.
_MDV2_ESCAPE_RE = re.compile(r'([_*\[\]()~`>#\+\-=|{}.!\\])')


def escape_mdv2(text: str) -> str:
    """Escape Telegram MarkdownV2 special characters with a preceding backslash."""
    return _MDV2_ESCAPE_RE.sub(r'\\\1', text)


def strip_mdv2(text: str) -> str:
    """Strip MarkdownV2 escape backslashes to produce clean plain text.

    Also removes MarkdownV2 formatting markers so the fallback doesn't show
    stray syntax characters from MarkdownV2 conversion.
    """
    cleaned = re.sub(r'\\([_*\[\]()~`>#\+\-=|{}.!\\])', r'\1', text)
    cleaned = re.sub(r'\*\*([^*]+)\*\*', r'\1', cleaned)
    cleaned = re.sub(r'\*([^*]+)\*', r'\1', cleaned)
    cleaned = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'\1', cleaned)
    cleaned = re.sub(r'~([^~]+)~', r'\1', cleaned)
    cleaned = re.sub(r'\|\|([^|]+)\|\|', r'\1', cleaned)
    return cleaned


# ---------------------------------------------------------------------------
# Markdown table → Telegram-friendly row groups
# ---------------------------------------------------------------------------
_TABLE_SEPARATOR_RE = re.compile(
    r'^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*){1,}\|?\s*$'
)


def is_table_row(line: str) -> bool:
    """Return True if *line* could plausibly be a table data row."""
    stripped = line.strip()
    return bool(stripped) and '|' in stripped


def split_markdown_table_row(line: str) -> list[str]:
    """Split a simple GFM table row into stripped cell values."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def render_table_block_for_telegram(table_block: list[str]) -> str:
    """Render a detected GFM table as Telegram-friendly row groups."""
    if len(table_block) < 3:
        return "\n".join(table_block)

    headers = split_markdown_table_row(table_block[0])
    if len(headers) < 2:
        return "\n".join(table_block)

    first_data_row = split_markdown_table_row(table_block[2]) if len(table_block) > 2 else []
    has_row_label_col = len(first_data_row) == len(headers) + 1

    rendered_groups: list[str] = []
    for index, row in enumerate(table_block[2:], start=1):
        cells = split_markdown_table_row(row)
        if has_row_label_col:
            heading = cells[0] if cells and cells[0] else f"Row {index}"
            data_cells = cells[1:]
        else:
            heading = next((cell for cell in cells if cell), f"Row {index}")
            data_cells = cells

        if len(data_cells) < len(headers):
            data_cells.extend([""] * (len(headers) - len(data_cells)))
        elif len(data_cells) > len(headers):
            data_cells = data_cells[: len(headers)]

        bullets: list[str] = []
        for header, value in zip(headers, data_cells):
            if not has_row_label_col and value == heading:
                continue
            bullets.append(f"• {header}: {value}")

        group_lines = [f"**{heading}**", *bullets]
        rendered_groups.append("\n".join(group_lines))

    return "\n\n".join(rendered_groups)


def wrap_markdown_tables(text: str) -> str:
    """Rewrite GFM-style pipe tables into Telegram-friendly bullet groups."""
    if '|' not in text or '-' not in text:
        return text

    lines = text.split('\n')
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        if stripped.startswith('```'):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue

        if (
            '|' in line
            and i + 1 < len(lines)
            and _TABLE_SEPARATOR_RE.match(lines[i + 1])
        ):
            table_block = [line, lines[i + 1]]
            j = i + 2
            while j < len(lines) and is_table_row(lines[j]):
                table_block.append(lines[j])
                j += 1
            out.append(render_table_block_for_telegram(table_block))
            i = j
            continue

        out.append(line)
        i += 1

    return '\n'.join(out)


def format_telegram_markdown(content: str) -> str:
    """Convert standard markdown to Telegram MarkdownV2 format."""
    if not content:
        return content

    placeholders: dict[str, str] = {}
    counter = [0]

    def _ph(value: str) -> str:
        key = f"\x00PH{counter[0]}\x00"
        counter[0] += 1
        placeholders[key] = value
        return key

    text = wrap_markdown_tables(content)

    def _protect_fenced(m: re.Match[str]) -> str:
        raw = m.group(0)
        open_end = raw.index('\n') + 1 if '\n' in raw[3:] else 3
        opening = raw[:open_end]
        body_and_close = raw[open_end:]
        body = body_and_close[:-3]
        body = body.replace('\\', '\\\\').replace('`', '\\`')
        return _ph(opening + body + '```')

    text = re.sub(r'(```(?:[^\n]*\n)?[\s\S]*?```)', _protect_fenced, text)
    text = re.sub(r'(`[^`]+`)', lambda m: _ph(m.group(0).replace('\\', '\\\\')), text)

    def _convert_link(m: re.Match[str]) -> str:
        display = escape_mdv2(m.group(1))
        url = m.group(2).replace('\\', '\\\\').replace(')', '\\)')
        return _ph(f'[{display}]({url})')

    text = re.sub(r'\[([^\]]+)\]\(([^()]*(?:\([^()]*\)[^()]*)*)\)', _convert_link, text)

    def _convert_header(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        inner = re.sub(r'\*\*(.+?)\*\*', r'\1', inner)
        return _ph(f'*{escape_mdv2(inner)}*')

    text = re.sub(r'^#{1,6}\s+(.+)$', _convert_header, text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', lambda m: _ph(f'*{escape_mdv2(m.group(1))}*'), text)
    text = re.sub(r'\*([^*\n]+)\*', lambda m: _ph(f'_{escape_mdv2(m.group(1))}_'), text)
    text = re.sub(r'~~(.+?)~~', lambda m: _ph(f'~{escape_mdv2(m.group(1))}~'), text)
    text = re.sub(r'\|\|(.+?)\|\|', lambda m: _ph(f'||{escape_mdv2(m.group(1))}||'), text)

    def _convert_blockquote(m: re.Match[str]) -> str:
        prefix = m.group(1)
        quote_content = m.group(2)
        if prefix.startswith('**') and quote_content.endswith('||'):
            return _ph(f'{prefix} {escape_mdv2(quote_content[:-2])}||')
        return _ph(f'{prefix} {escape_mdv2(quote_content)}')

    text = re.sub(r'^((?:\*\*)?>{1,3}) (.+)$', _convert_blockquote, text, flags=re.MULTILINE)
    text = escape_mdv2(text)

    for key in reversed(list(placeholders.keys())):
        text = text.replace(key, placeholders[key])

    code_split = re.split(r'(```[\s\S]*?```|`[^`]+`)', text)
    safe_parts = []
    for idx, seg in enumerate(code_split):
        if idx % 2 == 1:
            safe_parts.append(seg)
            continue

        def _esc_bare(m: re.Match[str], _seg: str = seg) -> str:
            s = m.start()
            ch = m.group(0)
            if s > 0 and _seg[s - 1] == '\\':
                return ch
            if ch == '(' and s > 0 and _seg[s - 1] == ']':
                return ch
            if ch == ')':
                before = _seg[:s]
                if '](http' in before or '](' in before:
                    depth = 0
                    for j in range(s - 1, max(s - 2000, -1), -1):
                        if _seg[j] == '(':
                            depth -= 1
                            if depth < 0:
                                if j > 0 and _seg[j - 1] == ']':
                                    return ch
                                break
                        elif _seg[j] == ')':
                            depth += 1
            return '\\' + ch

        safe_parts.append(re.sub(r'[(){}]', _esc_bare, seg))
    return ''.join(safe_parts)


# Backward-compatible aliases for old internal names imported by tests/plugins.
_escape_mdv2 = escape_mdv2
_strip_mdv2 = strip_mdv2
_is_table_row = is_table_row
_split_markdown_table_row = split_markdown_table_row
_render_table_block_for_telegram = render_table_block_for_telegram
_wrap_markdown_tables = wrap_markdown_tables
