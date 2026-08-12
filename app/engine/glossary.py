"""Loads the chess lexicon and maps detected concept tags to their definitions."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


class Glossary:
    def __init__(self, terms: dict[str, dict]):
        self.terms = terms

    def lookup(self, term: str) -> dict | None:
        return self.terms.get(term)

    def define(self, term: str) -> str:
        t = self.terms.get(term)
        return t["definition"] if t else ""

    def definitions_for(self, tags: list[dict]) -> list[dict]:
        """Given detected concept tags, return unique {term, category, definition}."""
        seen = set()
        out = []
        for t in tags:
            name = t.get("term")
            if name in seen:
                continue
            seen.add(name)
            entry = self.terms.get(name)
            if entry:
                out.append({"term": name, "category": entry["category"],
                            "definition": entry["definition"]})
        return out

    def all_names(self) -> list[str]:
        return sorted(self.terms.keys())


@lru_cache(maxsize=4)
def load_glossary(path: str) -> Glossary:
    p = Path(path)
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        return Glossary(data.get("terms", {}))
    return Glossary({})
