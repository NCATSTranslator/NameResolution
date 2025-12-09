# NameRes Translator "Fugu" July 2024 Release
- Babel: [2024jul13](https://stars.renci.org/var/babel_outputs/2024jul13/)
  ([Babel Translator July 2024 Release](https://github.com/TranslatorSRI/Babel/blob/master/releases/TranslatorFuguJuly2024.md))
- NameRes: [v1.3.14](https://github.com/TranslatorSRI/NameResolution/releases/tag/v1.3.14)

Next release: [Translator "Guppy" August 2024](./TranslatorGuppyAugust2024.md)

## New features
* None.

## Babel updates (from Babel Translator "Fugu" July 2024 Release)
* [Feature] Added manual disease concords, and used that to do a better job of combining opioid use disorder and alcohol use disorder. 
* [Feature] Moved ensembl_datasets_to_skip into the config file.
* [Bugfix] Eliminated preferred prefix overrides in Babel; we now trust the preferred prefixes from the Biolink Model.
* [Bugfix] DrugChemical conflation generation now removes CURIEs that can't be normalized.
* [Bugfix] Replaced http://nihilism.com/ with http://example.org/ as a base IRI.
* [Bugfix] Updated mappings from Reactome types to Biolink types.
* [Update] Updated Biolink from 4.1.6 to 4.2.1.
* [Update] Updated UMLS from 2023AB to 2024AA.
* [Update] Updated RxNorm from 03042024 to 07012024.
* [Update] Updated PANTHER_Sequence_Classification from PTHR18.0_human to PTHR19.0_human.
* [Update] Updated PANTHER pathways from SequenceAssociationPathway3.6.7.txt to SequenceAssociationPathway3.6.8.txt.

## Releases since [Translator May 2024](./TranslatorMay2024.md)
* [1.3.14](https://github.com/TranslatorSRI/NameResolution/releases/tag/v1.3.14): Forgot to increment version number in
  previous release.
* [1.3.13](https://github.com/TranslatorSRI/NameResolution/releases/tag/v1.3.13): Added release notes for Translator May 2024.