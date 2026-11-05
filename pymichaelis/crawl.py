"""Resumable crawl of the Portuguese headwords of the Michaelis Dicionário Escolar.

The site has no headword index, but its autocomplete lists up to five headwords
for a prefix. A reply of fewer than five is complete for that prefix, and a full
reply means the crawl must go one letter deeper. Walking that tree lists the
headwords, and each headword then gets one page fetch.

Three levels limit which letters the walk tries after a full reply. ``guided``
tries the letters that continue a word of the held word list. ``trigram`` tries
the letters that form a trigram seen in that list. ``full`` tries the whole
alphabet. Each level reuses all the replies of the level before it.

Two append-only JSONL logs hold all the state, so a restart loses nothing:
``entries.jsonl`` has one row per page fetch, misses included, and
``prefixes.jsonl`` has one row per autocomplete reply.

The crawl stops on the first sign of a block and leaves the reason in a
``STOPPED`` file. It refuses to start while that file exists.

CLI::

    python -m pymichaelis.crawl --data crawl words --dictionary espanhol --file pilot.txt
    python -m pymichaelis.crawl --data crawl walk --dictionary espanhol --level guided --held words.txt
    python -m pymichaelis.crawl --data crawl status
"""
from __future__ import annotations

import argparse
import fcntl
import json
import signal
import string
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from pymichaelis.client import BASE_URL, DICTIONARIES, SUGGESTION_CAP, Blocked, Client
from pymichaelis.models import Dictionary, Entry, Link, fold

ALPHABET = string.ascii_lowercase + "-' "
LEVELS = ("guided", "trigram", "full")
EXIT_BLOCKED = 3


def first_form(headword: str) -> str:
    """Return ``a`` for the printed headword ``a, A``."""
    return headword.split(", ")[0]


