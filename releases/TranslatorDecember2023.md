# NameRes Translator December 2023 Release

- Babel: [2023nov5](https://stars.renci.org/var/babel_outputs/2023nov5/)
- NameRes: [v1.3.11](https://github.com/NCATSTranslator/NameResolution/releases/tag/v1.3.11)

Next release: [Translator May 2024](TranslatorMay2024.md)

## New features
* The `/lookup` endpoints now:
  * Have an `exclude_prefixes` field that allows prefixes to be excluded (e.g. `UMLS|NCI`).
  * Have an `autocomplete` flag. When set to true (the default), NameRes assumes that the search phrase is incomplete
    (i.e. a search for `bloo` is expanded to `(bloo) OR (bloo*)` so that we can match `blood`). If set to false, the
    search phrase is assumed to be complete (i.e. only `bloo` will be searched for), which should provide better results
    when NameRes is used as an named-entity linker by Translator components.
  * Report a score for every result.
* Fixed bugs in [GET `/reverse_lookup`](https://name-lookup.test.transltr.io/docs#/lookup/lookup_names_get_reverse_lookup_get)
  and [POST `/reverse_lookup`](https://name-lookup.test.transltr.io/docs#/lookup/lookup_names_post_reverse_lookup_post).
  Also changes the `/reverse_lookup` return format to include additional information, such as the preferred label and Biolink type.
* Added a [GET `/status`](https://name-lookup.test.transltr.io/docs#/default/status_get_status_get) endpoint to
  report on Solr database.
* OTEL/Jaegar telemetry added.
* Minor fixes to Terms of Service and service description.
* Minor fixes to NameRes database builder.

## Releases since [Translator October 2023 release](TranslatorOctober2023.md)

* https://github.com/NCATSTranslator/NameResolution/releases/tag/v1.3.7
  * fixes reverse lookup get endpoint. by @YaphetKG in #102
* https://github.com/NCATSTranslator/NameResolution/releases/tag/v1.3.8
  * Fix Terms of Service and service description by @gaurav in #114
  * Return entire document in reverse lookups by @gaurav in #115
  * Report scores for reverse lookup by @gaurav in #116
  * Add exclude_prefixes parameter to /lookup endpoints by @gaurav in #117
  * Add an autocomplete flag by @gaurav in #118
  * Added a /status endpoint that tells us about this NameRes instance by @gaurav in #119
  * Added instruction to make data/synonyms directory in target data/synonyms/done by @gaurav in #123
* https://github.com/NCATSTranslator/NameResolution/releases/tag/v1.3.9
  * Otel instrumentation by @YaphetKG in #121
* https://github.com/NCATSTranslator/NameResolution/releases/tag/v1.3.10
  * Update server.py by @YaphetKG in #130
* https://github.com/NCATSTranslator/NameResolution/releases/tag/v1.3.11
  * Incremented version that I forgot to do in Name Resolver v1.3.10.
