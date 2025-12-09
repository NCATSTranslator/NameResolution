# NameRes Translator "Guppy" August 2024 Release
- Babel: [2024aug18](https://stars.renci.org/var/babel_outputs/2024aug18/)
  ([Babel Translator "Guppy" August 2024 Release](https://github.com/TranslatorSRI/Babel/blob/master/releases/TranslatorGuppyAugust2024.md))
- NameRes: [v1.4.3](https://github.com/TranslatorSRI/NameResolution/releases/tag/v1.4.3)
Next release: [Translator "Hammerhead" November 2024](./TranslatorHammerheadNovember2024.md)
Previous release: [Translator "Fugu" July 2024](./TranslatorFuguJuly2024.md)

## New features
* Added exactish index for synonyms ([#150](https://github.com/TranslatorSRI/NameResolution/pull/150))
* Added a Solr highlighter to report on matching labels and synonyms ([#156](https://github.com/TranslatorSRI/NameResolution/pull/156))
* Added support for multitype filtering ([#158](https://github.com/TranslatorSRI/NameResolution/pull/158))

## Babel updates (from [Babel Translator "Guppy" August 2024 Release](https://github.com/TranslatorSRI/Babel/blob/master/releases/TranslatorGuppyAugust2024.md))
* [Feature] Added support for generating DuckDB and Parquet files from the compendium and synonym files,
  allowing us to run queries such as looking for all the identically labeled cliques across
  all the compendia. Increased Babel Outputs file size to support DuckDB.
* [Feature] Added labels from DrugBank (https://github.com/TranslatorSRI/Babel/pull/335).
* [Feature] Improved cell anatomy concords using Wikidata (https://github.com/TranslatorSRI/Babel/pull/329).
* [Feature] Added manual concords for the DrugChemical conflation (https://github.com/TranslatorSRI/Babel/pull/337).
* [Feature] Wrote a script for comparing between two summary files (https://github.com/TranslatorSRI/Babel/pull/320).
* [Feature] Added timestamping as an option to Wget.
* [Feature] Reorganized primary label determination so that we can include it in compendia files as well.
  * This isn't currently used by the loader, but might be in the future. For now, this is only
    useful in helping track what labels are being chosen as the preferred label.
* [Bugfixes] Added additional ENSEMBL datasets to skip (https://github.com/TranslatorSRI/Babel/pull/297).
* [Bugfixes] Fixed a bug in recognizing the end of file when reading the PubChem ID and SMILES files.
* [Bugfixes] Fixed the lack of `clique_identifier_count` in leftover UMLS output.
* [Bugfixes] Fixed unraised exception in Ensembl BioMart download.
* [Bugfixes] Updated PubChem Compound download from FTP to HTTPS.
* [Bugfixes] Updated method for loading a prefix map.
* [Updates] Added additional Ubergraph IRI stem prefixes.
* [Updates] Changed DrugBank ID types from 'ChemicalEntity' to 'Drug'.

## Releases since [Translator "Fugu" July 2024](./TranslatorFuguJuly2024.md)
* [1.4.3](https://github.com/TranslatorSRI/NameResolution/releases/tag/v1.4.3)
  * Fix issue with empty Biolink type by @gaurav in [#159](https://github.com/TranslatorSRI/NameResolution/pull/159)
* [1.4.2](https://github.com/TranslatorSRI/NameResolution/releases/tag/v1.4.2)
  * Added support for multitype filtering by @gaurav in [#158](https://github.com/TranslatorSRI/NameResolution/pull/158)
* [1.4.1](https://github.com/TranslatorSRI/NameResolution/releases/tag/v1.4.1)
  * Added Translator Fugu release notes by @gaurav in [#155](https://github.com/TranslatorSRI/NameResolution/pull/155)
  * Use a Solr highlighter to identify matching terms by @gaurav in [#156](https://github.com/TranslatorSRI/NameResolution/pull/156)
* [1.4.0](https://github.com/TranslatorSRI/NameResolution/releases/tag/v1.4.0)
  * Add exactish index for synonyms by @gaurav in [#150](https://github.com/TranslatorSRI/NameResolution/pull/150)
