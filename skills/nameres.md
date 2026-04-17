# Skill: Resolve Biomedical Names to CURIEs using NameRes

## Overview

The Name Resolver (NameRes) maps lexical strings (names, synonyms, abbreviations) to normalized CURIEs from biomedical ontologies. Use it whenever you need a stable, normalized identifier for a biomedical concept before calling a downstream service.

- **Base URL:** `https://name-resolution-sri.renci.org/`
- **Interactive docs:** `https://name-resolution-sri.renci.org/docs`
- **Three endpoints:** `/lookup`, `/bulk-lookup`, `/synonyms`

All CURIEs returned are normalized using the Node Normalization service and are subject to GeneProtein and DrugChemical conflation (see [Conflation](#conflation)).

---

## Endpoint: `/lookup` — Find CURIEs for a single name

**When to use:** You have one biomedical term and need candidate CURIEs.

```
GET https://name-resolution-sri.renci.org/lookup?string=<term>&limit=10
```

POST is also supported (send `string` as a query parameter).

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `string` | string | required | The term to search |
| `limit` | integer | 10 | Number of results (max 1000). Use 10–25 when disambiguating. |
| `autocomplete` | boolean | false | `true` = prefix/partial matching (for search-as-you-type). `false` = exact entity linking (preferred for most uses). |
| `highlighting` | boolean | false | If `true`, response includes which labels/synonyms matched. Useful for debugging unexpected results. |
| `biolink_type` | string[] | [] | Filter to specific Biolink types (e.g., `Disease`, `Gene`, `ChemicalEntity`). Multiple values are OR'd. |
| `only_prefixes` | string | | Pipe-separated list of CURIE prefixes to include (e.g., `MONDO\|EFO`). Case-sensitive. |
| `exclude_prefixes` | string | | Pipe-separated list of CURIE prefixes to exclude (e.g., `UMLS\|EFO`). Case-sensitive. |
| `only_taxa` | string | | Pipe-separated NCBI taxon CURIEs (e.g., `NCBITaxon:9606` for human). Results without any taxon annotation are always included. |

### Response

A ranked list of `LookupResult` objects:

```json
[
  {
    "curie": "MONDO:0005148",
    "label": "diabetes mellitus",
    "score": 42.5,
    "types": ["biolink:Disease", "biolink:DiseaseOrPhenotypicFeature"],
    "taxa": [],
    "clique_identifier_count": 125,
    "synonyms": ["diabetes", "DM", "diabetes mellitus", "sugar diabetes"],
    "highlighting": {}
  }
]
```

### Scoring

Results are ranked by a TF*IDF score with field boosts:
- Exact preferred-name match: **250×** boost
- Exact synonym match: **100×** boost
- Partial preferred-name match: **25×** boost
- Partial synonym match: **10×** boost

The score is also multiplied by `log10(clique_identifier_count + 1)`, so widely-used, well-populated concepts rank higher when scores are otherwise similar. Results are sorted by score descending, then by clique size descending, then by CURIE suffix ascending.

---

## Choosing the Right Match — Disambiguation

**The top result is often correct, but do not blindly use it.** Scoring is strong for unambiguous terms and weaker for abbreviations, common words, and homonyms.

### When the top result is likely correct

- The `label` closely matches the input string (e.g., searching "diabetes mellitus" and getting back `label: "diabetes mellitus"`)
- The `score` of the top result is substantially higher than the second result
- The `types` are consistent with what you expect

### When to ask the user to choose

- The term is an **abbreviation** (e.g., "DMD" could be the gene *dystrophin* or the disease *Duchenne muscular dystrophy*; "MS" could be *multiple sclerosis* or *mass spectrometry*)
- **Multiple top results have similar scores** with different labels
- The term is a **common word** that appears in many contexts (e.g., "cold", "positive", "marker")
- The expected type is **ambiguous** (e.g., a term that could be a disease or a phenotypic feature)

When asking the user, present the top 10–25 results with `label`, `curie`, and the first `types` entry so they can identify the intended concept.

### Disambiguation strategies

1. **Add a `biolink_type` filter** when context makes the type clear. For example, if resolving names from a disease list, add `biolink_type=Disease`.

2. **Add `only_prefixes`** to prefer a canonical ontology for the domain:

   | Concept type | Preferred prefixes |
   |---|---|
   | Disease | `MONDO` |
   | Phenotype / clinical finding | `HP` |
   | Gene | `NCBIGene`, `HGNC` |
   | Chemical / drug | `CHEBI`, `CHEMBL`, `PUBCHEM.COMPOUND` |
   | Protein | `UniProtKB` |
   | Anatomical entity | `UBERON` |
   | Cell type | `CL` |
   | Taxon | `NCBITaxon` |

3. **Add `only_taxa=NCBITaxon:9606`** when you need human-specific results (genes, proteins).

4. **Use `highlighting=true`** to see which synonym triggered the match — useful for explaining unexpected results to the user.

---

## Endpoint: `/bulk-lookup` — Resolve multiple names in one request

**When to use:** You have 3 or more terms to resolve. Prefer this over serial `/lookup` calls.

```
POST https://name-resolution-sri.renci.org/bulk-lookup
Content-Type: application/json
```

Request body:

```json
{
  "strings": ["diabetes", "hypertension", "aspirin"],
  "limit": 10,
  "biolink_types": ["Disease"],
  "only_prefixes": "",
  "exclude_prefixes": "",
  "only_taxa": "",
  "autocomplete": false,
  "highlighting": false
}
```

Note: the body field is `biolink_types` (plural), unlike the query parameter `biolink_type` in `/lookup`.

All filter parameters apply uniformly to every string in the batch. If different strings need different filters, make separate requests.

### Response

A dictionary keyed by input string, each value is a ranked list of `LookupResult` objects (same structure as `/lookup`):

```json
{
  "diabetes": [
    { "curie": "MONDO:0005148", "label": "diabetes mellitus", "score": 42.5, ... }
  ],
  "hypertension": [
    { "curie": "MONDO:0005044", "label": "hypertension", "score": 38.1, ... }
  ],
  "aspirin": [
    { "curie": "CHEBI:15365", "label": "aspirin", "score": 51.2, ... }
  ]
}
```

---

## Endpoint: `/synonyms` — Retrieve all names for a known CURIE

**When to use:**
- You have a CURIE and need its full synonym list (e.g., to search text for mentions of a concept)
- You want to confirm what concept a CURIE refers to and what Biolink types it has
- You need the taxa or clique size for a set of CURIEs

```
GET https://name-resolution-sri.renci.org/synonyms?preferred_curies=MONDO:0005148
```

For multiple CURIEs, repeat the parameter:

```
GET https://name-resolution-sri.renci.org/synonyms?preferred_curies=MONDO:0005148&preferred_curies=NCBIGene:1756
```

POST is also supported:
```json
{ "preferred_curies": ["MONDO:0005148", "NCBIGene:1756"] }
```

### Response

A dictionary keyed by CURIE:

```json
{
  "MONDO:0005148": {
    "curie": "MONDO:0005148",
    "preferred_name": "diabetes mellitus",
    "names": ["diabetes mellitus", "diabetes", "DM", "sugar diabetes", "T2DM", ...],
    "types": ["Disease", "DiseaseOrPhenotypicFeature", ...],
    "taxa": [],
    "clique_identifier_count": 125
  },
  "NCBIGene:1756": {
    "curie": "NCBIGene:1756",
    "preferred_name": "DMD",
    "names": ["DMD", "dystrophin", "DYSTROPHIN", "BMD", ...],
    "types": ["Gene", "GeneOrGeneProduct", ...],
    "taxa": ["NCBITaxon:9606"],
    "clique_identifier_count": 22
  }
}
```

If a CURIE is not found, its value will be an empty object `{}`.

---

## Decision Guide

```
Need a CURIE for a name?
  One term   → GET /lookup?string=<term>&limit=10
  Many terms → POST /bulk-lookup  {"strings": [...]}

Result ambiguous?
  Context implies a type  → add biolink_type=Disease (or Gene, ChemicalEntity, etc.)
  Context implies a source → add only_prefixes=MONDO (or NCBIGene, CHEBI, etc.)
  Still ambiguous         → fetch limit=25, show label+curie+types to user, ask them to pick

Have a CURIE, need its names/metadata?
  → GET /synonyms?preferred_curies=<curie>
```

---

## Conflation

NameRes applies two conflations at index-build time (not on-the-fly):

- **GeneProtein conflation:** Protein-encoding genes are conflated with the protein(s) they encode. The gene identifier is used; searching for a protein name will return the gene CURIE.
- **DrugChemical conflation:** Drugs are conflated with their active ingredient. The active ingredient's identifier is used.

This means you may not find a separate entry for a specific protein or brand-name drug — look for the gene or active ingredient instead. Once you have a CURIE, you can use [Node Normalization](https://nodenormalization-sri.renci.org/) to retrieve equivalent identifiers with and without conflation applied.
