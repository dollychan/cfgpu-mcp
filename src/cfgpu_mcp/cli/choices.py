"""``click.Choice`` values read from the tool schema, so the CLI cannot drift from it.

Hand-copied lists had already fallen behind (image ``--resolution`` lacked 1K/1.5K,
image ``--aspect-ratio`` lacked 3:2/2:3/21:9/9:21/3:1/1:3, ``models list --task-type`` lacked
audio/understand), and click compares case-sensitively by default, so ``-r 480P``
failed here while the MCP tools accept it.
"""
from __future__ import annotations

import click
from pydantic import BaseModel


class _CanonicalChoice(click.Choice):
    """Case-insensitive, but ``--help`` lists the canonical spellings (``2K``, not ``2k``)."""

    def get_metavar(self, param: click.Parameter, ctx: click.Context) -> str:
        return f"[{'|'.join(map(str, self.choices))}]"


def schema_choice(model: type[BaseModel], field: str) -> click.Choice:
    prop = model.model_json_schema()["properties"][field]
    branches = prop.get("anyOf", [prop])
    values = [v for branch in branches for v in branch.get("enum", [])]
    if not values:
        raise ValueError(f"{model.__name__}.{field} has no enum in its schema")
    return _CanonicalChoice(values, case_sensitive=False)
