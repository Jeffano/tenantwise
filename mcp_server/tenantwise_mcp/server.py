"""TenantWise MCP server.

Design decision: this is a thin client over the public TenantWise search API.
* It holds no secrets and no database credentials, so it is safe to publish on PyPI.
* It never calls an LLM. The user's own MCP client (Claude Desktop, etc.) does the
  reasoning, so every MCP user costs the project essentially nothing.
* It reuses the exact retrieval pipeline the web app uses, so quality is identical.
"""
import argparse
import os

import httpx
from mcp.server.mcpserver import MCPServer

API_URL = os.environ.get("TENANTWISE_API_URL", "https://api.tenantwise.jeffanojohn.com").rstrip("/")

mcp = MCPServer(
    name="tenantwise",
    instructions=(
        "Search Ontario residential tenancy law (Residential Tenancies Act, O. Reg. 516/06, and official "
        "Ontario guides). Always cite the section_ref and url of results you rely on. This is general "
        "legal information, not legal advice; point users to the Landlord and Tenant Board for their case."
    ),
)


def _get(path: str, params: dict) -> dict:
    r = httpx.get(f"{API_URL}{path}", params=params, timeout=30,
                  headers={"User-Agent": "tenantwise-mcp/0.1"})
    r.raise_for_status()
    return r.json()


@mcp.tool()
def search_tenancy_law(query: str, k: int = 6) -> dict:
    """Search Ontario residential tenancy law. Returns the most relevant passages, each with a
    section reference (for example "RTA s. 116"), a heading path, the official URL, and the text.
    Use for questions about rent increases, repairs, evictions, deposits, entry, and LTB forms."""
    data = _get("/api/search", {"q": query, "k": max(1, min(k, 10))})
    if not data.get("in_scope", True):
        data["note"] = "Nothing closely matched. The question may be outside Ontario residential tenancy law."
    return data


@mcp.tool()
def get_section(section_ref: str) -> dict:
    """Get the full text of one section by its reference exactly as returned by
    search_tenancy_law, for example "RTA s. 48.1"."""
    return _get("/api/section", {"ref": section_ref})


def main() -> None:
    parser = argparse.ArgumentParser(description="TenantWise MCP server")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.http:
        mcp.run(transport="streamable-http", host="0.0.0.0", port=args.port)
    else:
        mcp.run()  # stdio: what Claude Desktop and most local clients use


if __name__ == "__main__":
    main()
