"""
Authenticated MCP Server with Clerk OAuth
Exposes the Cook Engineering Manual MCP over HTTP with authentication
"""
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv
import asyncio
from typing import Optional
import httpx
import json

# Import your existing MCP server logic
from mcp_cook_server import app as mcp_app, weaviate_client, openai_client
from mcp.types import Tool

load_dotenv('.env.local')

# FastAPI app
fastapi_app = FastAPI(title="Cook Engineering Manual MCP")

# CORS - allow Claude Desktop to connect
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify Claude's domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Clerk configuration
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY")
CLERK_PUBLISHABLE_KEY = os.getenv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY")

async def verify_clerk_token(authorization: str) -> dict:
    """Verify Clerk JWT token"""
    if not authorization:
        raise HTTPException(status_code=401, detail="No authorization header")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header format")

    token = authorization.replace("Bearer ", "")

    # Verify token with Clerk
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"https://api.clerk.com/v1/sessions/verify",
                headers={
                    "Authorization": f"Bearer {CLERK_SECRET_KEY}",
                    "Content-Type": "application/json",
                },
                params={"token": token}
            )

            if response.status_code != 200:
                raise HTTPException(status_code=401, detail="Invalid token")

            return response.json()
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Token verification failed: {str(e)}")

@fastapi_app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "ok",
        "service": "Cook Engineering Manual MCP",
        "auth": "Clerk OAuth",
        "version": "1.0.0"
    }

@fastapi_app.get("/.well-known/mcp")
async def mcp_metadata():
    """MCP server metadata for Claude Desktop discovery"""
    return {
        "name": "cook-engineering-manual",
        "version": "1.0.0",
        "description": "Search the Cook Engineering Handbook with authentication",
        "protocol": "mcp/1.0",
        "auth": {
            "type": "oauth2",
            "authorization_url": f"https://{CLERK_PUBLISHABLE_KEY.split('_')[2]}.clerk.accounts.dev/oauth/authorize",
            "token_url": f"https://{CLERK_PUBLISHABLE_KEY.split('_')[2]}.clerk.accounts.dev/oauth/token",
        }
    }

@fastapi_app.get("/tools", response_model=list)
async def list_tools(authorization: str = Header(None)):
    """List available MCP tools (authenticated)"""
    await verify_clerk_token(authorization)

    # Get tools from your MCP server
    tools = await mcp_app._tool_manager.list_tools()

    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.inputSchema
        }
        for tool in tools
    ]

@fastapi_app.post("/tools/{tool_name}")
async def call_tool(
    tool_name: str,
    request: Request,
    authorization: str = Header(None)
):
    """Execute an MCP tool (authenticated)"""
    user_data = await verify_clerk_token(authorization)

    # Get request body
    body = await request.json()
    arguments = body.get("arguments", {})

    # Add user context to arguments (optional - for usage tracking)
    arguments["_user_id"] = user_data.get("user_id")

    # Call your MCP tool
    try:
        results = await mcp_app._tool_manager.call_tool(tool_name, arguments)

        # Convert MCP response to JSON-serializable format
        response_data = []
        for result in results:
            if result.type == "text":
                response_data.append({
                    "type": "text",
                    "text": result.text
                })
            elif result.type == "image":
                response_data.append({
                    "type": "image",
                    "data": result.data,
                    "mimeType": result.mimeType
                })

        return JSONResponse(content={"results": response_data})

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tool execution failed: {str(e)}")

@fastapi_app.get("/health")
async def health_check():
    """Health check for Railway"""
    try:
        # Check Weaviate connection
        weaviate_client.is_ready()

        return {
            "status": "healthy",
            "weaviate": "connected",
            "openai": "configured" if openai_client.api_key else "missing"
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(fastapi_app, host="0.0.0.0", port=port)