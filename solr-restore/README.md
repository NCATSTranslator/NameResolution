# solr-restore

This directory contains a script that can be used to restore a local Apache Solr backup to a Solr database
in Docker along with the indexes needed to query them from NameRes. It assumes that the backup is present
on the Solr server in the Solr data directory (by default `./data/solr`) and is
named `snapshot.backup.tar.gz`. If you follow the instructions in [the main README file](../README.md),
this script will be used automatically.

It is essentially the same script as is included in
[the name-lookup Helm chart](https://github.com/helxplatform/translator-devops/tree/develop/helm/name-lookup) 
of the `translator-devops` repository, but with some modifications allowing the script to be used
locally.
