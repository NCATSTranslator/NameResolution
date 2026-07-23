#Given all-synonyms.json, construct a smaller set of synonyms that can be used in testing
import json

with open('all-synonyms.json','r') as inf:
    syns = json.load(inf)

alzheimer_curies = set([ x['curie'] for x in syns if 'alzheimer' in x['name'].lower()])
parkinson_curies = set([ x['curie'] for x in syns if 'parkinson' in x['name'].lower()])

allcuries = set()
allcuries.update(alzheimer_curies)
allcuries.update(parkinson_curies)

smallsyns = list(filter( lambda x: x['curie'] in allcuries , syns ))

# Write as JSON-lines (one JSON document per line), matching the format of the
# Babel synonym files that are loaded in production. The loader counts lines to
# verify every document made it into Solr, so the format must match.
with open('tests/data/test-synonyms.json','w') as outf:
    for syn in smallsyns:
        outf.write(json.dumps(syn))
        outf.write('\n')


