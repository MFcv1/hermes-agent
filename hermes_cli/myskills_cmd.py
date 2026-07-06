"""Shared ``/myskills`` command — list personal / learned skills on disk."""

from __future__ import annotations


def _truncate(text: str, max_len: int = 72) -> str:
    text = " ".join((text or "").split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def format_myskills_message(*, surface: str = "gateway") -> str:
    """Build the user-facing list of local extension skills."""
    from tools.skill_usage import myskills_report

    rows = myskills_report()
    if not rows:
        hint = (
            "Aucun skill perso pour l'instant.\n"
            "Crée-en un avec `/learn <sujet>` ou demande à l'agent d'en author un."
        )
        return hint if surface == "gateway" else f"[myskills] {hint}"

    title = f"**Mes skills** ({len(rows)})" if surface == "gateway" else f"[myskills] {len(rows)} skill(s)"
    lines = [title, ""]
    for r in rows:
        pin = " 📌" if r.get("pinned") else ""
        origin = r.get("origin_label") or "local"
        desc = _truncate(r.get("description") or "")
        rel = r.get("relative_dir") or ""
        lines.append(f"• `/{r['name']}` — {desc}{pin}")
        lines.append(f"    _{origin}_ · `{rel}`")
    lines.append("")
    lines.append("Charger: `/<nom>` · Raccourci déploiement: `/deploy` · Groupes: `/bundles`")
    if surface != "gateway":
        return "\n".join(lines)
    return "\n".join(lines)


def handle_myskills_command(*, surface: str = "cli") -> str:
    return format_myskills_message(surface=surface)