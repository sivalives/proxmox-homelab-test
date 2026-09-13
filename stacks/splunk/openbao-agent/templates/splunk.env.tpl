{{ with secret "secret/data/splunk" }}
SPLUNK_PASSWORD={{ .Data.data.SPLUNK_PASSWORD }}
{{ end }}