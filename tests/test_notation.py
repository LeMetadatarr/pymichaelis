"""Each case is an example of the publisher's key, with the IPA written by hand."""
import re
from pathlib import Path

import pytest

from pymichaelis import UnknownSymbol, to_ipa
from pymichaelis.notation import SYMBOLS

KEY = Path(__file__).parent / "fixtures" / "transcricao_fonetica.html"


@pytest.mark.parametrize(
    "raw, ipa",
    [
        ("[k′aru]", "ˈkaɾu"),
        ("[f′ɛ]", "ˈfɛ"),
        ("[′uva]", "ˈuva"),
        ("[d′ẽti]", "ˈdẽti"),
        ("[f′ĩ]", "ˈfĩ"),
        ("[′õsə]", "ˈõsə"),
        ("[p′ejʃi]", "ˈpejʃi"),
        ("[m′aw]", "ˈmaw"),
        ("[g′ɛr̄a]", "ˈɡɛʁa"),
        ("[ʒig′ãti]", "ʒiˈɡãti"),
        ("[fiks′ãw]", "fikˈsãw"),
        ("[kak′i]", "kaˈki"),
        ("[λ′ãmə]", "ˈʎãmə"),
        ("[k′aλə]", "ˈkaʎə"),
        ("[l′iñu]", "ˈliɲu"),
        ("[′arma]", "ˈaɾma"),
        ("[aʃ′ar]", "aˈʃaɾ"),
        ("[r̄′atu]", "ˈʁatu"),
        ("[kor̄′er]", "koˈʁeɾ"),
        ("[fr′asku]", "ˈfɾasku"),
        ("[sos′egu]", "soˈseɡu"),
        ("[esploz′ivu]", "esploˈzivu"),
        ("[ẽʃ′ʌmi]", "ẽˈʃɐmi"),
        ("[k′ʌma]", "ˈkɐma"),
        ("[r̄′ɔzə]", "ˈʁɔzə"),
        ("[ez′emplu]", "eˈzemplu"),
    ],
)
def test_key_examples(raw, ipa):
    assert to_ipa(raw) == ipa


@pytest.mark.parametrize(
    "raw, ipa",
    [
        ("[pes′oә]", "peˈsoə"),
        ("[dez′ojtu]", "deˈzojtu"),
        ("[gwardәʃ′uvә]", "ɡwaɾdəˈʃuvə"),
        ("[pa′is]", "paˈis"),
    ],
)
def test_entries_as_served(raw, ipa):
    assert to_ipa(raw) == ipa


@pytest.mark.parametrize(
    "raw, ipa",
    [
        ("[kajʃ′ɔti]", "kajˈʃɔti"),
        ("[adapt′ar]", "adapˈtaɾ"),
        ("[abr′ir]", "aˈbɾiɾ"),
        ("[adekw′adu]", "adeˈkwadu"),
        ("[pn′ew]", "ˈpnew"),
        ("[maj′o]", "maˈjo"),
        ("[kõstr′uir]", "kõsˈtɾuiɾ"),
        ("[abstr′atu]", "absˈtɾatu"),
    ],
)
def test_stress_moves_to_the_start_of_the_syllable(raw, ipa):
    assert to_ipa(raw) == ipa


@pytest.mark.parametrize(
    "raw, ipa",
    [
        ("[ãʒelic′aw]", "ãʒeliˈkaw"),
        ("[awtocõtr′oli]", "awtokõˈtɾoli"),
        ("[prɛcoz′er]", "pɾɛkoˈzeɾ"),
        ("[t′ocu]", "ˈtoku"),
        ("[ãtisoci′aw]", "ãtisosiˈaw"),
        ("[awspici′ozu]", "awspisiˈozu"),
        ("[az′uwcel′ɛsti]", "aˈzuwseˈlɛsti"),
        ("[evolucjon′ismu]", "evolusjoˈnismu"),
        ("[placid′es]", "plasiˈdes"),
    ],
)
def test_a_printed_c_takes_its_spelling_value(raw, ipa):
    assert to_ipa(raw) == ipa


def test_a_symbol_outside_the_key_is_refused():
    with pytest.raises(UnknownSymbol):
        to_ipa("[kaθa]")


def test_an_empty_transcription_is_refused():
    with pytest.raises(UnknownSymbol):
        to_ipa("[]")


def test_every_mapped_symbol_appears_in_the_saved_key():
    key = KEY.read_text(encoding="utf-8")
    printed = set(re.findall(r"\[([^\]\s]{1,2})\]", key))
    for symbol in set(SYMBOLS) - {"ә"}:
        assert symbol in printed, symbol
