from __future__ import annotations

import dataclasses
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional

FOUND = "found"
NO_ENTRY = "no_entry"
NO_TRANSCRIPTION = "no_transcription"


def fold(word: str) -> str:
    """Lower-case ``word`` and strip its accents, as the site's own search does."""
    decomposed = unicodedata.normalize("NFD", word.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


@dataclass(frozen=True)
class Dictionary:
    """One Portuguese-to-foreign half of the Michaelis Dicionário Escolar."""

    name: str
    rep: int
    target: int

    @property
    def search_path(self) -> str:
        return f"/moderno-{self.name}/busca/portugues-{self.name}"


@dataclass(frozen=True)
class Link:
    """A "Veja também" link. ``text`` is the headword as the link prints it."""

    text: str
    homograph: Optional[int]
    path: str


@dataclass(frozen=True)
class Entry:
    """The outcome of one page fetch.

    ``raw`` is the first ``<et>`` text exactly as served. ``raw_more`` holds any
    further ``<et>`` texts of the same page.
    """

    word: str
    dictionary: str
    url: str
    status: str
    headword: Optional[str] = None
    homograph: Optional[int] = None
    syllables: Optional[str] = None
    raw: Optional[str] = None
    raw_more: tuple[str, ...] = ()
    ipa: Optional[str] = None
    pos: tuple[str, ...] = ()
    related: tuple[Link, ...] = ()

    @property
    def matches_query(self) -> bool:
        """False when the site fell back to a longer headword for a missing word.

        A lookup of ``flag`` returns ``flagelação``, and this entry says nothing
        about the pronunciation of ``flag``.
        """
        forms = (self.headword or "").split(", ")
        return any(fold(form) == fold(self.word) for form in forms)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)
