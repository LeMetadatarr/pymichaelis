import json
from pathlib import Path

import pytest
import requests

from pymichaelis import DICTIONARIES, Blocked, Client, Entry, Link
from pymichaelis.client import word_url
from pymichaelis.crawl import Crawl, Letters, Store, fold, main
from pymichaelis.dataset import unique_entries, write_tsv
from pymichaelis.models import FOUND, NO_ENTRY

SPANISH = DICTIONARIES["espanhol"]
GERMAN = DICTIONARIES["alemao"]


class FakeClient:
    """Serves a tiny dictionary of headwords with the site's cap of five."""

    def __init__(self, headwords, homographs=None):
        self.headwords = headwords
        self.homographs = homographs or {}
        self.requests_sent = 0
        self.lookups = []
        self.suggested = []

    def suggest(self, dictionary, prefix):
        self.requests_sent += 1
        self.suggested.append(prefix)
        return tuple(w for w in self.headwords if fold(w).startswith(prefix))[:5]

    def lookup(self, dictionary, word):
        self.requests_sent += 1
        self.lookups.append(word)
        url = word_url(dictionary, word)
        match = next((w for w in self.headwords if fold(w) == fold(word)), None)
        if match is None:
            return Entry(word, dictionary.name, url, NO_ENTRY)
        related = self.homographs.get(match, ())
        return Entry(word, dictionary.name, url, FOUND, headword=match, raw=f"[{match}]",
                     homograph=1 if related else None, related=related)

    def follow(self, dictionary, link):
        self.requests_sent += 1
        self.lookups.append(link.path)
        return Entry(link.text, dictionary.name, link.path, FOUND, headword=link.text,
                     homograph=link.homograph, raw=f"[{link.text}2]")


def walk(client, store, level="full", dictionary=SPANISH, words=()):
    Crawl(client, store, dictionary).walk(Letters.from_words(level, words))


def test_fold_removes_accents_and_cedilla():
    assert fold("Coração") == "coracao"


def test_walk_lists_every_headword_behind_the_cap(tmp_path):
    words = ["casa", "casaco", "casal", "casamento", "casar", "casca", "caso", "pé"]
    client = FakeClient(words)
    store = Store(tmp_path)
    walk(client, store)
    assert {row["headword"] for row in store.rows if row["status"] == FOUND} == set(words)


def test_a_word_hidden_behind_its_own_extensions_is_still_fetched(tmp_path):
    words = ["casaco", "casal", "casamento", "casar", "casarao", "casa"]
    client = FakeClient(words)
    store = Store(tmp_path)
    walk(client, store)
    assert "casa" in client.lookups
    assert ("espanhol", "casa") in store.printed


def test_a_hidden_word_survives_a_restart(tmp_path):
    words = ["casaco", "casal", "casamento", "casar", "casarao", "casa"]
    store = Store(tmp_path)
    store.add_reply("espanhol", "casa", tuple(words[:5]))
    for word in words[:5]:
        store.add_entry(FakeClient(words).lookup(SPANISH, word))
    resumed = FakeClient(words)
    walk(resumed, Store(tmp_path))
    assert "casa" in resumed.lookups


def test_a_second_homograph_is_reached_through_its_link(tmp_path):
    link = Link("colher", 2, "/palavra/Dl8zD/colher/")
    client = FakeClient(["colher"], {"colher": (link,)})
    store = Store(tmp_path)
    walk(client, store)
    assert sorted(
        (row["headword"], row["homograph"]) for row in store.rows if row["status"] == FOUND
    ) == [("colher", 1), ("colher", 2)]


def test_a_restart_sends_no_request_twice(tmp_path):
    words = ["casa", "casaco", "casal", "casamento", "casar", "casca", "caso", "pé"]
    first = FakeClient(words)
    store = Store(tmp_path)
    Crawl(first, store, SPANISH).walk(Letters.from_words("full", ()), max_requests=30)
    seen_lookups, seen_prefixes = list(first.lookups), list(first.suggested)
    found = {row["headword"] for row in store.rows if row["status"] == FOUND}
    assert seen_prefixes and 0 < len(found) < len(words)

    second = FakeClient(words)
    resumed = Store(tmp_path)
    walk(second, resumed)
    assert not set(second.lookups) & set(seen_lookups)
    assert not set(second.suggested) & set(seen_prefixes)
    assert {row["headword"] for row in resumed.rows if row["status"] == FOUND} == set(words)


def test_a_miss_is_never_fetched_twice(tmp_path):
    store = Store(tmp_path)
    client = FakeClient(["casa"])
    assert Crawl(client, store, SPANISH).run_words(["zzz", "zzz"]) == 1
    assert Crawl(FakeClient(["casa"]), Store(tmp_path), SPANISH).run_words(["zzz"]) == 0


def test_a_torn_last_row_is_dropped_and_the_next_row_stays_clean(tmp_path):
    good = {"word": "casa", "dictionary": "espanhol", "url": "u", "status": "found",
            "headword": "casa", "homograph": None, "related": []}
    (tmp_path / "entries.jsonl").write_text(json.dumps(good) + '\n{"word": "ca', encoding="utf-8")
    store = Store(tmp_path)
    assert len(store.rows) == 1
    store.add_entry(Entry("pe", "espanhol", "v", NO_ENTRY))
    assert [row["word"] for row in Store(tmp_path).rows] == ["casa", "pe"]


