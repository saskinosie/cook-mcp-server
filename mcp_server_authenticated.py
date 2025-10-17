"""
Authenticated MCP Server with Clerk OAuth
Exposes the Cook Engineering Manual MCP over HTTP with authentication
"""
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv
import httpx
from typing import Optional

# Import MCP components
from mcp.types import Tool, TextContent, ImageContent
import weaviate
from weaviate.classes.query import Filter
from openai import OpenAI

load_dotenv('.env.local')

# Initialize Weaviate and OpenAI
weaviate_client = weaviate.connect_to_weaviate_cloud(
    cluster_url=os.getenv("WEAVIATE_URL"),
    auth_credentials=weaviate.auth.Auth.api_key(os.getenv("WEAVIATE_API_KEY")),
    headers={
        "X-OpenAI-Api-Key": os.getenv("OPENAI_API_KEY"),
        "X-Cohere-Api-Key": os.getenv("COHERE_KEY")
    }
)

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# FastAPI app
app = FastAPI(title="Cook Engineering Manual MCP")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Clerk configuration
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY")
CLERK_PUBLISHABLE_KEY = os.getenv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY")

async def verify_clerk_session(session_token: str) -> dict:
    """Verify Clerk session token"""
    if not session_token:
        raise HTTPException(status_code=401, detail="No session token")

    async with httpx.AsyncClient() as client:
        try:
            # Extract instance domain from publishable key
            parts = CLERK_PUBLISHABLE_KEY.split("_")
            if len(parts) >= 3:
                instance = parts[2]
                clerk_domain = f"https://{instance}.clerk.accounts.dev"
            else:
                raise HTTPException(status_code=500, detail="Invalid Clerk configuration")

            response = await client.get(
                f"{clerk_domain}/v1/sessions/{session_token}",
                headers={"Authorization": f"Bearer {CLERK_SECRET_KEY}"}
            )

            if response.status_code != 200:
                raise HTTPException(status_code=401, detail="Invalid session token")

            return response.json()
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Auth failed: {str(e)}")

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "ok",
        "service": "Cook Engineering Manual MCP",
        "auth": "Clerk",
        "version": "1.0.0"
    }

@app.get("/health")
async def health_check():
    """Health check for Railway"""
    try:
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

@app.get("/tools")
async def list_tools(authorization: Optional[str] = Header(None)):
    """List available MCP tools (public for now, add auth later)"""
    return [
        {
            "name": "search_engineering_manual",
            "description": "Search the Cook Engineering Handbook for technical specifications, formulas, charts, and guidelines.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The technical question or search query"
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "get_page_direct",
            "description": "Retrieve a specific page from the Cook Engineering Handbook by page number.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page_number": {
                        "type": "integer",
                        "description": "Page number (1-150)"
                    }
                },
                "required": ["page_number"]
            }
        }
    ]

@app.post("/tools/search_engineering_manual")
async def search_tool(request: Request, authorization: Optional[str] = Header(None)):
    """Execute search tool"""
    body = await request.json()
    question = body.get("query")

    if not question:
        raise HTTPException(status_code=400, detail="Missing 'query' parameter")

    # Search Weaviate
    collection = weaviate_client.collections.get("Cook_Engineering_Manual")
    search_results = collection.query.near_text(
        query=question,
        limit=5,
        return_properties=[
            "content", "section", "page", "content_type",
            "has_critical_visual", "visual_content", "visual_description"
        ]
    )

    full_objects = search_results.objects

    if not full_objects:
        return {"result": "No relevant information found in the engineering manual for your query."}

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

Question: {question}

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

        final_answer = response.choices[0].message.content
        return {"result": final_answer}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/tools/get_page_direct")
async def get_page_tool(request: Request, authorization: Optional[str] = Header(None)):
    """Execute get page tool"""
    body = await request.json()
    page_number = body.get("page_number")

    if not page_number:
        raise HTTPException(status_code=400, detail="Missing 'page_number' parameter")

    collection = weaviate_client.collections.get("Cook_Engineering_Manual")
    response = collection.query.fetch_objects(
        filters=Filter.by_property("page").equal(page_number),
        limit=10
    )

    if not response.objects:
        return {"result": f"No content found for page {page_number}. The manual contains pages 1-150."}

    # Combine content
    page_content = []
    for obj in response.objects:
        props = obj.properties
        page_content.append(f"[{props['section']}]\n\n{props['content']}")

    result_text = f"Content from Page {page_number}:\n\n" + "\n\n---\n\n".join(page_content)
    return {"result": result_text}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)