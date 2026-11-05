"""Convert the Michaelis phonetic notation to IPA.

The publisher documents the notation at
https://michaelis.uol.com.br/escolar-espanhol/transcricao-fonetica-do-portugues/
and :data:`SYMBOLS` follows that key. The stress mark needs more than a symbol
swap: Michaelis writes it before the stressed vowel, IPA before the stressed
syllable.
"""
from __future__ import annotations

import unicodedata

STRESS = "′"
IPA_STRESS = "ˈ"

#: Michaelis symbol -> IPA. Symbols missing here are the same in both.
SYMBOLS = {
    "r̄": "ʁ",  # "r forte e aspirado", as in rato [r̄′atu]
    "r": "ɾ",  # "r brando", as in arma [′arma]
    "ә": "ə",  # Cyrillic schwa on entries; the key prints U+0259
    "ʌ": "ɐ",  # cama [k′ʌma]: ⟨a⟩ before a nasal consonant
    "λ": "ʎ",  # "para lh"
    "ñ": "ɲ",  # "para nh"
    "g": "ɡ",
}

VOWELS = frozenset("aɛeiɔouəɐãẽĩõũ̃")
CONSONANTS = frozenset("bdfɡʒklʎmnɲpɾʁsʃtvz")
GLIDES = frozenset("jw")
SEPARATORS = frozenset(" -")
INVENTORY = VOWELS | CONSONANTS | GLIDES | SEPARATORS | {IPA_STRESS}

#: Two-consonant onsets a Portuguese syllable allows.
CLUSTERS = frozenset(
    first + second for first in "pbtdkɡfv" for second in "ɾl"
)


class UnknownSymbol(ValueError):
    """The transcription holds a symbol that the notation key does not define."""


def _is_onset(run: str) -> bool:
    if run[-1] in GLIDES and len(run) > 1:
        run = run[:-1]
    return len(run) == 1 or run in CLUSTERS


def _move_stress(text: str) -> str:
    """Move each stress mark from before the vowel to before the syllable."""
    out: list[str] = []
    for char in text:
        if char != IPA_STRESS:
            out.append(char)
            continue
        start = len(out)
        while start and out[start - 1] in CONSONANTS | GLIDES:
            start -= 1
        run = "".join(out[start:])
        word_initial = start == 0 or out[start - 1] in SEPARATORS
        if run and not word_initial:
            while not _is_onset(run):
                start += 1
                run = run[1:]
        out.insert(start, IPA_STRESS)
    return "".join(out)


def to_ipa(raw: str) -> str:
    """Return the IPA form of a raw ``<et>`` string such as ``[pes′oә]``.

    Raises:
        UnknownSymbol: ``raw`` holds a symbol outside the notation key.
    """
    text = unicodedata.normalize("NFC", raw.strip()).strip("[]").strip()
    for symbol, ipa in SYMBOLS.items():
        text = text.replace(symbol, ipa)
    text = text.replace(STRESS, IPA_STRESS)
    unknown = sorted(set(text) - INVENTORY)
    if unknown or not text:
        raise UnknownSymbol(f"{raw!r}: {unknown}")
    return _move_stress(text)
