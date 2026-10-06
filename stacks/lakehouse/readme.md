                         ┌── Prometheus
                         │
                         ├── Splunk
                         │
                         ▼
                       PySpark
                         │
                  ┌──────┴──────┐
                  │             │
                Bronze        Delta
                  │             │
                  ▼             ▼
                Silver        Gold
                                │
                         ┌──────┴──────┐
                         │             │
                      DuckDB        Trino
                         │             │
                         └──────┬──────┘
                                ▼
                           Dashboards


Later:

Docker/Pi-hole/App events
          ↓
        Kafka
          ↓
 Spark Structured Streaming
          ↓
       Delta Lake