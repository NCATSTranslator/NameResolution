#!/usr/bin/env bash
#
# Set up the fields and types needed by NameRes.
#
# This file should be sourced, not called directly.

# require sourcing
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  echo "Must be sourced: source $0" >&2
  exit 1
}

# require SOLR_SERVER
: "${SOLR_SERVER:?SOLR_SERVER must be set}"

echo "Setting up Solr database with SOLR_SERVER='$SOLR_SERVER'"

# add collection
curl -X POST "$SOLR_SERVER/solr/admin/collections?action=CREATE&name=name_lookup&numShards=1&replicationFactor=1"

# do not autocreate fields
curl "$SOLR_SERVER/solr/name_lookup/config" -d '{"set-user-property": {"update.autoCreateFields": "false"}}'

# add lowercase text type
curl -X POST -H 'Content-type:application/json' --data-binary '{
    "add-field-type" : {
        "name": "LowerTextField",
        "class": "solr.TextField",
        "positionIncrementGap": "100",
        "analyzer": {
            "tokenizer": {
                "class": "solr.StandardTokenizerFactory"
            },
            "filters": [{
                "class": "solr.LowerCaseFilterFactory"
            }]
        }
    }
}' "$SOLR_SERVER/solr/name_lookup/schema"

# add exactish text type (as described at https://stackoverflow.com/a/29105025/27310)
curl -X POST -H 'Content-type:application/json' --data-binary '{
    "add-field-type" : {
        "name": "exactish",
        "class": "solr.TextField",
        "positionIncrementGap": "100",
        "analyzer": {
            "tokenizer": {
                "class": "solr.KeywordTokenizerFactory"
            },
            "filters": [{
                "class": "solr.LowerCaseFilterFactory"
            }]
        }
    }
}' "$SOLR_SERVER/solr/name_lookup/schema"



# add fields
curl -X POST -H 'Content-type:application/json' --data-binary '{
    "add-field": [
        {
            "name":"names",
            "type":"LowerTextField",
            "indexed":true,
            "stored":true,
            "multiValued":true
        },
        {
            "name":"names_exactish",
            "type":"exactish",
            "indexed":true,
            "stored":false,
            "multiValued":true
        },
        {
            "name":"curie",
            "type":"string",
            "stored":true
        },
        {
            "name":"preferred_name",
            "type":"LowerTextField",
            "stored":true
        },
        {
            "name":"preferred_name_exactish",
            "type":"exactish",
            "indexed":true,
            "stored":false,
            "multiValued":false
        },
        {
            "name":"types",
            "type":"string",
            "stored":true
            "multiValued":true
        },
        {
            "name":"shortest_name_length",
            "type":"pint",
            "stored":true
    	  },
        {
            "name":"curie_suffix",
            "type":"plong",
            "docValues":true,
            "stored":true,
            "required":false,
            "sortMissingLast":true
        },
        {
            "name":"taxa",
            "type":"string",
            "stored":true,
            "multiValued":true
        },
        {
            "name":"taxon_specific",
            "type":"boolean",
            "stored":true,
            "multiValued":false,
            "sortMissingLast":true
        },
        {
            "name":"clique_identifier_count",
            "type":"pint",
            "stored":true
        }
    ] }' "$SOLR_SERVER/solr/name_lookup/schema"

# Add a copy field to copy names into names_exactish.
curl -X POST -H 'Content-type:application/json' --data-binary '{
    "add-copy-field": {
      "source": "names",
      "dest": "names_exactish"
    }
}' "$SOLR_SERVER/solr/name_lookup/schema"

# Add a copy field to copy preferred_name into preferred_name_exactish.
curl -X POST -H 'Content-type:application/json' --data-binary '{
    "add-copy-field": {
      "source": "preferred_name",
      "dest": "preferred_name_exactish"
    }
}' "$SOLR_SERVER/solr/name_lookup/schema"
