import docker
from mcp.server import MCPServer

mcp = MCPServer("docker-mcp")

client = docker.from_env()


@mcp.tool()
def docker_list_containers() -> list[dict]:
    """List all Docker containers and their current status."""
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
def docker_container_status(name: str) -> dict:
    """Get detailed status information for a Docker container."""
    container = client.containers.get(name)

    return {
        "name": container.name,
        "id": container.short_id,
        "status": container.status,
        "image": container.image.tags,
        "started_at": container.attrs["State"].get("StartedAt"),
    }


@mcp.tool()
def docker_container_logs(name: str, lines: int = 100) -> str:
    """Get recent logs from a Docker container."""

    if lines < 1 or lines > 1000:
        raise ValueError("lines must be between 1 and 1000")

    container = client.containers.get(name)

    logs = container.logs(
        tail=lines,
        timestamps=True
    )

    return logs.decode("utf-8", errors="replace")


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8000,
    )