def test_guided_level_tries_only_letters_of_held_words():
    letters = Letters.from_words("guided", ["casa", "cedo"])
    assert letters.children("c") == ["ca", "ce"]


def test_trigram_level_keeps_a_letter_seen_elsewhere_in_a_word():
    letters = Letters.from_words("trigram", ["abcd"])
    assert letters.children("zab") == ["zabc"]
    assert letters.children("zbc") == ["zbcd"]
    assert letters.children("zxy") == []


def test_a_later_dictionary_fetches_only_what_the_first_misses(tmp_path):
    store = Store(tmp_path)
    walk(FakeClient(["casa"]), store)
    german = FakeClient(["casa", "haus"])
    walk(german, store, dictionary=GERMAN)
    assert german.lookups == ["haus"]


class FakeResponse:
    def __init__(self, status, text="", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}
        self.encoding = None
        self.is_redirect = status in (301, 302)


class FakeSession(requests.Session):
    def __init__(self, replies):
        super().__init__()
        self.replies = list(replies)
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


FIXTURE = Path(__file__).parent / "fixtures" / "pessoa.html"


def client(replies):
    return Client(min_interval=0, session=FakeSession(replies), server_error_backoff=(0, 0),
                  refusal_pause=0)


def test_one_refusal_pauses_and_retries():
    page = FIXTURE.read_text(encoding="utf-8")
    entry = client([FakeResponse(429), FakeResponse(200, page)]).lookup(SPANISH, "pessoa")
    assert entry.headword == "pessoa"


@pytest.mark.parametrize("status", [403, 429])
def test_a_repeated_refusal_stops_the_crawl(status):
    with pytest.raises(Blocked, match="repeated"):
        client([FakeResponse(status), FakeResponse(status)]).lookup(SPANISH, "pessoa")


def test_server_errors_back_off_and_then_stop():
    errors = [FakeResponse(503), FakeResponse(503), FakeResponse(503)]
    session_client = client(errors)
    with pytest.raises(Blocked, match="503"):
        session_client.lookup(SPANISH, "pessoa")
    assert session_client.session.calls == 3


def test_a_network_error_recovers_after_the_pause():
    page = FIXTURE.read_text(encoding="utf-8")
    replies = [requests.ConnectionError("reset"), FakeResponse(200, page)]
    assert client(replies).lookup(SPANISH, "pessoa").headword == "pessoa"


def test_a_redirect_stops_the_crawl():
    login = FakeResponse(302, headers={"Location": "https://conta.uol.com.br/login"})
    with pytest.raises(Blocked, match="redirect"):
        client([login]).lookup(SPANISH, "pessoa")


def test_the_client_never_sends_an_email_address():
    session_client = client([])
    assert session_client.session.headers["User-Agent"] == "pymichaelis/0.1"
    assert "From" not in session_client.session.headers


def test_requests_keep_the_minimum_interval(monkeypatch):
    now = [0.0]
    sleeps = []
    monkeypatch.setattr("pymichaelis.client.time.monotonic", lambda: now[0])

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("pymichaelis.client.time.sleep", sleep)
    page = FIXTURE.read_text(encoding="utf-8")
    throttled = Client(min_interval=1.0, session=FakeSession([FakeResponse(200, page)] * 3))
    now[0] = 100.0
    for _ in range(3):
        throttled.lookup(SPANISH, "pessoa")
        now[0] += 0.25
    assert sleeps == [0.75, 0.75]


def test_a_block_leaves_a_stopped_file_and_refuses_to_restart(tmp_path, monkeypatch, capsys):
    words = tmp_path / "words.txt"
    words.write_text("pessoa\n", encoding="utf-8")
    data = tmp_path / "data"

    def blocked(self, dictionary, word):
        raise Blocked("captcha")

    monkeypatch.setattr(Client, "lookup", blocked)
    args = ["--data", str(data), "words", "--dictionary", "espanhol", "--file", str(words)]
    assert main(args) == 3
    assert "captcha" in (data / "STOPPED").read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        main(args)


def test_dataset_keeps_one_row_per_headword_and_homograph(tmp_path):
    def row(word, dictionary, homograph=None, status=FOUND):
        return {"headword": word, "homograph": homograph, "status": status, "pos": ["sm"],
                "dictionary": dictionary, "raw": "[k′aru]", "syllables": "ca.ro", "url": "u"}

    rows = [row("caro", "alemao"), row("caro", "espanhol"), row("a, A", "espanhol", 1),
            row("a", "espanhol", 2), row("x", "espanhol", status=NO_ENTRY)]
    entries = unique_entries(rows)
    assert [(e["headword"], e["homograph"], e["dictionary"]) for e in entries] == [
        ("a", 2, "espanhol"), ("a, A", 1, "espanhol"), ("caro", None, "espanhol")]
    path = tmp_path / "out.tsv"
    assert write_tsv(entries, path) == 3
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[3].split("\t")[:4] == ["caro", "", "ˈkaɾu", "[k′aru]"]
