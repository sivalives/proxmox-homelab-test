{{ with secret "secret/data/shared" }}
SPLUNK_HEC_URL={{ .Data.data.SPLUNK_HEC_URL }}
SPLUNK_HEC_TOKEN={{ .Data.data.SPLUNK_HEC_TOKEN }}
{{ end }}