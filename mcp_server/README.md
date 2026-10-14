# tenantwise-mcp

An MCP server that lets Claude Desktop and other MCP clients search Ontario residential tenancy law with section level citations. It calls the public TenantWise search API, holds no secrets, and makes no LLM calls of its own.

## Install in Claude Desktop

Add this to your Claude Desktop config file (Settings, Developer, Edit Config):

```json
{
  "mcpServers": {
    "tenantwise": {
      "command": "uvx",
      "args": ["tenantwise-mcp"]
    }
  }
}
```

Restart Claude Desktop, then ask: "Using TenantWise, how much notice does an Ontario landlord need for a rent increase?"

## Tools

`search_tenancy_law(query, k)` returns relevant passages with section references and official URLs.
`get_section(section_ref)` returns the full text of one section.

General legal information only, not legal advice.
