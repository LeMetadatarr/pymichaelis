# pymichaelis

pymichaelis reads the phonetic transcriptions of Brazilian Portuguese from the
Portuguese-to-foreign halves of the Michaelis Dicionário Escolar. It keeps each
transcription exactly as the site serves it, and it adds an IPA form beside it.

## Use

```python
from pymichaelis import DICTIONARIES, Client

client = Client()
entry = client.lookup(DICTIONARIES["espanhol"], "pessoa")
print(entry.headword, entry.raw, entry.ipa, entry.pos)
# pessoa [pes′oә] peˈsoə ('sf',)
```

The site ignores accents, so `cafe` returns `café`. For a missing word it can
return a longer headword: `flag` returns `flagelação`. Check `entry.matches_query`
before you trust an entry for the word you asked for. In the crawl logs, `word` is
the query and `headword` is the printed form, so key on `headword`.

A lookup returns only the first homograph. The others are "Veja também" links:

```python
dictionary = DICTIONARIES["espanhol"]
noun = client.lookup(dictionary, "colher")
verb = client.follow(dictionary, next(l for l in noun.related if l.homograph == 2))
print(noun.raw, verb.raw)  # [koλ′ɛr] [koλ′er]
```

`Client` sends one request at a time over one connection, at most one per
`min_interval` seconds. It raises `Blocked` on a redirect, a repeated 403 or
429, a page it does not recognise, or a 5xx error that persists. Treat `Blocked`
as final: stop and read the reason.

## Dictionaries

| Name | Search path | Suggest parameters |
|---|---|---|
| `espanhol` | `/moderno-espanhol/busca/portugues-espanhol/<word>/` | `r=3 f=0 t=2` |
| `alemao` | `/moderno-alemao/busca/portugues-alemao/<word>/` | `r=4 f=0 t=3` |
| `italiano` | `/moderno-italiano/busca/portugues-italiano/<word>/` | `r=5 f=0 t=4` |
| `frances` | `/moderno-frances/busca/portugues-frances/<word>/` | `r=6 f=0 t=5` |

The Portuguese-to-English dictionary has no transcription.

## Notation

`to_ipa` follows the publisher's key at
<https://michaelis.uol.com.br/escolar-espanhol/transcricao-fonetica-do-portugues/>.

| Michaelis | IPA | What the key says |
|---|---|---|
| `′` before the vowel | `ˈ` before the syllable | The stress mark precedes the stressed vowel, as in `[f′ɛ]`. |
| `r` | `ɾ` | "para r brando: arma [′arma], achar [aʃ′ar]" |
| `r̄` | `ʁ` | "para r forte e aspirado, inicial ou rr: rato [r̄′atu], correr [kor̄′er]" |
| `ә` (U+04D9) | `ə` | The key prints U+0259: "mesa [m′esə]". Entries serve the Cyrillic letter. |
| `ʌ` | `ɐ` | "cama [k′ʌma], cana [k′ʌna]" |
| `λ` | `ʎ` | "para lh: lhama [λ′ãmə], calha [k′aλə]" |
| `ñ` | `ɲ` | "para nh: linho [l′iñu]" |
| `g` | `ɡ` | "gato [g′atu]" |

Every other symbol of the key is already IPA. `to_ipa` raises `UnknownSymbol` on
a symbol outside the key, and the crawl then stores `ipa` as null beside the raw
text.

The key defines `ʌ` by example only, so the `ɐ` is a reading of the key. The key
also calls `r̄` "aspirado", and `ʁ` is the symbol that orthography2ipa writes for
this rhotic. The raw text stays in every row, so a consumer can read these two
symbols differently.

## Crawl

The site has no headword index. Its autocomplete returns at most five headwords
for a prefix, so the crawl walks a prefix tree: a reply of fewer than five is
complete, and a full reply means it must go one letter deeper.

```bash
python -m pymichaelis.crawl --data crawl walk --dictionary espanhol --level guided --held words.txt
python -m pymichaelis.crawl --data crawl walk --dictionary espanhol --level trigram --held words.txt
python -m pymichaelis.crawl --data crawl walk --dictionary espanhol --level full
python -m pymichaelis.crawl --data crawl status
python -m pymichaelis.dataset crawl/entries.jsonl michaelis.tsv
```

The levels limit which letters the walk tries after a full reply. `guided` tries
the letters that continue a held word, `trigram` the letters that form a trigram
of the held words, and `full` the whole alphabet. Each level reuses the replies
of the level before it.

Two append-only logs hold all the state. `entries.jsonl` has one row per page
fetch, misses included. `prefixes.jsonl` has one row per autocomplete reply. A
restart rebuilds its queues from them and sends no request twice. A lock file
allows one crawl per directory, because the rate limit assumes one.

On a block, the crawl writes the reason to `STOPPED` and exits with status 3. It
refuses to start while that file exists. Read the reason before you remove it.

## Test

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e '.[test]'
.venv/bin/python -m pytest
```

The tests parse saved pages from `tests/fixtures`. No test touches the network.
