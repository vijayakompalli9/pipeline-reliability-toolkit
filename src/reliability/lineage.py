"""Minimal lineage graph used to compute the blast radius of a failing dataset."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reliability.contracts import load_yaml
from reliability.exceptions import ContractError


@dataclass(frozen=True)
class Impact:
    dataset: str
    depth: int
    owner: str
    criticality: str
    description: str = ""


class Lineage:
    def __init__(self, nodes: dict[str, dict[str, Any]]) -> None:
        self.nodes = nodes

    @classmethod
    def load(cls, path: str | Path) -> Lineage:
        data = load_yaml(path).get("datasets")
        if not isinstance(data, dict):
            raise ContractError(f"{path}: expected a 'datasets' mapping")
        return cls(data)

    def downstream(self, dataset: str) -> list[Impact]:
        """Breadth-first walk of everything that reads (directly or not) from ``dataset``."""
        seen: set[str] = {dataset}
        queue = deque((child, 1) for child in self.nodes.get(dataset, {}).get("downstream", []))
        impacts: list[Impact] = []
        while queue:
            name, depth = queue.popleft()
            if name in seen:
                continue
            seen.add(name)
            meta = self.nodes.get(name, {})
            impacts.append(Impact(name, depth, meta.get("owner", "unknown"), meta.get("criticality", "medium"),
                                  meta.get("description", "")))
            queue.extend((child, depth + 1) for child in meta.get("downstream", []))
        return impacts
