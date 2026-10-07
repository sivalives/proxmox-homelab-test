FROM spark:4.2.0-python3

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       software-properties-common \
       ca-certificates \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       python3.13 \
       python3.13-venv \
       python3.13-distutils \
    && rm -rf /var/lib/apt/lists/*

RUN python3.13 --version

ENV PYSPARK_PYTHON=/usr/bin/python3.13

USER spark