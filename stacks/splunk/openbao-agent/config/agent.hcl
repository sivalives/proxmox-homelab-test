pid_file = "/tmp/openbao-agent.pid"

vault {
  address = "http://192.168.1.106:8200"
}

auto_auth {
  method "approle" {
    mount_path = "auth/approle"

    config = {
      role_id_file_path   = "/openbao-agent/bootstrap/role-id"
      secret_id_file_path = "/openbao-agent/bootstrap/secret-id"
    }
  }

  sink "file" {
    config = {
      path = "/openbao-agent/rendered/token"
    }
  }
}

template {
  source      = "/openbao-agent/templates/splunk.env.tpl"
  destination = "/openbao-agent/rendered/splunk.env"
}

template {
  source      = "/openbao-agent/templates/shared.env.tpl"
  destination = "/openbao-agent/rendered/shared.env"
}