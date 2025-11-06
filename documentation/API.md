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

Every `/lookup` or `/bulk-lookup` search result returns a search score. This score value is calculated by Apache Solr
and does not have an upper range. For every term in the query and every document in the result, Solr will calculate a
[TF*IDF score](https://en.wikipedia.org/wiki/Tf%E2%80%93idf) by multiplying:
* The term frequency: the relative frequency of the term in the document. Solr uses the equation `freq / (freq + k1 * (1 - b + b * dl / avgdl))`,
  where freq = number of occurrences of terms within this document, k1 = term saturation parameter, b = length normalization parameter,
  dl = length of field and avgdl = average length of field.
* The inverse document frequency: a measure of how rare this term is among all documents. Solr uses the equation
  `log(1 + (N - n + 0.5) / (n + 0.5))`, where N = total number of documents with this field, and n = number of documents
  containing the term.

If multiple terms are matched in the same document, the sum of the score for each term will be used.

The TF*IDF score will be multiplied by boosts that depend on three factors:
* We index two fields: the "preferred name" of every clique and the "synonyms" of every clique. The preferred name is
  chosen by Babel to 
* We index the "preferred name" of every clique with a boost of 10.
* We index the "synonyms" of every clique with a boost of 1.


This score begins with the [TF-IDF](https://en.wikipedia.org/wiki/Tf%E2%80%93idf)
(term frequency-inverse document frequency) score, which is a measure of how relevant a term is to a document in a
collection of documents. The score is then multiplied by the [BM25](https://en.wikipedia.org/wiki/Okapi_BM25) score,
which is a measure of how relevant a document is to a query.


## Lookup endpoints

### `/synonyms`