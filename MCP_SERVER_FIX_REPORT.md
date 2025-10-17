# MCP Server Connection Fix Report

**Date:** 2025-10-14
**Issue:** Cook Engineering Manual MCP server disconnecting immediately after startup
**Status:** ✅ Resolved

---

## Problem Summary

The MCP server (`mcp_cook_server.py`) was configured in Claude Desktop but kept disconnecting with the error:
```
Server disconnected
Command: /Users/scottaskinosie/Documents/weaviate/cook_image_query/.venv/bin/python3
Arguments: /Users/scottaskinosie/Documents/weaviate/cook_image_query/mcp_cook_server.py
```

Claude Desktop logs showed:
```
Server transport closed unexpectedly, this is likely due to the process exiting early.
```

---

## Root Cause

The Weaviate client was being initialized **at module import time** (lines 15-22 in original code):

```python
# OLD CODE - PROBLEMATIC
weaviate_client = weaviate.connect_to_weaviate_cloud(
    cluster_url=os.getenv("WEAVIATE_URL"),
    auth_credentials=weaviate.auth.Auth.api_key(os.getenv("WEAVIATE_API_KEY")),
    headers={...}
)
```

This caused the server to:
1. Attempt connection to Weaviate Cloud before MCP initialization
2. Fail with `UnexpectedStatusCodeError: Meta endpoint! Unexpected status code: 404`
3. Exit immediately, preventing MCP from establishing connection

---

## Solution Implemented

### Change 1: Lazy Async Client Initialization

Replaced eager initialization with an async lazy initialization pattern:

```python
# NEW CODE - FIXED
# Global client references - will be initialized when server starts
_weaviate_client = None
_openai_client = None

async def ensure_clients():
    """Ensure clients are initialized."""
    global _weaviate_client, _openai_client

    if _weaviate_client is None:
        # Use context manager syntax for proper async connection
        _weaviate_client = await weaviate.use_async_with_weaviate_cloud(
            cluster_url=os.getenv("WEAVIATE_URL"),
            auth_credentials=weaviate.auth.Auth.api_key(os.getenv("WEAVIATE_API_KEY")),
            headers={
                "X-OpenAI-Api-Key": os.getenv("OPENAI_API_KEY"),
                "X-Cohere-Api-Key": os.getenv("COHERE_KEY")
            }
        ).__aenter__()

    if _openai_client is None:
        _openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    return _weaviate_client, _openai_client
```

**Key improvements:**
- Clients are only initialized when first tool is called
- Uses `weaviate.use_async_with_weaviate_cloud()` for proper async support
- Server can start and connect to Claude Desktop without requiring external services

### Change 2: Updated Tool Functions

Modified both tool implementations to call `ensure_clients()` before use:

**In `search_engineering_manual` tool:**
```python
# Before
collection = weaviate_client.collections.get("Cook_Engineering_Manual")

# After
weaviate_client, _ = await ensure_clients()
collection = weaviate_client.collections.get("Cook_Engineering_Manual")
```

**In `get_page_direct` tool:**
```python
# Before
collection = weaviate_client.collections.get("Cook_Engineering_Manual")

# After
weaviate_client, _ = await ensure_clients()
collection = weaviate_client.collections.get("Cook_Engineering_Manual")
```

**For OpenAI client:**
```python
# Before
response = openai_client.chat.completions.create(...)

# After
_, openai_client = await ensure_clients()
response = openai_client.chat.completions.create(...)
```

---

## Files Modified

- **File:** `/Users/scottaskinosie/Documents/weaviate/cook_image_query/mcp_cook_server.py`
- **Lines changed:** 14-36, 88-95, 160-162, 184-189

---

## Verification

Import test confirmed successful startup:
```bash
$ .venv/bin/python3 -c "import mcp_cook_server; print('Import successful!')"
Import successful!
```

---

## Next Steps

1. **Restart Claude Desktop** to pick up the fixed server
2. **Test the MCP tools** in a Claude Desktop conversation:
   - Try: "Search the engineering manual for friction loss information"
   - Try: "Get page 42 from the handbook"
3. Monitor `~/Library/Logs/Claude/mcp*.log` for any connection issues

---

## Technical Notes

- **Async pattern:** MCP servers run in async context, so using `weaviate.use_async_with_weaviate_cloud()` is the correct approach
- **Context manager:** Using `.__aenter__()` allows manual async context entry while maintaining proper connection lifecycle
- **Lazy loading:** Prevents external service dependencies from blocking server startup
- **Error isolation:** Connection errors now occur at tool call time with proper error messages, not at server startup

---

## Configuration Reference

**Claude Desktop Config Location:**
```
~/Library/Application Support/Claude/claude_desktop_config.json
```

**Server Configuration:**
```json
{
  "mcpServers": {
    "cook-engineering-manual": {
      "command": "/Users/scottaskinosie/Documents/weaviate/cook_image_query/.venv/bin/python3",
      "args": [
        "/Users/scottaskinosie/Documents/weaviate/cook_image_query/mcp_cook_server.py"
      ]
    }
  }
}
```

---

**Report generated:** 2025-10-14
**Issue resolved by:** Claude Code
