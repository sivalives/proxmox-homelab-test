# config/openbao.hcl
ui = true

api_addr = "http://192.168.1.101:8200"
cluster_addr = "https://192.168.1.101:8201"

listener "tcp" {
  address           = "0.0.0.0:8200"
  cluster_address   = "0.0.0.0:8201"
  tls_disable       = true
}

storage "raft" {
  # Should match docker-compose mount: ./data:/openbao/data
  path    = "/openbao/data"
  node_id = "openbao-lxc101"
}