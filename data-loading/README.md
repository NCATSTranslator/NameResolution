# Loading NameResolution data

NameResolution data needs to be loaded as a compressed [Apache Solr](https://solr.apache.org/) database.
To create this dataset is a three-step process.

1. Set up a Solr server locally. The easiest way to do this is via Docker:

   ```shell
   $ docker run -v "$PWD/data/solrdata:/var/solr" --name name_lookup -p 8983:8983 -t solr -cloud -p 8983 -m 12G
   ```
   
   You can adjust the `12G` to increase the amount of memory available to Solr. You can also add `-d` to the
   Docker arguments if you would like to run this node in the background.

2. Copy the synonym files into the `data/synonyms` directory. Synonym files that are too large will
   need to split it into smaller files. (`gsplit` is the GNU version of `split`, which includes support
   for adding an additional suffix to files)

   ```shell
   $ gsplit -l 5000000 -d --additional-suffix .txt SmallMolecule.txt SmallMolecule
   $ gsplit -l 5000000 -d --additional-suffix .txt MolecularMixture.txt MolecularMixture
   ```

3. Convert all the synonym text files into JSON document. To do this, you need to use the `csv2json.py` script
   included in this directory. By default, the Makefile expects the synonym files to be present in `data/synonyms`
   and writes out JSON files to `data/json`.

   ```shell
   $ pip install -r requirements.txt
   $ make
   ```

4. Load the JSON files into the Solr database by running:

   ```shell
   $ ./setup-and-load-solr.sh "data/json/*.json"
   ```
   
   Note the double-quotes: setup-and-load-solr.sh requires a glob pattern as its first argument, not a list of files to process!

5. Generate a backup of the Solr instance. The first command will create a directory at
   `solrdata/data/name_lookup_shard1_repical_n1/data/snapshot.backup` -- you can track its progress by comparing the
   number of files in that directory to the number of files in `../data/index` (as I write this, it has 513 files).

   ```shell
   $ curl 'http://localhost:8983/solr/name_lookup/replication?command=backup&name=backup'
   $ curl 'http://localhost:8983/solr/name_lookup/replication?command=details'
   ```
   
   Once the backup is complete, you'll see a part of the `details` response that looks like this:

   ```json
   "backup":{
      "startTime":"2022-09-13T18:42:43.678219123Z",
      "fileCount":512,
      "indexFileCount":512,
      "status":"success",
      "snapshotCompletedAt":"2022-09-13T19:36:00.599797304Z",
      "endTime":"2022-09-13T19:36:00.599797304Z",
      "snapshotName":"backup",
      "directoryName":"snapshot.backup"}
   }
   ```

6. Shutdown the Solr instance.

   ```shell
   $ docker exec name_lookup solr stop -p 8983 -verbose
   ```
   
7. Generate the backup tarball. At the moment, this is expected to be in the format
   `var/solr/data/snapshot.backup/[index files]`. The easiest way to generate this tarball correctly is to run:

   ```shell
   $ mkdir -p data/var/solr/data
   $ mv /var/solr/name_lookup_shard1_replica_n1/data/snapshot.backup data/var/solr/data
   $ cd data
   $ tar zcvf snapshot.backup.tar.gz var
   ```

8. Publish `snapshot.backup.tar.gz` to a publicly-accessible URL.

9. Use the instructions at https://github.com/helxplatform/translator-devops/tree/develop/helm/name-lookup to set up an
   instance of NameRes that downloads snapshot.backup.tar.gz from this publicly-accessible URL.

The Makefile included in this directory contains targets for more of these steps.