def _read_jsonl(path: Path) -> list[dict]:
    """Read a log. A row cut short by a crash is dropped, and it is fetched again."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            print(f"dropped a torn row in {path.name}: {line[:80]!r}", flush=True)
    return rows


class Store:
    """The two logs and the indexes derived from them."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.entries_path = directory / "entries.jsonl"
        self.prefixes_path = directory / "prefixes.jsonl"
        self.stopped_path = directory / "STOPPED"
        self.rows = _read_jsonl(self.entries_path)
        self.queried: set[tuple[str, str]] = set()
        self.printed: set[tuple[str, str]] = set()
        self.homographs: set[tuple[str, str, Optional[int]]] = set()
        self.urls: set[str] = set()
        for row in self.rows:
            self._index(row)
        self.replies: dict[tuple[str, str], tuple[str, ...]] = {
            (row["dictionary"], row["prefix"]): tuple(row["suggestions"])
            for row in _read_jsonl(self.prefixes_path)
        }
        self._entries = None
        self._prefixes = None

    @staticmethod
    def _open(path: Path):
        """Open a log for append and end any torn last row with a newline."""
        if path.exists() and path.stat().st_size:
            with path.open("rb") as log:
                log.seek(-1, 2)
                torn = log.read(1) != b"\n"
            if torn:
                with path.open("ab") as log:
                    log.write(b"\n")
        return path.open("a", encoding="utf-8")

    def _index(self, row: dict) -> None:
        dictionary = row["dictionary"]
        self.queried.add((dictionary, row["word"]))
        self.urls.add(row["url"])
        if row["headword"]:
            for form in row["headword"].split(", "):
                self.printed.add((dictionary, form))
                self.homographs.add((dictionary, form, row["homograph"]))

    def add_entry(self, entry: Entry) -> None:
        row = entry.to_dict()
        row["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._entries = self._entries or self._open(self.entries_path)
        self._entries.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._entries.flush()
        self.rows.append(row)
        self._index(row)

    def add_reply(self, dictionary: str, prefix: str, suggestions: tuple[str, ...]) -> None:
        row = {"dictionary": dictionary, "prefix": prefix, "suggestions": list(suggestions)}
        self._prefixes = self._prefixes or self._open(self.prefixes_path)
        self._prefixes.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._prefixes.flush()
        self.replies[dictionary, prefix] = suggestions

    def has_word(self, dictionary: str, word: str) -> bool:
        return (dictionary, word) in self.queried or (dictionary, word) in self.printed

    def has_link(self, dictionary: str, link: Link) -> bool:
        if f"{BASE_URL}{link.path}" in self.urls:
            return True
        if link.homograph is None:
            return self.has_word(dictionary, link.text)
        return (dictionary, link.text, link.homograph) in self.homographs

    def status_counts(self) -> dict[str, Counter]:
        counts: dict[str, Counter] = {}
        for row in self.rows:
            counts.setdefault(row["dictionary"], Counter())[row["status"]] += 1
        for dictionary, _ in self.replies:
            counts.setdefault(dictionary, Counter())["prefixes"] += 1
        return counts


@dataclass
class Letters:
    """Decides which letters a walk tries after a full reply."""

    level: str
    prefixes: frozenset[str] = frozenset()
    trigrams: frozenset[str] = frozenset()

    @classmethod
    def from_words(cls, level: str, words: Iterable[str]) -> "Letters":
        prefixes: set[str] = set()
        trigrams: set[str] = set()
        for word in map(fold, words):
            prefixes.update(word[:end] for end in range(1, len(word) + 1))
            padded = f"^^{word}"
            trigrams.update(padded[i : i + 3] for i in range(len(padded) - 2))
        return cls(level, frozenset(prefixes), frozenset(trigrams))

    def allows(self, prefix: str, letter: str) -> bool:
        if self.level == "guided":
            return prefix + letter in self.prefixes
        if self.level == "trigram":
            return f"^^{prefix}"[-2:] + letter in self.trigrams
        return True

    def children(self, prefix: str) -> list[str]:
        return [prefix + letter for letter in ALPHABET if self.allows(prefix, letter)]


@dataclass
class Crawl:
    client: Client
    store: Store
    dictionary: Dictionary
    report_every: int = 100
    stop_requested: bool = False
    _started: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.name = self.dictionary.name
        self._others = [name for name in DICTIONARIES if name != self.name]
        self._primary = next(iter(DICTIONARIES))

    def _report(self, pending_words: int = 0, pending_prefixes: int = 0) -> None:
        sent = self.client.requests_sent
        if sent % self.report_every:
            return
        counts = self.store.status_counts().get(self.name, Counter())
        rate = sent / max(time.monotonic() - self._started, 1e-9)
        print(
            f"{datetime.now().isoformat(timespec='seconds')} {self.name} sent={sent} "
            f"rate={rate:.2f}/s {dict(counts)} pending_words={pending_words} "
            f"pending_prefixes={pending_prefixes}",
            flush=True,
        )

    def _known_elsewhere(self, word: str, homograph: Optional[int] = None) -> bool:
        """True when another dictionary already holds ``word``.

        The primary dictionary fetches everything. The others fetch only what
        it misses.
        """
        if self.name == self._primary:
            return False
        if homograph is None:
            return any((other, word) in self.store.printed for other in self._others)
        return any((other, word, homograph) in self.store.homographs for other in self._others)

    def run_words(self, words: Iterable[str]) -> int:
        """Look up each word that has no row yet. Return how many it fetched."""
        fetched = 0
        for word in dict.fromkeys(words):
            if self.stop_requested:
                break
            if (self.name, word) in self.store.queried:
                continue
            self.store.add_entry(self.client.lookup(self.dictionary, word))
            fetched += 1
            self._report()
        return fetched

    def _pending(self, letters: Letters) -> tuple[deque, deque]:
        """Rebuild both queues from the logs."""
        words: deque = deque()
        prefixes: deque[str] = deque()
        for (dictionary, prefix), suggestions in self.store.replies.items():
            if dictionary == self.name:
                words.extend(suggestions)
                if len(suggestions) >= SUGGESTION_CAP:
                    words.append(prefix)
                    prefixes.extend(letters.children(prefix))
        for row in self.store.rows:
            if row["dictionary"] == self.name:
                words.extend(Link(**link) for link in row["related"])
        prefixes.extend(string.ascii_lowercase)
        return words, prefixes

    def _is_done(self, task: "str | Link") -> bool:
        if isinstance(task, Link):
            return self.store.has_link(self.name, task) or self._known_elsewhere(
                task.text, task.homograph
            )
        return self.store.has_word(self.name, task) or self._known_elsewhere(task)

    def walk(self, letters: Letters, max_requests: Optional[int] = None) -> None:
        """Walk the prefix tree and fetch every headword it finds."""
        words, prefixes = self._pending(letters)
        start = self.client.requests_sent
        while not self.stop_requested:
            if max_requests is not None and self.client.requests_sent - start >= max_requests:
                return
            if words:
                task = words.popleft()
                if self._is_done(task):
                    continue
                if isinstance(task, Link):
                    entry = self.client.follow(self.dictionary, task)
                else:
                    entry = self.client.lookup(self.dictionary, task)
                self.store.add_entry(entry)
                words.extend(entry.related)
            elif prefixes:
                prefix = prefixes.popleft()
                if (self.name, prefix) in self.store.replies:
                    continue
                suggestions = self.client.suggest(self.dictionary, prefix)
                self.store.add_reply(self.name, prefix, suggestions)
                words.extend(suggestions)
                if len(suggestions) >= SUGGESTION_CAP:
                    words.append(prefix)
                    prefixes.extend(letters.children(prefix))
            else:
                print(f"{self.name}: level {letters.level} is complete", flush=True)
                return
            self._report(len(words), len(prefixes))


def _print_status(store: Store) -> None:
    for name, counts in store.status_counts().items():
        print(name, dict(counts))
    if store.stopped_path.exists():
        print("STOPPED:", store.stopped_path.read_text(encoding="utf-8").strip())


def _guarded(store: Store, action: Callable[[], None]) -> int:
    """Run ``action``. On a block, record the reason and return the block status."""
    try:
        action()
    except Blocked as error:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.stopped_path.write_text(f"{stamp} {error}\n", encoding="utf-8")
        print(f"STOPPED: {error}", flush=True)
        return EXIT_BLOCKED
    return 0


def _read_words(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, required=True, help="directory of the two logs")
    commands = parser.add_subparsers(dest="command", required=True)
    words = commands.add_parser("words", help="look up the words of a file")
    walk = commands.add_parser("walk", help="walk the prefix tree and fetch the headwords")
    commands.add_parser("status", help="print the counts of the logs")
    for command in (words, walk):
        command.add_argument("--dictionary", choices=DICTIONARIES, required=True)
        command.add_argument("--interval", type=float, default=1.05)
    words.add_argument("--file", type=Path, required=True)
    walk.add_argument("--level", choices=LEVELS, required=True)
    walk.add_argument("--held", type=Path, help="word list for the guided and trigram levels")
    walk.add_argument("--max-requests", type=int)
    args = parser.parse_args(argv)

    if args.command == "status":
        _print_status(Store(args.data))
        return 0

    args.data.mkdir(parents=True, exist_ok=True)
    lock = (args.data / "crawl.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error(f"another crawl holds {args.data}; the rate limit allows only one")
    store = Store(args.data)
    if store.stopped_path.exists():
        parser.error(f"a block stopped the crawl: {store.stopped_path.read_text().strip()}")

    crawl = Crawl(Client(min_interval=args.interval), store, DICTIONARIES[args.dictionary])

    def request_stop(*_) -> None:
        crawl.stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    if args.command == "words":
        return _guarded(store, lambda: crawl.run_words(_read_words(args.file)))
    held = _read_words(args.held) if args.held else ()
    if args.level != "full" and not held:
        parser.error(f"level {args.level} needs --held")
    letters = Letters.from_words(args.level, held)
    return _guarded(store, lambda: crawl.walk(letters, args.max_requests))


if __name__ == "__main__":
    sys.exit(main())
