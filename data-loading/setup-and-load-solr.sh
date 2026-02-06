#!/usr/bin/env bash

SOLR_SERVER="http://localhost:8983"

is_solr_up(){
    echo "Checking if solr is up on http://$SOLR_SERVER/solr/admin/cores"
    http_code=`echo $(curl -s -o /dev/null -w "%{http_code}" "http://$SOLR_SERVER/solr/admin/cores")`
    echo $http_code
    return `test $http_code = "200"`
}

wait_for_solr(){
    while ! is_solr_up; do
        sleep 3
    done
}

wait_for_solr

source "setup_solr.sh"

# add data
for f in $1; do
	echo "Loading $f..."
	# curl -d @$f needs to load the entire file into memory before uploading it, whereas
	# curl -X POST -T $f will stream it. See https://github.com/TranslatorSRI/NameResolution/issues/194
	curl -H 'Content-Type: application/json' -X POST -T $f \
	    'http://$SOLR_SERVER/solr/name_lookup/update/json/docs?processor=uuid&uuid.fieldName=id&commit=true'
	sleep 30
done
echo "Check solr"
curl -s --negotiate -u: '$SOLR_SERVER/solr/name_lookup/query?q=*:*&rows=0'

