# Dockerfile for NameResolution data-loading
#
# A NameResolution worker is a SOLR instance that downloads its source file a SOLR database backup.
# (see ../Dockerfile and [1] for details). This Dockerfile is intended to create a SOLR instance that can load the
# data from a set of Babel synonym files (see https://github.com/NCATSTranslator/Babel), thus creating the SOLR backup
# which can be uploaded somewhere for the NameResolution workers.
#
# [1] https://github.com/helxplatform/translator-devops/blob/affcf34cf103230d25bdb859098d2a5ac81a49fb/helm/name-lookup/templates/scripts-config-map.yaml#L8-L105

# Use the RENCI Python image to make it easier to work with other
# RENCI Docker packages and to make sure we have an up to date image.
# (https://github.com/TranslatorSRI/RENCI-Python-image)
FROM ghcr.io/translatorsri/renci-python-image:3.11.5

# Configuration options:
# - ${ROOT} is where the source code will be copied.
ARG ROOT=/code/nameres-data-loading
# - ${SOLR_VERSION} is the SOLR version to install. This has to be an exact version
#   (it names a release tarball), and it is the floor for every Solr that serves the
#   resulting backup -- see "Restoring the backup" in README.md.
#   You can find the latest version at https://solr.apache.org/downloads.html
ARG SOLR_VERSION=9.10.0
# - ${SOLR_DIR} is the SOLR directory to use.
ARG SOLR_DIR=/var/solr

# Upgrade system files
RUN apt update
RUN apt -y upgrade

# Install or upgrade some prerequisite packages.
RUN apt install -y python3-venv
RUN pip3 install --upgrade pip

# Solr 9 needs Java 11 at a minimum, but is built and tested against 17; use 17.
RUN apt install -y openjdk-17-jre

# SOLR uses lsof to check on its status.
RUN apt install -y lsof

# pigz (parallel gzip) is used by the Makefile to compress the backup; gzip alone
# takes hours on a 130 GB index.
RUN apt install -y pigz

# The following packages are useful in debugging, but can be
# removed once this container is working properly.
RUN apt install -y htop
RUN apt install -y screen
RUN apt install -y vim
RUN apt install -y rsync

# Create a /var/solr directory for SOLR to use.
VOLUME ${SOLR_DIR}
RUN mkdir -p ${SOLR_DIR}
RUN chown nru:nru ${SOLR_DIR}

# Switch to nru user.
RUN mkdir -p ${ROOT}
RUN chown nru:nru ${ROOT}
WORKDIR ${ROOT}
USER nru

# Download Solr into the Solr directory.
RUN mkdir -p "${ROOT}/solr"
ADD --chown=nru:nru https://www.apache.org/dyn/closer.lua/solr/solr/${SOLR_VERSION}/solr-${SOLR_VERSION}.tgz?action=download "${ROOT}/solr"
RUN tar zxvf "${ROOT}/solr/solr-${SOLR_VERSION}.tgz" --directory "${ROOT}/solr"

# Set up VENV.
ENV VIRTUAL_ENV=${ROOT}/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install requirements from the lockfile.
COPY --chown=nru requirements.txt ${ROOT}
RUN pip3 install -r requirements.txt

# Copy necessary files. The configset carries the Solr schema/config; the core is
# created from it with `solr create -c name_lookup -d configsets/name_lookup`.
COPY --chown=nru setup-and-load-solr.sh ${ROOT}
COPY --chown=nru README.md ${ROOT}
COPY --chown=nru Makefile ${ROOT}
COPY --chown=nru configsets ${ROOT}/configsets

# On entry, start the Solr instance (standalone -- no ZooKeeper for a one-node load).
ENV SOLR_EXEC="${ROOT}/solr/solr-${SOLR_VERSION}/bin/solr"
ENV SOLR_DIR="$SOLR_DIR"
ENTRYPOINT ${SOLR_EXEC} start -f --user-managed -p 8983 -m 64G -s ${SOLR_DIR}
