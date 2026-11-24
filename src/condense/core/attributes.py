"""Attributes: facts about each transcript to filter and compare by (MAXQDA's document variables).

Some come from the study's folders and need no typing: Day, Group, Type (session / interview / other) and Date (the
first date in the folder names, e.g. "Day 1 2026-08-10"). The project defines its own on top (Activity, Condition,
Facilitator …), each optionally with a list of values to choose from:

    condense-project.json  "attributes": [{"name": "Activity", "choices": ["Clay", "Paper boats"]}, …]
    <name>.transcript.json "attributes": {"Activity": "Clay", …}

A transcript's own values live in its file, so they move with it and are covered by its backups and edit log.
"""
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

AUTO = ("Day", "Group", "Type", "Date")
TYPE_NAMES = {"session": "Session", "interview": "Interview", "other": "Other"}
_DATE_RES = (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), re.compile(r"(?<!\d)(\d{2})\.(\d{2})\.(\d{4})(?!\d)"))


def date_in(parts: Iterable[str]) -> str:
    """The first date (2026-08-10 or 10.08.2026) in the given folder or file names, as YYYY-MM-DD; '' if none."""
    for part in parts:
        m = _DATE_RES[0].search(part)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        m = _DATE_RES[1].search(part)
        if m:
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return ""


def auto_values(project: Path, path: Path, levels: Sequence, role: str) -> Dict[str, str]:
    """Day / Group / Type / Date of a transcript. levels: [(level name, folder name), …] as project.level_path gives."""
    out = {lv: name for lv, name in levels}
    out["Type"] = TYPE_NAMES.get(role, "Other")
    try:
        parts = Path(path).resolve().relative_to(Path(project).resolve()).parts
    except ValueError:
        parts = Path(path).parts
    out["Date"] = date_in(parts)
    return out


def definitions(project_data: Dict) -> List[Dict]:
    """The project's own attributes: [{"name", "choices"}] (choices may be empty: any value)."""
    out = []
    for d in project_data.get("attributes") or []:
        name = " ".join(str(d.get("name", "")).split())
        if name and name not in AUTO and all(o["name"] != name for o in out):
            out.append({"name": name, "choices": [str(c) for c in d.get("choices") or [] if str(c).strip()]})
    return out


def clean_value(value) -> str:
    return " ".join(str(value or "").split())


def values_of(doc) -> Dict[str, str]:
    return {k: v for k, v in (doc.data.get("attributes") or {}).items() if v not in (None, "")}


def set_value(doc, name: str, value: str) -> bool:
    """Set (or with an empty value: remove) one attribute of a transcript. Returns True if it changed."""
    attrs = dict(doc.data.get("attributes") or {})
    value = clean_value(value)
    if (attrs.get(name) or "") == value:
        return False
    if value:
        attrs[name] = value
    else:
        attrs.pop(name, None)
    doc.data["attributes"] = attrs
    doc.log_edit("set_attribute", attribute=name, value=value)
    return True


def rename_key(doc, old: str, new: Optional[str]) -> bool:
    """An attribute was renamed (new) or deleted (None) in the project: follow in this transcript."""
    attrs = dict(doc.data.get("attributes") or {})
    if old not in attrs:
        return False
    value = attrs.pop(old)
    if new:
        attrs[new] = value
    doc.data["attributes"] = attrs
    doc.log_edit("rename_attribute" if new else "delete_attribute", attribute=old, new=new)
    return True


def used_values(rows: Iterable[Dict], name: str) -> List[str]:
    """Values an attribute already has somewhere (to offer them again)."""
    seen: List[str] = []
    for r in rows:
        v = (r.get("attributes") or {}).get(name)
        if v and v not in seen:
            seen.append(v)
    return sorted(seen, key=str.lower)
