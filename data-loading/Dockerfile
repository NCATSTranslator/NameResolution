# Dockerfile for NameResolution data-loading
#
# A NameResolution worker is a SOLR instance that downloads its source file a SOLR database backup.
# (see ../Dockerfile and [1] for details). This Dockerfile is intended to create a SOLR instance that can load the
# data from a set of Babel synonym files (see https://github.com/NCATSTranslator/Babel), thus creating the SOLR backup
# which can be uploaded somewhere for the NameResolution workers.
#
# [1] https://github.com/helxplatform/translator-devops/blob/affcf34cf103230d25bdb859098d2a5ac81a49fb/helm/name-lookup/templates/scripts-config-map.yaml#L8-L105

# Solr is lifted out of the official Solr image rather than downloaded.
#
# It used to be fetched with `ADD https://www.apache.org/dyn/closer.lua/...`, which
# redirects to a randomly chosen Apache mirror. Mirrors vary from fast to barely
# moving, and ADD-from-a-URL has no timeout, no retry and no resume, so a bad draw
# hung the build until the job timed out. Nothing verified the download either.
#
# Pulling the official image instead removes all of that: it comes over a CDN, the
# daemon verifies it by digest, and it is cached like any other layer. It also means
# the Solr that builds a backup is bit-for-bit the Solr that serves it.
#
# Pinned exactly, on purpose: this version is the floor for every Solr that serves the
# resulting backup, while the servers track the 9.10 line and float up. See
# "Restoring the backup" in README.md. Latest at https://solr.apache.org/downloads.html
# --platform is pinned because the RENCI base image below is published for amd64 only,
# so that is the only architecture this image can be. Without it, a build on an arm64
# machine resolves this stage to arm64 (solr is multi-arch) while the final stage falls
# back to amd64, and the copied JDK is silently the wrong architecture: every java call
# then fails with "No such file or directory", which is the dynamic loader's unhelpful
# way of saying the ELF header is foreign.
ARG SOLR_VERSION=9.10.1
FROM --platform=linux/amd64 solr:${SOLR_VERSION} AS solr

# Use the RENCI Python image to make it easier to work with other
# RENCI Docker packages and to make sure we have an up to date image.
# (https://github.com/TranslatorSRI/RENCI-Python-image)
FROM ghcr.io/translatorsri/renci-python-image:3.11.5

# Configuration options:
# - ${ROOT} is where the source code will be copied.
ARG ROOT=/code/nameres-data-loading
# - ${SOLR_DIR} is the SOLR directory to use.
ARG SOLR_DIR=/var/solr

# Upgrade system files
RUN apt update
RUN apt -y upgrade

# Install or upgrade some prerequisite packages.
RUN apt install -y python3-venv
RUN pip3 install --upgrade pip

# SOLR uses lsof to check on its status. (It also needs ps, which the base image has.)
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

# Solr itself, and the JDK it ships with, taken straight from the official image.
# Owned by nru so `bin/solr` can write its own logs when SOLR_LOGS_DIR is not set
# (the Makefile does set it; the ENTRYPOINT below does not).
COPY --from=solr --chown=nru:nru /opt/solr /opt/solr
COPY --from=solr /opt/java /opt/java
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH="${JAVA_HOME}/bin:${PATH}"

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
COPY --chown=nru available-cpus.sh ${ROOT}
COPY --chown=nru README.md ${ROOT}
COPY --chown=nru Makefile ${ROOT}
COPY --chown=nru configsets ${ROOT}/configsets

# On entry, start the Solr instance (standalone -- no ZooKeeper for a one-node load).
#
# SOLR_MEM matches the Makefile's default, and for the same reason: `solr -m` sets
# both -Xms and -Xmx, and indexing wants the memory as page cache rather than heap
# (see the SOLR_MEM comment in the Makefile). Note that the Kubernetes pod replaces
# this command entirely and lets the Makefile start Solr, so this entrypoint only
# matters when the image is run directly.
ENV SOLR_EXEC="/opt/solr/bin/solr"
ENV SOLR_DIR="$SOLR_DIR"
ENV SOLR_MEM="31G"
ENTRYPOINT ${SOLR_EXEC} start -f --user-managed -p 8983 -m ${SOLR_MEM} -s ${SOLR_DIR}
