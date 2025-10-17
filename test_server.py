# test_server.py
import asyncio
import sys
import os

# Add current directory to path so we can import mcp_cook_server
sys.path.insert(0, os.path.dirname(__file__))

from mcp_cook_server import call_tool

async def test():
    print("Testing MCP server...")
    
    result = await call_tool(
        name="search_engineering_manual",
        arguments={"query": "Is Missouri a high wind zone?"}
    )
    
    print(f"\nGot {len(result)} items back")
    for item in result:
        if hasattr(item, 'text'):
            print(f"\nText: {item.text[:200]}...")
        if hasattr(item, 'data'):
            print(f"\nImage: {len(item.data)} bytes")

asyncio.run(test())