"""HTTP client and page parser for the Michaelis Dicionário Escolar.

The client sends one request at a time over one connection, no faster than
``min_interval``. It raises :class:`Blocked` on anything that looks like a block,
and it never tries to get past one.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional
from urllib.parse import quote, unquote, urlsplit

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter

from pymichaelis.models import (
    FOUND,
    NO_ENTRY,
    NO_TRANSCRIPTION,
    Dictionary,
    Entry,
    Link,
)
from pymichaelis.notation import UnknownSymbol, to_ipa

BASE_URL = "https://michaelis.uol.com.br"
USER_AGENT = "pymichaelis/0.1"
NOT_FOUND_TITLE = "Verbete não encontrado"

DICTIONARIES = {
    name: Dictionary(name, rep, target)
    for name, rep, target in (
        ("espanhol", 3, 2),
        ("alemao", 4, 3),
        ("italiano", 5, 4),
        ("frances", 6, 5),
    )
}

#: The most suggestions the site returns for one prefix.
SUGGESTION_CAP = 5

_LINK_TEXT = re.compile(r"^(?P<text>.*?)(?:<ei>(?P<homograph>\d+)</ei>)?$")
_LINK_PATH = re.compile(r"^/palavra/(?P<id>[^/]+)/")


class Blocked(RuntimeError):
    """The site refused the crawl, or served a page this parser does not know."""


def word_url(dictionary: Dictionary, word: str) -> str:
    return f"{BASE_URL}{dictionary.search_path}/{quote(word, safe='')}/"


def _text(tag: Optional[Tag]) -> Optional[str]:
    return tag.get_text() if tag else None


def _parse_link(anchor: Tag) -> Link:
    href = unquote(urlsplit(anchor["href"]).path)
    match = _LINK_TEXT.match(anchor.decode_contents().strip())
    homograph = match["homograph"]
    entry_id = _LINK_PATH.match(href)["id"]
    return Link(
        text=match["text"],
        homograph=int(homograph) if homograph else None,
        path=f"/palavra/{entry_id}/{quote(match['text'], safe='')}/",
    )


def parse_entry(html: str, word: str, dictionary: str, url: str) -> Entry:
    """Parse one page.

    Raises:
        Blocked: The page is neither an entry nor the "not found" page.
    """
    soup = BeautifulSoup(html, "html.parser")
    title = _text(soup.title) or ""
    verbete = soup.select_one("div.verbete")
    if verbete is None:
        if NOT_FOUND_TITLE in title:
            return Entry(word, dictionary, url, NO_ENTRY)
        raise Blocked(f"unknown page for {url}: title {title!r}, {len(html)} bytes")

    headwords = verbete.find_all("e1")
    if not headwords:
        raise Blocked(f"entry without a headword at {url}")
    sibling = headwords[0].find_next_sibling()
    homograph = sibling.get_text() if sibling and sibling.name == "ei" else None
    transcriptions = [tag.get_text() for tag in verbete.find_all("et")]
    raw = transcriptions[0] if transcriptions else None
    try:
        ipa = to_ipa(raw) if raw else None
    except UnknownSymbol:
        ipa = None
    return Entry(
        word=word,
        dictionary=dictionary,
        url=url,
        status=FOUND if raw else NO_TRANSCRIPTION,
        headword=", ".join(tag.get_text() for tag in headwords),
        homograph=int(homograph) if homograph else None,
        syllables=_text(verbete.find("es")),
        raw=raw,
        raw_more=tuple(transcriptions[1:]),
        ipa=ipa,
        pos=tuple(tag.get_text() for tag in verbete.find_all("cg")),
        related=tuple(_parse_link(a) for a in soup.select("#lista_verbetes_links a")),
    )


def parse_suggestions(body: str) -> tuple[str, ...]:
    """Parse a ``/suggest/`` reply.

    Raises:
        Blocked: The reply is not the JSON the site's own page expects.
    """
    try:
        suggestions = json.loads(body)["suggestions"]
    except (ValueError, KeyError, TypeError) as error:
        raise Blocked(f"unknown suggest reply: {body[:200]!r}") from error
    return tuple(item["value"] for item in suggestions or ())


class Client:
    """Rate-limited, single-connection client.

    Args:
        min_interval: Seconds between the starts of two requests.
        server_error_backoff: Pauses after successive 5xx replies or network
            errors. The request fails with :class:`Blocked` when they run out.
        refusal_pause: Pause after a first 403 or 429.
        refusal_window: A second 403 or 429 within this many requests stops the
            client.
    """

    def __init__(
        self,
        min_interval: float = 1.05,
        session: Optional[requests.Session] = None,
        server_error_backoff: tuple[float, ...] = (30, 60, 120, 240, 480, 900),
        refusal_pause: float = 120,
        refusal_window: int = 100,
    ) -> None:
        self.min_interval = min_interval
        self.server_error_backoff = server_error_backoff
        self.refusal_pause = refusal_pause
        self.requests_sent = 0
        self.refusal_window = refusal_window
        self._last_refusal: Optional[int] = None
        self._last_start = 0.0
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.session.mount("https://", HTTPAdapter(pool_connections=1, pool_maxsize=1))

    def _send(self, method: str, url: str, data: Optional[dict] = None) -> requests.Response:
        wait = self._last_start + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_start = time.monotonic()
        self.requests_sent += 1
        return self.session.request(
            method, url, data=data, timeout=30, allow_redirects=False
        )

    def _request(self, method: str, url: str, data: Optional[dict] = None) -> requests.Response:
        pauses = iter(self.server_error_backoff)
        while True:
            try:
                response = self._send(method, url, data)
            except requests.RequestException as error:
                problem = repr(error)
            else:
                status = response.status_code
                if status in (403, 429):
                    self._handle_refusal(status, url, response)
                    continue
                if status < 500:
                    return self._reject_redirect(response, url)
                problem = f"HTTP {status}"
            pause = next(pauses, None)
            if pause is None:
                raise Blocked(f"{problem} persisted at {url}")
            print(f"  {problem} at {url}; pausing {pause}s", flush=True)
            time.sleep(pause)

    def _handle_refusal(self, status: int, url: str, response: requests.Response) -> None:
        last = self._last_refusal
        if last is not None and self.requests_sent - last <= self.refusal_window:
            raise Blocked(f"repeated HTTP {status} at {url}")
        self._last_refusal = self.requests_sent
        retry_after = response.headers.get("Retry-After", "")
        pause = max(self.refusal_pause, int(retry_after) if retry_after.isdigit() else 0)
        print(f"  HTTP {status} at {url}; pausing {pause}s", flush=True)
        time.sleep(pause)

    @staticmethod
    def _reject_redirect(response: requests.Response, url: str) -> requests.Response:
        if response.is_redirect:
            raise Blocked(f"redirect from {url} to {response.headers.get('Location')}")
        return response

    def _fetch_entry(self, dictionary: Dictionary, word: str, url: str) -> Entry:
        response = self._request("GET", url)
        if response.status_code == 404:
            return Entry(word, dictionary.name, url, NO_ENTRY)
        if response.status_code != 200:
            raise Blocked(f"HTTP {response.status_code} at {url}")
        response.encoding = "utf-8"
        return parse_entry(response.text, word, dictionary.name, url)

    def lookup(self, dictionary: Dictionary, word: str) -> Entry:
        """Fetch ``word``.

        The site ignores accents, so ``cafe`` returns ``café``. For a missing word
        it can return a longer headword, so check ``Entry.matches_query``.
        """
        return self._fetch_entry(dictionary, word, word_url(dictionary, word))

    def follow(self, dictionary: Dictionary, link: Link) -> Entry:
        """Fetch a "Veja também" link. Only this reaches a second homograph."""
        return self._fetch_entry(dictionary, link.text, f"{BASE_URL}{link.path}")

    def suggest(self, dictionary: Dictionary, prefix: str) -> tuple[str, ...]:
        """Return up to :data:`SUGGESTION_CAP` headwords that start with ``prefix``."""
        url = f"{BASE_URL}/suggest/"
        data = {
            "palavra": prefix,
            "r": dictionary.rep,
            "f": 0,
            "t": dictionary.target,
            "_csrf": "",
        }
        response = self._request("POST", url, data)
        if response.status_code != 200:
            raise Blocked(f"HTTP {response.status_code} at {url} for {prefix!r}")
        response.encoding = "utf-8"
        return parse_suggestions(response.text)
