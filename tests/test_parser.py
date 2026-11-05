"""Expected values come from the text of the saved pages, not from the parser."""
from pathlib import Path

import pytest

from pymichaelis import FOUND, NO_ENTRY, Blocked, parse_entry, parse_suggestions

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://michaelis.uol.com.br/x/"


def parse(name, word="w"):
    html = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    return parse_entry(html, word, "espanhol", URL)


def test_pessoa_keeps_the_raw_string_with_the_served_code_points():
    entry = parse("pessoa")
    assert entry.status == FOUND
    assert entry.headword == "pessoa"
    assert entry.syllables == "pes.so.a"
    assert entry.raw == "[pes′oә]"
    assert entry.ipa == "peˈsoə"
    assert entry.pos == ("sf",)
    assert entry.homograph is None


def test_two_homographs_carry_different_vowels():
    noun, verb = parse("colher_1"), parse("colher_2")
    assert (noun.homograph, noun.raw, noun.pos) == (1, "[koλ′ɛr]", ("sf",))
    assert (verb.homograph, verb.raw, verb.pos) == (2, "[koλ′er]", ("vtd",))


def test_a_homograph_page_links_to_its_sibling():
    link = next(item for item in parse("colher_1").related if item.homograph)
    assert (link.text, link.homograph, link.path) == ("colher", 2, "/palavra/Dl8zD/colher/")


def test_accented_link_text_survives():
    assert [(item.text, item.homograph) for item in parse("avo").related] == [("avô", None), ("avós", None)]


def test_an_unaccented_query_records_the_printed_headword():
    entry = parse("cafe", word="cafe")
    assert entry.word == "cafe"
    assert entry.headword == "café"


def test_an_accent_difference_still_matches_the_query():
    assert parse("cafe", word="cafe").matches_query


def test_a_fallback_to_a_longer_headword_does_not_match_the_query():
    entry = parse("flag_fallback", word="flag")
    assert entry.headword == "flagelação"
    assert not entry.matches_query


def test_a_second_printed_form_matches_the_query():
    assert parse("a", word="A").matches_query


def test_every_part_of_speech_is_kept():
    assert parse("cafe").pos == ("sm", "adj m+f sing+pl")


def test_a_hyphenated_headword_keeps_its_hyphen():
    entry = parse("guarda_chuva")
    assert entry.headword == "guarda-chuva"
    assert entry.syllables == "guar.da-chu.va"
    assert entry.raw == "[gwardәʃ′uvә]"


def test_a_page_with_two_headword_tags():
    entry = parse("a")
    assert entry.headword == "a, A"
    assert entry.homograph == 1
    assert entry.raw == "[a]"


def test_a_page_with_one_headword_tag_holding_two_forms():
    entry = parse("e")
    assert entry.headword == "e, E"
    assert entry.homograph is None
    assert entry.raw == "[′e]"


def test_a_miss_is_recorded_and_carries_no_data():
    entry = parse("not_found", word="zzzqq")
    assert entry.status == NO_ENTRY
    assert entry.word == "zzzqq"
    assert entry.headword is None
    assert entry.raw is None


@pytest.mark.parametrize(
    "html",
    [
        "",
        "<html><head><title>Just a moment...</title></head><body>challenge</body></html>",
        "<html><head><title>Michaelis</title></head><body>"
        "<form action='/login'><input type='password'></form></body></html>",
    ],
)
def test_an_unknown_page_stops_the_crawl(html):
    with pytest.raises(Blocked):
        parse_entry(html, "w", "espanhol", URL)


def test_suggestions_as_served():
    body = '{"suggestions":[{"value":"pessoal"},{"value":"p\\u00e9ssimo"}]}'
    assert parse_suggestions(body) == ("pessoal", "péssimo")


def test_no_suggestions_as_served():
    assert parse_suggestions('{"suggestions":null}') == ()


def test_a_suggest_reply_that_is_not_json_stops_the_crawl():
    with pytest.raises(Blocked):
        parse_suggestions("<html>captcha</html>")
