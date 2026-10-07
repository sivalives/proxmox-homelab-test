FROM spark:4.2.0-python3

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN wget -q https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
    -O /tmp/miniconda.sh \
    && bash /tmp/miniconda.sh -b -p /opt/conda \
    && rm -f /tmp/miniconda.sh

ENV PATH=/opt/conda/bin:$PATH

RUN conda install -y python=3.13 \
    && conda clean -afy

ENV PYSPARK_PYTHON=/opt/conda/bin/python

USER spark