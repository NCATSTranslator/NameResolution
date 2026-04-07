# Scoring

Every `/lookup` or `/bulk-lookup` search result (see [API](./API.md)) returns a search score. This score value is calculated by Apache Solr
and does not have an upper range. For every term in the query and every document in the result, Solr will calculate a
[TF*IDF score](https://en.wikipedia.org/wiki/Tf%E2%80%93idf) by multiplying:
* The term frequency: the relative frequency of the term in the document. Solr uses the equation `freq / (freq + k1 * (1 - b + b * dl / avgdl))`,
  where freq = number of occurrences of terms within this document, k1 = term saturation parameter, b = length normalization parameter,
  dl = length of field and avgdl = average length of field.
* The inverse document frequency: a measure of how rare this term is among all documents. Solr uses the equation
  `log(1 + (N - n + 0.5) / (n + 0.5))`, where N = total number of documents with this field, and n = number of documents
  containing the term.

If multiple terms are matched in the same document, the sum of the score for each term will be used.

The TF*IDF score will be multiplied by [several boosts](https://github.com/NCATSTranslator/NameResolution/blob/56e2151bb9e6fd120644cebdf4ff45b3bc47da05/api/server.py#L436-L461)
that depend on four factors:
* We index two fields: the "preferred name" of every clique and the "synonyms" of every clique. The [preferred name
  is chosen by Babel](https://github.com/NCATSTranslator/Babel?tab=readme-ov-file#how-does-babel-choose-a-preferred-label-for-a-clique),
  while the synonyms are collected from all the different Babel sources.
* We set up two indexes: a [StandardTokenizer](https://solr.apache.org/guide/solr/latest/indexing-guide/tokenizers.html#standard-tokenizer)
  that splits the field into tokens at whitespace and punctuation characters, and a
  [KeywordTokenizer](https://solr.apache.org/guide/solr/latest/indexing-guide/tokenizers.html#keyword-tokenizer) that
  treats the entire field as a single token.
* We use the [Query Fields (qf)](https://solr.apache.org/guide/solr/latest/query-guide/dismax-query-parser.html#qf-query-fields-parameter)
  field to search for the tokens in the index, but we also use the [Phrase Fields (pf)](https://solr.apache.org/guide/solr/latest/query-guide/edismax-query-parser.html#extended-dismax-parameters)
  field to additionally boost search results where all the tokens are found in close proximity.
  (NOTE: this might be removed soon.)
* We use the number of identifiers in the clique as a measure of how widely used a clique is. Since some cliques
  share the same preferred name or label, we can use this to promote the clique most likely to be useful.

We combine these factors in this way in a standard query matches:

|                          | Preferred name match | Synonym match |
|--------------------------|----------------------|---------------|
| Keyword Tokenizer index  | 250x                 | 100x          |
| StandardTokenizer index  | 25x                  | 10x           |

And provide additional boosts for phrase matches, boosting synonym matches more than preferred name matches:

|                          | Preferred name match | Synonym match |
|--------------------------|----------------------|---------------|
| Keyword Tokenizer index  | 300x                 | 200x          |
| StandardTokenizer index  | 30x                  | 20x           |

Finally, we multiply the total score by the (base 10) logarithm by the number of identifiers in the clique plus one.
This boost ranges from log(2) = 0.3 for a clique that only has a single identifier to over log(1000) = 3.
