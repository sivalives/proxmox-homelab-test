import docker
from docker.errors import DockerException, NotFound
from docker.tls import TLSConfig
from mcp.server import MCPServer

mcp = MCPServer("docker-mcp")


class DockerHostManager:
    """Manage Docker connections to multiple hosts."""

    def __init__(self):
        self.clients = {
            "ai": docker.from_env(),
            "docker-main": self._create_remote_client(),
        }

    def _create_remote_client(self):
        tls = TLSConfig(
            client_cert=(
                "/app/certs/cert.pem",
                "/app/certs/key.pem",
            ),
            ca_cert="/app/certs/ca.pem",
            verify=True,
        )

        return docker.DockerClient(
            base_url="https://192.168.1.106:2376",
            tls=tls,
        )

    def get_client(self, host: str):
        if host not in self.clients:
            raise ValueError(
                f"Unknown Docker host '{host}'. "
                f"Available hosts: {', '.join(self.clients.keys())}"
            )
        return self.clients[host]

    def hosts(self) -> list[str]:
        return list(self.clients.keys())


docker_hosts = DockerHostManager()


@mcp.tool()
def docker_list_hosts() -> list[str]:
    """List all Docker hosts available to this MCP server."""
    return docker_hosts.hosts()


@mcp.tool()
def docker_list_containers(host: str = "ai") -> list[dict]:
    """List all Docker containers on a Docker host."""

    client = docker_hosts.get_client(host)

    containers = client.containers.list(all=True)

    return [
        {
            "name": container.name,
            "id": container.short_id,
            "image": container.image.tags,
            "status": container.status,
        }
        for container in containers
    ]


@mcp.tool()
def docker_container_status(
    name: str,
    host: str = "ai",
) -> dict:
    """Get detailed status information for a Docker container."""

    client = docker_hosts.get_client(host)

    try:
        container = client.containers.get(name)
    except NotFound:
        raise ValueError(
            f"Container '{name}' not found on Docker host '{host}'."
        )

    state = container.attrs.get("State", {})

    return {
        "host": host,
        "name": container.name,
        "id": container.short_id,
        "status": container.status,
        "image": container.image.tags,
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
        "exit_code": state.get("ExitCode"),
    }


@mcp.tool()
def docker_container_logs(
    name: str,
    host: str = "ai",
    lines: int = 100,
) -> str:
    """Get recent logs from a Docker container."""

    if lines < 1 or lines > 1000:
        raise ValueError("lines must be between 1 and 1000")

    client = docker_hosts.get_client(host)

    try:
        container = client.containers.get(name)
    except NotFound:
        raise ValueError(
            f"Container '{name}' not found on Docker host '{host}'."
        )

    logs = container.logs(
        tail=lines,
        timestamps=True,
    )

    return logs.decode("utf-8", errors="replace")


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8000,
    )