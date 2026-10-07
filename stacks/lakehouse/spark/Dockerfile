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
    && rm -rf /var/lib/apt/lists/*

# Make python3 resolve to Python 3.13
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 10 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.13 20 \
    && update-alternatives --set python3 /usr/bin/python3.13

ENV PYSPARK_PYTHON=/usr/bin/python3.13
ENV PYSPARK_DRIVER_PYTHON=/usr/bin/python3.13
ENV PYTHONPATH=/opt/spark/python/lib/pyspark.zip:/opt/spark/python/lib/py4j-0.10.9.9-src.zip

RUN python3 --version \
    && python3.13 --version

USER spark