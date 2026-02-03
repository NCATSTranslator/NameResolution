# Name Resolver

Name Resolver (Name Lookup or NameRes) takes lexical strings and attempts to map them to identifiers (CURIEs) from a vocabulary or ontology. An optional autocomplete mode (which assumes the query is incomplete) is available,
along with many other options. Given a preferred CURIE, the known synonyms of that CURIE can also be retrieved.
Multiple results may be returned representing possible conceptual matches, but all of the identifiers have been correctly normalized using the [Node Normalization](https://github.com/NCATSTranslator/NodeNormalization) service.

Note that the results returned by this service have been conflated using both GeneProtein and DrugChemical conflation; you can read more about this at the [Conflation documentation](https://github.com/NCATSTranslator/Babel/blob/master/docs/Conflation.md).

* See this [Jupyter Notebook](documentation/NameResolution.ipynb) for examples of use.
* See the [API documentation](documentation/API.md) for information about the NameRes API.
* See [Scoring](documentation/Scoring.md) for information about the scoring algorithm used by NameRes.
* See [Deployment](documentation/Deployment.md) for instructions on deploying NameRes.
