"""The single chokepoint for rendering secret-bearing data as output.

No function here returns a raw secret value: diffs and summaries emit only key
names. (A salted value-digest renderer lived here previously but had no consumer;
re-add one alongside the command that needs it.)
"""
from __future__ import annotations


def key_diff(old: dict[str, str], new: dict[str, str]) -> dict[str, list[str]]:
    """Compare two env maps and return key-only changes.

    'changed' is determined by direct value comparison (both values are already
    in memory); values themselves are never returned.
    """
    old_keys, new_keys = set(old), set(new)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    changed = sorted(k for k in old_keys & new_keys if old[k] != new[k])
    return {"added": added, "removed": removed, "changed": changed}


def format_diff(diff: dict[str, list[str]]) -> str:
    parts = []
    for label in ("added", "removed", "changed"):
        for key in diff[label]:
            parts.append(f"{label}: {key}")
    return "\n".join(parts) if parts else "no changes"
