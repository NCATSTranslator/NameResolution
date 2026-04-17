# Name Resolver

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.18488923.svg)](https://doi.org/10.5281/zenodo.18488923) [![arXiv](https://img.shields.io/badge/arXiv-2601.10008-b31b1b.svg)](https://arxiv.org/abs/2601.10008)

Name Resolver (Name Lookup or NameRes) takes lexical strings and attempts to map them to identifiers (CURIEs) from a vocabulary or ontology. An optional autocomplete mode (which assumes the query is incomplete) is available,
along with many other options. Given a preferred CURIE, the known synonyms of that CURIE can also be retrieved.
Multiple results may be returned representing possible conceptual matches, but all of the identifiers have been correctly normalized using the [Node Normalization](https://github.com/NCATSTranslator/NodeNormalization) service.

The concepts NameRes searches are built by the [Babel](https://github.com/NCATSTranslator/Babel)
pipeline. Note that the results returned by this service have been conflated using both GeneProtein
and DrugChemical conflation; you can read more about this, and about the other Babel behaviour
visible through this API, at [Where NameRes data comes from](documentation/Babel.md).

## Getting started

The best place to start is the Jupyter Notebook, which walks through the most common use cases with live examples:

* [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/NCATSTranslator/NameResolution/blob/main/documentation/NameResolution.ipynb) [Jupyter Notebook](documentation/NameResolution.ipynb) — interactive examples covering lookup, filtering, autocomplete, bulk lookup, and synonyms

## Documentation

* [Translator Guide](documentation/TranslatorGuide.md) — what to do when results are unexpected, when to use `/synonyms` vs. NodeNorm, and performance tips
* [Using NameRes from an AI agent](documentation/LLMs.md) — the agent skill, how to install it, and how to fetch it from a running instance at `/llms.txt`
* [API documentation](documentation/API.md) — full reference for all NameRes endpoints
* [Where NameRes data comes from](documentation/Babel.md) — how Babel builds the concepts NameRes searches, and the gotchas that follow from that
* [Scoring](documentation/Scoring.md) — how NameRes scores and ranks results
* [Deployment](documentation/Deployment.md) — instructions for deploying NameRes

## Reporting a problem

If a *concept* is wrong -- identifiers merged that shouldn't be, a wrong Biolink type or preferred
label, missing synonyms -- report it to [Babel](https://github.com/NCATSTranslator/Babel/issues),
which builds the concepts. If the *service* is wrong -- errors, badly ranked results, a parameter
not behaving as documented -- report it
[here](https://github.com/NCATSTranslator/NameResolution/issues). If you're not sure, file it in
Babel. See [Where NameRes data comes from](documentation/Babel.md#reporting-a-problem) for details.
