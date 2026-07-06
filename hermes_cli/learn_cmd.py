"""Shared ``/learn`` command logic for CLI, TUI, and gateway.

Turns URLs, directories, pasted notes, or a free-text brief into a reusable
skill authored via ``skill_manage``. The handler returns an ``agent_seed`` that
the calling surface runs as the next agent turn (same pattern as ``/blueprint``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class LearnCommandResult:
    """Outcome of a ``/learn`` invocation."""

    text: str
    agent_seed: Optional[str] = None


def build_learn_agent_seed(user_request: str) -> str:
    """Build the one-shot agent instruction for skill authoring."""
    return f"""You are executing the Hermes /learn command.

Goal: Create a high-quality, reusable Hermes skill from the user's brief.

User request:
{user_request}

Requirements:
1. Pick a short kebab-case skill name and a description (max 60 characters).
2. Use web_search and web_extract on official documentation when accuracy matters.
3. Author SKILL.md with: When to Use, Procedure (numbered steps + commands), Pitfalls, Verification.
4. For broad topics, add focused files under references/ (and templates/ if useful).
5. Use skill_manage(action='create', ...) then skill_manage(action='write_file', ...) for supporting files.
6. In SKILL.md frontmatter set `metadata.hermes.source: learn` (under metadata.hermes).
7. Do not invent APIs or limits — cite official URLs in references when unsure.
8. When finished, confirm the skill path, slash command `/<name>`, run skills_list to verify, and tell the user they can run `/myskills` to see it in their personal list.

Proceed without asking for confirmation unless a name collision requires choosing a new slug."""


def handle_learn_command(args: str, *, surface: str = "cli") -> LearnCommandResult:
    """Handle ``/learn <brief>`` for CLI or gateway surfaces."""
    user_request = (args or "").strip()
    if not user_request:
        return LearnCommandResult(
            "Usage: `/learn <what to turn into a skill>`\n"
            "Example: `/learn Cloudflare production site architecture (Pages, Workers, D1, R2, Images, DNS, deploy, verify)`",
            agent_seed=None,
        )

    preview = user_request if len(user_request) <= 120 else user_request[:117] + "..."
    ack = (
        f"**Learn** — creating skill from your brief.\n\n"
        f"Topic: {preview}\n\n"
        f"I'll gather sources, author `SKILL.md`, and register the slash command."
    )
    if surface == "cli":
        ack = (
            f"[learn] Creating skill from: {preview}\n"
            f"The agent will use skill_manage + web tools on the next turn."
        )

    return LearnCommandResult(text=ack, agent_seed=build_learn_agent_seed(user_request))