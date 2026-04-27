# NameRes Translator Guide

This guide is aimed at Translator developers and users who are integrating NameRes into their workflows.
It covers what to do when results are unexpected, how `/synonyms` (reverse-lookup) relates to NodeNorm,
and tips for improving performance.

## What to do when a name lookup returns unexpected results

NameRes ranks results by a [Solr TF*IDF score](./Scoring.md) — the top result is the best *textual* match,
not necessarily the biologically intended concept. If the results don't look right, try these steps.

### 1. Use `highlighting` to understand what matched

Set `highlighting=true` on a `/lookup` call to see which label or synonym drove the match:

```
GET /lookup?string=cold&highlighting=true&limit=5
```

This tells you which synonym triggered the match, which helps diagnose why an unexpected concept ranked high.

### 2. Filter by Biolink type

Use `biolink_type` to restrict results to the category you expect. Multiple types are combined with OR logic:

```
GET /lookup?string=cold&biolink_type=Disease&biolink_type=PhenotypicFeature
```

Common types: `Disease`, `Gene`, `ChemicalEntity`, `PhenotypicFeature`, `BiologicalProcess`, `AnatomicalEntity`.
Types can be specified with or without the `biolink:` prefix.

### 3. Restrict to trusted prefixes

Use `only_prefixes` to limit results to a specific ontology, or `exclude_prefixes` to drop a noisy one.
Prefixes are pipe-separated and case-sensitive:

```
# Only MONDO disease identifiers
GET /lookup?string=diabetes&biolink_type=Disease&only_prefixes=MONDO

# Exclude UMLS (often produces many ambiguous matches)
GET /lookup?string=NIH&exclude_prefixes=UMLS
```

Common trusted prefixes by category:

| Category | Recommended prefixes |
|---|---|
| Disease | `MONDO`, `OMIM`, `ORPHANET` |
| Gene | `NCBIGene`, `HGNC` |
| Chemical/Drug | `CHEBI`, `DRUGBANK` |
| Phenotype | `HP`, `MP` |
| Anatomy | `UBERON`, `CL` |

### 4. Filter by taxon for gene/protein queries

When searching for a gene or protein, results may include entries from multiple species. Use `only_taxa`
to restrict to a specific organism. The value is a pipe-separated list of NCBI Taxon CURIEs:

```
# Human genes only
GET /lookup?string=APOE&biolink_type=Gene&only_taxa=NCBITaxon:9606

# Human and mouse
GET /lookup?string=APOE&only_taxa=NCBITaxon:9606|NCBITaxon:10090
```

Common taxa: human `NCBITaxon:9606`, mouse `NCBITaxon:10090`, rat `NCBITaxon:10116`, zebrafish `NCBITaxon:7955`.

### 5. Try autocomplete mode for partial strings

If your search string is a fragment of a name (e.g., typed by a user mid-word), set `autocomplete=true`.
This expands the final word with a wildcard so that `"diab"` matches `"diabetes"`, `"diabetic"`, etc.:

```
GET /lookup?string=diab&autocomplete=true&limit=5
```

Without `autocomplete`, `"diab"` will only match documents that literally contain the token `"diab"`.

### 6. If the correct concept is consistently missing

If your filtering is correct but the expected result never appears, the concept may be missing from the
Babel data that NameRes is built from. Consider filing an issue on:
- [NameRes GitHub](https://github.com/NCATSTranslator/NameResolution/issues) — for search/ranking problems
- [Babel GitHub](https://github.com/NCATSTranslator/Babel/issues) — for missing synonyms or identifiers

---

## Using `/synonyms` (reverse-lookup) vs. NodeNorm

These two services answer different questions.

### Use `/synonyms` when you want to inspect synonyms for a known CURIE

The `/synonyms` endpoint returns all names and synonyms that NameRes knows for a given concept, along with
its Biolink types, taxa, and clique identifier count. This is useful for verifying synonym coverage or
debugging why a particular name did or did not match.

```
GET /synonyms?preferred_curies=NCBIGene:1756
```

**Important:** `/synonyms` requires the *preferred* (normalized) CURIE. If you pass a non-preferred
identifier (e.g. a UniProtKB accession for a gene), you will get an empty result. Before calling
`/synonyms`, normalize your CURIE with NodeNorm (see below).

You can look up multiple CURIEs in one request:

```
GET /synonyms?preferred_curies=MONDO:0005148&preferred_curies=NCBIGene:1756
```

### Use NodeNorm when you need identifier normalization or equivalent identifiers

The [Node Normalization service](https://nodenormalization-sri.renci.org/) is the right tool when you need to:

- Convert a non-preferred identifier to its preferred CURIE
- Find all equivalent identifiers for a concept across ontologies
- Check which Biolink types a CURIE maps to
- Determine whether two CURIEs refer to the same concept

To normalize a CURIE before passing it to `/synonyms`, call NodeNorm with GeneProtein and DrugChemical
conflation enabled (to match the conflation used by NameRes):

```
GET https://nodenormalization-sri.renci.org/get_normalized_nodes?curie=UniProtKB:A0A0S2Z3B5&conflate=true&drug_chemical=true
```

The `id.identifier` field in the response is the preferred CURIE you can then pass to `/synonyms`.

### Quick decision guide

| Question | Tool |
|---|---|
| What synonyms does NameRes know for this CURIE? | `/synonyms` |
| What is the preferred identifier for this concept? | NodeNorm |
| Are these two CURIEs equivalent? | NodeNorm |
| What Biolink types does this CURIE have? | NodeNorm |
| Why didn't a particular name match in `/lookup`? | `/synonyms` + `highlighting` |

---

## Performance tips

### Batch multiple queries with `/bulk-lookup`

Instead of making N separate `/lookup` calls, send them all in one POST request to `/bulk-lookup`.
It returns a dictionary keyed by input string:

```json
POST /bulk-lookup
{
  "strings": ["diabetes", "hypertension", "asthma"],
  "limit": 5,
  "biolink_types": ["Disease"]
}
```

This is significantly more efficient than sequential individual requests.

### Add filters before processing results

Apply `biolink_type`, `only_prefixes`, and `only_taxa` at query time rather than filtering the response
yourself. Server-side filtering reduces the result set before it is serialized and transmitted.

### Set `limit` to what you actually need

The default `limit` is 10 and the maximum is 1000. If you only need the top result, set `limit=1`.
If you need to page through a large result set, use `offset` for server-side pagination rather than
requesting a large `limit` and slicing client-side.

### Cache results between Babel data releases

NameRes results are stable between Babel data releases (which happen a few times per year). If your
application calls NameRes repeatedly for the same input strings, cache the results locally. Check the
`/status` endpoint to detect when the Babel version changes and invalidate your cache accordingly:

```
GET /status
```

The `babel_version` field in the response changes with each data release.
