# NameRes Translator "Hammerhead" November 2024 Release
- Babel: [2024oct24](https://stars.renci.org/var/babel_outputs/2024oct24/)
  ([Babel Translator November 2024 Release](https://github.com/TranslatorSRI/Babel/blob/master/releases/TranslatorHammerheadNovember2024.md))
- NameRes: [v1.4.5](https://github.com/TranslatorSRI/NameResolution/releases/tag/v1.4.5)
Next release: [NameRes v1.4.7](./v1.4.7.md)
Previous release: [Translator "Guppy" August 2024](./TranslatorGuppyAugust2024.md)

## New features
* Searching for an empty string now returns an empty list in [#167](https://github.com/TranslatorSRI/NameResolution/pull/167).

## Cleanup and improvements
* Add an MIT license for NameRes in [#169](https://github.com/TranslatorSRI/NameResolution/pull/169).
* Delete tests/data/test-synonyms.jsonl, which does not appear to be used in [#170](https://github.com/TranslatorSRI/NameResolution/pull/170).

## Babel updates (from [Babel Translator "Hammerhead" November 2024 Release](https://github.com/TranslatorSRI/Babel/blob/master/releases/TranslatorHammerheadNovember2024.md))
- [New features] Added taxon information to proteins ([#349](https://github.com/TranslatorSRI/Babel/pull/349))
- [Updates] Upgraded RxNorm to 09032024.
- [Updates] Changed NCBIGene download from FTP to HTTP.
- [Updates] Increased DRUG_CHEMICAL_SMALLER_MAX_LABEL_LENGTH (introduced in [#330](https://github.com/TranslatorSRI/Babel/pull/330)) from 30 to 40.

## Releases since [Translator "Guppy" August 2024](./TranslatorGuppyAugust2024.md)
* [1.4.5](https://github.com/TranslatorSRI/NameResolution/releases/tag/v1.4.5)
  * Searching for an empty string now returns an empty list in [#167](https://github.com/TranslatorSRI/NameResolution/pull/167).
  * Add an MIT license for NameRes in [#169](https://github.com/TranslatorSRI/NameResolution/pull/169).
  * Delete tests/data/test-synonyms.jsonl, which does not appear to be used in [#170](https://github.com/TranslatorSRI/NameResolution/pull/170).
* [1.4.4](https://github.com/TranslatorSRI/NameResolution/releases/tag/v1.4.4)
  * Added release notes for Translator Guppy in [#160](https://github.com/TranslatorSRI/NameResolution/pull/160).
