FROM quay.io/jupyter/pyspark-notebook:python-3.10

USER root

COPY requirements.txt /tmp/requirements.txt

RUN pip install --no-cache-dir -r /tmp/requirements.txt

USER ${NB_UID}