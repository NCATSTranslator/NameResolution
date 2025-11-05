# Name Resolution API

The Name Resolution API is intended to provide an [Apache Solr](https://solr.apache.org/)-based interface to the
[Babel](https://github.com/NCATSTranslator/Babel) cliques of equivalent identifiers. 

largely consists of three endpoints: `/lookup` (to search for normalized concepts),
`/bulk-lookup` (to search for multiple normalized concepts) and `/synonyms` (to look up for the synonyms for a normalized CURIE).

Unlike the Node Normalizer, the Name Resolution Service does not currently support on-the-fly conflation. Instead,
all the [Babel conflations](https://github.com/NCATSTranslator/Babel/blob/master/docs/Conflation.md) are turned on
when Solr database is built. This means that -- for example -- protein-encoding genes will include the synonyms found
for the protein they encode, and that no separate entry will be available for those proteins.

## Search endpoints

### `/lookup`

### `/bulk-lookup`

### Scoring

Every `/lookup` or `/bulk-lookup` search result returns a search score. This score value is calculated by Apache Solr,
and does not have an upper range. This score begins with the [TF-IDF](https://en.wikipedia.org/wiki/Tf%E2%80%93idf)
(term frequency-inverse document frequency) score, which is a measure of how relevant a term is to a document in a
collection of documents. The score is then multiplied by the [BM25](https://en.wikipedia.org/wiki/Okapi_BM25) score,
which is a measure of how relevant a document is to a query.


## Lookup endpoints

### `/synonyms`