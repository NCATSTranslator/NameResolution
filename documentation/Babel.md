# Where NameRes data comes from

NameRes does not decide what a concept is. It is a search interface over concepts built by
[Babel](https://github.com/NCATSTranslator/Babel), the pipeline that merges identifiers from
dozens of biomedical vocabularies into "cliques" of equivalent identifiers and collects every
synonym for each one.

The division of labour:

- **Babel decides** which identifiers belong together, which one leads the clique, what the
  preferred name is, which Biolink type it gets, and which synonyms it has.
- **NameRes decides** how those concepts are indexed, matched and ranked (see
  [Scoring](./Scoring.md)), and what the API looks like (see [API](./API.md)).

This document covers the Babel behaviour that is *visible through the NameRes API* — the things
that will otherwise look like NameRes bugs. It is also the only place in this repository that
links into the Babel repository, so that a reorganization there is a one-file fix here.

## What is actually loaded

NameRes is built from Babel's published `synonyms/` directory for one release
(`SYNONYMS_URL` in [`data-loading/Makefile`](../data-loading/Makefile)); the loader takes every
`*.txt`/`*.txt.gz` file in it. As of the `2025sep1` and `2026jul22` releases that is:

```text
AnatomicalEntity  BiologicalProcess  Cell  CellLine  CellularComponent  Disease
DrugChemicalConflated  GeneFamily  GeneProteinConflated  GrossAnatomicalStructure
MacromolecularComplex  MolecularActivity  OrganismTaxon  Pathway  PhenotypicFeature
Publication  umls
```

The set of searchable Biolink types is therefore Babel's decision, not NameRes's: if a Babel
release adds a synonym file, the next NameRes load picks it up with no change to this repository.

Two entries in that list are worth reading twice.

### `GeneProteinConflated` and `DrugChemicalConflated` replace the per-type files

There is no `Gene.txt`, `Protein.txt` or `SmallMolecule.txt` in the published directory — the
conflated files stand in for them. This is why NameRes results are conflated and why, unlike
NodeNorm, NameRes cannot turn conflation off on a per-query basis: it is baked into the index at
load time.

The practical consequence is that **there is no separate document to find for the conflated-away
member.** A protein is searchable, but the result you get back is identified by the gene that
encodes it and carries the union of both sets of synonyms; likewise a drug is identified by its
active ingredient. What each conflation merges, and in what order, is described in
[Babel's Conflation documentation](https://github.com/NCATSTranslator/Babel/blob/master/docs/Conflation.md).

Once you have an identifier from NameRes you can use
[NodeNorm](https://nodenormalization-sri.renci.org/) to see the clique with and without each
conflation. Use the NodeNorm instance that corresponds to the NameRes instance you are querying.

### `umls` is Babel's leftover-UMLS compendium

Babel's last pipeline step sweeps up every valid UMLS concept that no other compendium claimed and
writes each one out as a **single-identifier clique**. These are in your search results. They are
usually the reason for a result that has a `UMLS:` CURIE, a `clique_identifier_count` of 1, and a
broader Biolink type than you expected — Babel derives the type from the concept's UMLS semantic
type, falling back to `biolink:NamedThing` where UMLS asserts none.

This is deliberate coverage, not a defect: it means a UMLS concept still resolves to *something*
even when Babel could not merge it into a richer clique. See
[Babel's leftover-UMLS documentation](https://github.com/NCATSTranslator/Babel/blob/master/docs/sources/UMLS/Leftover.md).

## Gotchas in the returned fields

Every field NameRes returns from `/lookup` and `/synonyms` comes from Babel's
[synonym file format](https://github.com/NCATSTranslator/Babel/blob/master/docs/DataFormats.md).
Four of them behave in ways that surprise people.

### `label` is not necessarily the label of the CURIE you looked up

`label` is Babel's `preferred_name` for the whole clique, and Babel may deliberately choose a name
that is not the label of the clique leader — to disambiguate the concept, or to give the
Translator UI something better to display. Looking up a CURIE and getting back a different-looking
name is expected. How the preferred name is chosen (including the chemical-specific boost prefixes
and the length demotion) is in
[Understanding Babel outputs](https://github.com/NCATSTranslator/Babel/blob/master/docs/Understanding.md).

### `synonyms` is not ordered by quality

Babel writes `names` shortest-first — *except* for conflated cliques, where it writes all the
synonyms of the first clique, then all the synonyms of the second, and so on. Since almost
everything NameRes loads is conflated, `synonyms[0]` is not reliably the best or shortest name.
Use `label` when you want one name for a concept.

### `clique_identifier_count` measures coverage, not importance

NameRes multiplies each search score by the logarithm of this count (see
[Scoring](./Scoring.md)), on the theory that a widely cross-referenced concept is the one a user
more likely meant. What it literally counts is how many identifiers Babel merged into the clique,
which tracks how many source vocabularies happen to cover the concept. It is a good proxy in
practice, but it is why a leftover-UMLS singleton ranks below a heavily cross-referenced chemical
even when the two match the query equally well.

### `taxa` is a union, and an empty list means "nobody said"

Babel collects taxa per identifier and the clique's `taxa` is the union of those. An empty `taxa`
means no source asserted a taxon for any member, not that the concept is taxon-agnostic. This is
why `only_taxa` filtering keeps results that have *no* taxon alongside results with a matching
one — excluding them would drop concepts that are simply unannotated.

Note that "no taxon" looks different between the two endpoints: `/lookup` always returns a `taxa`
key and gives it an empty list, while `/synonyms` returns the raw Solr document, which simply omits
the field. Babel's own `taxon_specific` flag is carried through to `/synonyms` if you want the
distinction as a boolean.

### There are no descriptions

Babel collects descriptions (from [UberGraph](https://github.com/INCATools/ubergraph/)) and
NodeNorm can return them, but they are not carried into the synonym files and so are not in the
NameRes index. Use NodeNorm's `description` flag if you need them.

## The index is a snapshot of one Babel release

A NameRes instance serves a fixed build, not live Babel output. `/status` reports which one:

```json
{
  "babel_version": "2025sep1",
  "babel_version_url": "https://github.com/ncatstranslator/Babel/blob/master/releases/2025sep1.md",
  "biolink_model": { "tag": "v4.2.6-rc5" }
}
```

A concept that Babel has since fixed will still be wrong here until the instance is rebuilt and
redeployed. Check `/status` before reporting a stale clique, and see
[Babel's releases](https://github.com/NCATSTranslator/Babel/blob/master/releases/README.md) for
what changed between builds.

## Reporting a problem

Babel and NameRes have separate issue trackers, and the split is by *what is wrong*:

- **The concept itself is wrong** — wrong identifiers merged or not merged, wrong Biolink type,
  wrong preferred label, missing or bogus synonyms → the
  [Babel issue tracker](https://github.com/NCATSTranslator/Babel/issues).
- **The service is wrong** — errors, results ranked badly for a query, a parameter not behaving as
  documented → the
  [NameRes issue tracker](https://github.com/NCATSTranslator/NameResolution/issues).

If you are not sure, file it in Babel and it will be sorted out.
[Babel's guide to filing an issue](https://github.com/NCATSTranslator/Babel/blob/master/docs/NewIssue.md)
describes what to include and how the priority/impact/size fields are used.

## Going deeper

Everything above is what a NameRes user needs. If you want to know how the cliques are actually
built — the per-source ingestion, the concord files, the union-find merge, running the pipeline
yourself — start at [Babel's documentation index](https://github.com/NCATSTranslator/Babel/blob/master/docs/).
