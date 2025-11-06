"""
MCP Server with SSE Transport for Remote Access
Implements the Model Context Protocol over HTTP with Server-Sent Events
"""
from mcp.server.fastmcp import FastMCP
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import weaviate
from weaviate.classes.query import Filter
from openai import OpenAI

# Load environment variables - try multiple paths for Railway deployment
env_paths = ['.env.local', '.env', Path(__file__).parent / '.env']
for env_path in env_paths:
    if load_dotenv(env_path):
        print(f"Loaded env from: {env_path}", file=sys.stderr)
        break

# Lazy initialization - clients created on first use
_weaviate_client = None
_openai_client = None

def get_weaviate_client():
    """Get or create Weaviate client."""
    global _weaviate_client
    if _weaviate_client is None:
        _weaviate_client = weaviate.connect_to_weaviate_cloud(
            cluster_url=os.getenv("WEAVIATE_URL"),
            auth_credentials=weaviate.auth.Auth.api_key(os.getenv("WEAVIATE_API_KEY")),
            headers={
                "X-OpenAI-Api-Key": os.getenv("OPENAI_API_KEY"),
                "X-Cohere-Api-Key": os.getenv("COHERE_KEY")
            }
        )
    return _weaviate_client

def get_openai_client():
    """Get or create OpenAI client."""
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _openai_client

# Create FastMCP server
mcp = FastMCP("Cook Engineering Manual")

@mcp.tool()
def search_engineering_manual(query: str) -> str:
    """
    Search the Cook Engineering Handbook for technical specifications,
    formulas, charts, and guidelines. Use this for questions about fans, motors,
    ductwork, HVAC systems, wind zones, seismic zones, etc.

    Args:
        query: The technical question or search query

    Returns:
        A comprehensive answer with relevant information from the manual
    """
    # Search Weaviate
    weaviate_client = get_weaviate_client()
    collection = weaviate_client.collections.get("Cook_Engineering_Manual")
    search_results = collection.query.near_text(
        query=query,
        limit=5,
        return_properties=[
            "content", "section", "page", "content_type",
            "has_critical_visual", "visual_content", "visual_description"
        ]
    )

    full_objects = search_results.objects

    if not full_objects:
        return "No relevant information found in the engineering manual for your query."

    # Build context
    text_context = []
    for obj in full_objects:
        props = obj.properties
        text_context.append(
            f"[{props['section']} - Page {props['page']}]\n{props['content'][:500]}..."
        )

    pages_found = [obj.properties['page'] for obj in full_objects]
    initial_summary = f"Found relevant information on pages: {', '.join(map(str, pages_found))}"

    # Build vision message
    message_content = [
        {
            "type": "text",
            "text": f"""You are a technical assistant helping with engineering specifications.

Question: {query}

Initial analysis from search system:
{initial_summary}

Additional context from relevant sections:
{chr(10).join(text_context)}

Please provide a comprehensive answer. If images are provided, carefully examine them for specific information like maps, charts, or diagrams that may contain data not in the text."""
        }
    ]

    # Add images
    for obj in full_objects:
        if obj.properties.get("has_critical_visual"):
            img_base64 = obj.properties["visual_content"]
            message_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img_base64}",
                    "detail": "high"
                }
            })

    # Call GPT-4V
    try:
        openai_client = get_openai_client()
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are a technical assistant. When images are provided, examine them carefully for specific information like locations on maps, values in charts, or specifications in tables."
                },
                {
                    "role": "user",
                    "content": message_content
                }
            ],
            max_tokens=1500
        )

        return response.choices[0].message.content

    except Exception as e:
        return f"Error calling vision model: {str(e)}"

@mcp.tool()
def get_page_direct(page_number: int) -> str:
    """
    Retrieve a specific page from the Cook Engineering Handbook by page number.
    Use this when you know the exact page you need or when search results reference a specific page.

    Args:
        page_number: Page number (1-150)

    Returns:
        The content from the specified page
    """
    weaviate_client = get_weaviate_client()
    collection = weaviate_client.collections.get("Cook_Engineering_Manual")
    response = collection.query.fetch_objects(
        filters=Filter.by_property("page").equal(page_number),
        limit=10
    )

    if not response.objects:
        return f"No content found for page {page_number}. The manual contains pages 1-150."

    # Combine content
    page_content = []
    for obj in response.objects:
        props = obj.properties
        page_content.append(f"[{props['section']}]\n\n{props['content']}")

    return f"Content from Page {page_number}:\n\n" + "\n\n---\n\n".join(page_content)

if __name__ == "__main__":
    # Run with SSE transport
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    # Create FastAPI app
    app = FastAPI(title="Cook Engineering Manual MCP Server")

    # Health check endpoint
    @app.get("/")
    @app.get("/health")
    async def health():
        try:
            # Test connections
            wc = get_weaviate_client()
            oc = get_openai_client()
            return JSONResponse({
                "status": "healthy",
                "weaviate": "connected",
                "openai": "configured"
            })
        except Exception as e:
            return JSONResponse({
                "status": "unhealthy",
                "error": str(e)
            }, status_code=503)

    # REST API endpoints for Streamlit
    @app.post("/tools/search_engineering_manual")
    async def api_search(request: Request):
        try:
            data = await request.json()
            query = data.get("query", "")
            result = search_engineering_manual(query)
            return JSONResponse({"result": result})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/tools/get_page_direct")
    async def api_get_page(request: Request):
        try:
            data = await request.json()
            page_number = data.get("page_number", 1)
            result = get_page_direct(page_number)
            return JSONResponse({"result": result})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # Mount MCP SSE endpoint
    app.mount("/mcp", mcp.get_asgi_app())

    # Run with uvicorn
    port = int(os.getenv("PORT", 8080))
    print(f"Starting server on port {port}", file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=port)