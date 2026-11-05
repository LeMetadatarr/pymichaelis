---
name: pymichaelis
description: Michaelis Dicionário Escolar client — fetch Brazilian Portuguese phonetic transcriptions per headword, keep the raw notation, convert it to IPA, and crawl the headwords resumably
---
# pymichaelis

Reads the transcription printed on each Portuguese headword of the Michaelis
Dicionário Escolar, for example `pessoa [pes′oә]`.

## When to use

- "How does Michaelis transcribe this Portuguese word?"
- "Does this word have an open or a closed mid vowel in Brazilian Portuguese?"
- "Where does Michaelis put the stress?"
- "Build or extend the pt-BR pronunciation list from Michaelis."

## Install

```bash
uv pip install -e .
```

## Core operations

### `Client.lookup(dictionary, word) -> Entry`

```python
from pymichaelis import DICTIONARIES, Client

client = Client()
entry = client.lookup(DICTIONARIES["espanhol"], "pessoa")
print(entry.status, entry.headword, entry.raw, entry.ipa, entry.pos)
```

`entry.status` is `found`, `no_entry` or `no_transcription`. The site ignores
accents: `cafe` returns `café`, and `entry.headword` holds the printed form. For a
missing word it can return a longer headword (`flag` returns `flagelação`), so
check `entry.matches_query`.

### `Client.follow(dictionary, link) -> Entry`

A lookup returns the first homograph only. Follow the links in `entry.related`
that carry a `homograph` number to reach the others.

### `Client.suggest(dictionary, prefix) -> tuple[str, ...]`

Returns at most five headwords. Fewer than five means the list is complete for
that prefix.

### `to_ipa(raw) -> str`

Converts `[pes′oә]` to `peˈsoə`. Raises `UnknownSymbol` on a symbol outside the
publisher's key.

## Crawl

```bash
python -m pymichaelis.crawl --data crawl walk --dictionary espanhol --level full
python -m pymichaelis.crawl --data crawl status
python -m pymichaelis.dataset crawl/entries.jsonl michaelis.tsv
```

## Rules

- Send at most one request per second, over one connection, with one crawl at a
  time. `Client` enforces this. Do not lower `min_interval` below 1.
- `Blocked` is final. Stop and report it. Do not change the client name, and do
  not use a proxy or an archive copy.
- Keep `raw` as served. Use `ipa` for comparison only.
- The notation is a teaching norm: it has no [tʃ dʒ] and no regional coda. Use it
  for stress and mid-vowel quality, not for allophones.
