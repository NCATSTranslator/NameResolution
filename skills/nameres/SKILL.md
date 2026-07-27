---
name: nameres
description: Resolve biomedical names to CURIEs using the Translator Name Resolver (NameRes) — turn a term like "type 2 diabetes", "BRCA1", "aspirin" or "Duchenne muscular dystrophy" into a normalized identifier, entity-link a whole list of terms at once, or list every known synonym for a CURIE. Use when text names a disease, gene, protein, chemical, drug, phenotype, cell type or anatomical structure and you need an identifier for it, when normalizing a column of names in a spreadsheet, or when checking whether two names refer to the same concept.
---

# Resolving biomedical names to CURIEs with NameRes

NameRes searches every name and synonym Translator knows for a biomedical concept and returns
ranked, normalized identifiers (CURIEs).

**Base URL:** `https://name-resolution-sri.renci.org/` — if you were given a different NameRes URL,
use that instead. Every instance serves the same API and its own `/llms.txt` and `/status`.
Examples below use bare paths so they work against any of them.

`GET /openapi.json` is the machine-readable authority on parameters and defaults. Prefer it over
`/docs`, which is a JavaScript-rendered Swagger UI and near-useless to read.

## Is this the right service?

| You have | You want | Use |
|---|---|---|
| A name or string (`"aspirin"`) | An identifier | **NameRes** — this service |
| An identifier (`CHEBI:15365`) | Its preferred identifier, equivalent identifiers in other databases, or a description | [NodeNorm](https://nodenormalization-sri.renci.org/docs) — not this service |
| An identifier | Its synonyms | **NameRes** `/synonyms` |

NameRes will not normalize a CURIE you already have. Do not pass a CURIE to `/lookup`.

## Endpoints

| Endpoint | Use |
|---|---|
| `GET`/`POST` `/lookup` | One term → ranked candidate CURIEs |
| `POST /bulk-lookup` | Many terms at once (entity linking / NER) |
| `GET`/`POST` `/synonyms` | Known CURIE → all its names |
| `GET /status` | Which data this instance is serving |
| `/reverse_lookup` | **Deprecated** — use `/synonyms` |

## One term: `/lookup`

```
GET /lookup?string=hypertension&limit=10
```

`POST /lookup` takes the same **query parameters**, not a JSON body.

| Parameter | Default | Notes |
|---|---|---|
| `string` | required | The term to search for |
| `limit` | 10 | Max 1000 |
| `offset` | 0 | Pagination; the only way past the first 1000 results |
| `autocomplete` | false | See below — the most consequential parameter here |
| `highlighting` | false | Adds which label/synonym matched |
| `biolink_type` | none | Repeatable. With or without the `biolink:` prefix. Multiple values are OR'd |
| `only_prefixes` | none | Pipe-separated, **case-sensitive**, e.g. `MONDO\|EFO` |
| `exclude_prefixes` | none | Pipe-separated, case-sensitive |
| `only_taxa` | none | Pipe-separated NCBITaxon CURIEs. Also keeps results that have *no* taxon |
| `debug` | none | `none\|query\|timing\|results\|all`; `results` adds per-result `explain` |

**`autocomplete=false` (the default) is entity-linker mode** — the whole string is treated as a
complete phrase. Set `autocomplete=true` only for search-as-you-type, where the user is still typing
and the last word is a prefix. Using `true` for entity linking produces confident nonsense.

### Reading the response

A ranked list, best first. This is the response *shape* — labels, counts and scores come from the
Babel build the instance is serving and change between releases:

```json
[
  {
    "curie": "MONDO:0005044",
    "label": "hypertension",
    "synonyms": ["hypertension", "HTN", "high blood pressure"],
    "types": ["biolink:Disease", "biolink:DiseaseOrPhenotypicFeature"],
    "taxa": [],
    "score": 3770.1,
    "clique_identifier_count": 33,
    "highlighting": {},
    "explain": null,
    "debug": null
  }
]
```

- **`label`** is the *clique's* preferred name. It is not necessarily the label of `curie` in its own
  source database, and it may differ in case or wording from what you searched for.
- **`types`** carry the `biolink:` prefix here. On `/synonyms` they do **not**. 
- **`synonyms`** is not ordered by quality — do not take `synonyms[0]` as the best name. Use `label`.
- **`score`** is unbounded and has no fixed scale; real scores run into the thousands.
  **Never threshold on an absolute score.** Compare the top score to the second one instead.
- **`highlighting`** is `{}` unless you passed `highlighting=true`, when it becomes
  `{"labels": [...], "synonyms": [...]}`.
- **`explain`** and `debug` are `null` unless you passed `debug=results` or `debug=all`.

Ranking, in short: an exact match on the preferred name outranks an exact match on a synonym, which
outranks a partial match; the score is then scaled by the log of `clique_identifier_count`, so a
widely cross-referenced concept beats an obscure one on an otherwise equal match. Details:
[Scoring](https://github.com/NCATSTranslator/NameResolution/blob/main/documentation/Scoring.md).

## Do not blindly take the top result

The top hit is the best *textual* match, which is not always the concept meant. A real example —
searching `diabetes` returns, in order:

| curie | label | clique_identifier_count |
|---|---|---|
| `UMLS:C0011847` | Diabetes | 1 |
| `MONDO:0005015` | diabetes mellitus | 15 |

The winner is a **single-identifier UMLS concept**, because it matches the string exactly. The
concept a caller almost always wants is the second one. A `UMLS:` CURIE with
`clique_identifier_count: 1` is a leftover-UMLS singleton — real, deliberately included for
coverage, and usually not what you want. See
[Where NameRes data comes from](https://github.com/NCATSTranslator/NameResolution/blob/main/documentation/Babel.md).

**Ask the user to choose** when the term is an abbreviation (`DMD` is both a gene and a disease;
`MS` could be multiple sclerosis or mass spectrometry), when the top few scores are close, or when
the term is a common word (`cold`, `positive`, `marker`). Show `label`, `curie` and the first `types`
entry for the top 10–25 and let them pick.

**Narrow the query instead of guessing:**

1. `biolink_type=Disease` when context makes the type clear.
2. `only_prefixes` to prefer a canonical vocabulary:

   | Concept | Prefixes |
   |---|---|
   | Disease | `MONDO` |
   | Phenotype | `HP` |
   | Gene | `NCBIGene`, `HGNC` |
   | Chemical / drug | `CHEBI`, `CHEMBL`, `PUBCHEM.COMPOUND` |
   | Protein | `UniProtKB` |
   | Anatomy | `UBERON` |
   | Cell type | `CL` |
   | Taxon | `NCBITaxon` |

   `exclude_prefixes=UMLS` is a blunt but effective way to drop the singletons above.
3. `only_taxa=NCBITaxon:9606` for human genes and proteins.
4. `highlighting=true` to see *which* synonym matched, or `debug=results` for the score breakdown.

**Deciding whether two names are the same concept:** resolve both and compare the returned `curie`.
Equal CURIEs mean Babel considers them the same concept. Different CURIEs are not proof they differ —
check with [NodeNorm](https://nodenormalization-sri.renci.org/docs), since conflation may relate them.

## Many terms: `/bulk-lookup`

For three or more terms, and for any entity-linking or NER pass, use this instead of looping over
`/lookup`.

```
POST /bulk-lookup
Content-Type: application/json

{
  "strings": ["diabetes", "hypertension", "aspirin"],
  "limit": 10,
  "autocomplete": false,
  "biolink_types": ["Disease"]
}
```

**The body field is `biolink_types` (plural).** The `/lookup` query parameter is `biolink_type`
(singular). Using the wrong one is silently ignored rather than rejected.

Every filter applies to every string in the batch. If different terms need different filters, send
separate requests. The response is a dictionary keyed by your input strings, each value a ranked list
of the same objects `/lookup` returns.

## A known CURIE: `/synonyms`

```
GET /synonyms?preferred_curies=NCBIGene:1756&preferred_curies=MONDO:0005015
```

`POST /synonyms` takes `{"preferred_curies": [...]}`. This endpoint does **not** normalize — pass a
preferred CURIE, which you can get from `/lookup` or from NodeNorm.

It returns the raw underlying document, keyed by CURIE, and so differs from `/lookup` in ways that
will trip you up:

- Field names are `preferred_name` and `names`, not `label` and `synonyms`.
- **`types` have no `biolink:` prefix** here (`"Gene"`, not `"biolink:Gene"`).
- **`taxa` is omitted entirely** when the concept has none, where `/lookup` returns `[]`.
- An unknown CURIE comes back as an **empty object** `{}` — the key is present, not missing, not null.
- There are extra index fields (`id`, `_version_`, `curie_suffix`, `shortest_name_length`); ignore them.

These divergences are known and tracked in
[issue #291](https://github.com/NCATSTranslator/NameResolution/issues/291).

## Which data am I querying?

```
GET /status
```

Reports `babel_version` (the Babel build this index was made from), `nameres_version`, the Biolink
model tag, and the conflations baked into the index. Older deployments may omit some of these; if
`babel_version` is absent, the instance is running an older NameRes build.

An instance serves a **fixed snapshot**, not live data. A concept Babel has since fixed stays wrong
here until the instance is rebuilt.

## Conflation, and why results look the way they do

Conflation is **baked into the index when it is built** and cannot be turned off per query — unlike
NodeNorm, which takes conflation flags per request.

- **GeneProtein:** a protein is searchable, but the result is identified by the *gene* that encodes
  it, and carries both sets of synonyms. There is no separate document for the protein.
- **DrugChemical:** a drug is identified by its active ingredient.

So searching a protein name and getting `NCBIGene:…` back is correct behaviour, not a bug. Once you
have a CURIE, use [NodeNorm](https://nodenormalization-sri.renci.org/docs) to see the clique with and
without each conflation.

Other Babel-derived behaviour worth knowing, all covered in
[Where NameRes data comes from](https://github.com/NCATSTranslator/NameResolution/blob/main/documentation/Babel.md):

- Empty `taxa` means no source asserted a taxon — not that the concept is taxon-agnostic. That is why
  `only_taxa` keeps untaxoned results.
- `clique_identifier_count` counts how many source vocabularies cover the concept. It is a decent
  proxy for "well known", which is why it boosts the score, but it is not importance.
- There are **no descriptions** in NameRes. Use NodeNorm's `description` flag.
- A concept that is missing, wrongly merged, or wrongly labelled is a
  [Babel](https://github.com/NCATSTranslator/Babel/issues) issue. Bad *ranking* or an API error is a
  [NameRes](https://github.com/NCATSTranslator/NameResolution/issues) issue.

## Full reference

[API documentation](https://github.com/NCATSTranslator/NameResolution/blob/main/documentation/API.md)
for every parameter and field;
[Translator Guide](https://github.com/NCATSTranslator/NameResolution/blob/main/documentation/TranslatorGuide.md)
for worked troubleshooting and performance advice.
