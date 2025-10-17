# mcp_cook_server.py
from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent
import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import weaviate
from weaviate.classes.query import Filter
from openai import OpenAI
import base64

# Ensure .env is loaded from the script's directory
script_dir = Path(__file__).parent
env_path = script_dir / ".env"
load_dotenv(env_path)

# Debug: Log to stderr so it appears in Claude Desktop logs
print(f"Loading .env from: {env_path}", file=sys.stderr)
print(f"WEAVIATE_URL exists: {bool(os.getenv('WEAVIATE_URL'))}", file=sys.stderr)

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

app = Server("cook-engineering-manual")

@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools for Claude."""
    return [
        Tool(
            name="search_engineering_manual",
            description="""Search the Cook Engineering Handbook for technical specifications, 
            formulas, charts, and guidelines. Use this for questions about fans, motors, 
            ductwork, HVAC systems, wind zones, seismic zones, etc. 
            
            Examples:
            - "What is the friction loss for round elbows?"
            - "Is Missouri a high wind zone?"
            - "What are the motor efficiency requirements?"
            
            This tool will automatically handle visual content like maps, charts, and diagrams.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The technical question or search query"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_page_direct",
            description="""Retrieve a specific page from the Cook Engineering Handbook by page number.
            Use this when you know the exact page you need or when search results reference a specific page.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "page_number": {
                        "type": "integer",
                        "description": "Page number (1-150)"
                    }
                },
                "required": ["page_number"]
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    """Handle tool calls from Claude."""
    
    if name == "search_engineering_manual":
        question = arguments["query"]

        # Ensure clients are connected
        weaviate_client, _ = await ensure_clients()

        # Step 1: Direct vector search (no QueryAgent)
        collection = weaviate_client.collections.get("Cook_Engineering_Manual")

        search_results = await collection.query.near_text(
            query=question,
            limit=5,
            return_properties=[
                "content", "section", "page", "content_type",
                "has_critical_visual", "visual_content", "visual_description"
            ]
        )

        full_objects = search_results.objects
        
        if not full_objects:
            return [TextContent(
                type="text",
                text="No relevant information found in the engineering manual for your query."
            )]
        
        # Step 2: Build context exactly like your working code
        text_context = []
        for obj in full_objects:
            props = obj.properties
            text_context.append(
                f"[{props['section']} - Page {props['page']}]\n{props['content'][:500]}..."
            )
        
        # Create a simple summary for "initial analysis"
        # (since we're not using QueryAgent, we'll create a basic summary)
        pages_found = [obj.properties['page'] for obj in full_objects]
        initial_summary = f"Found relevant information on pages: {', '.join(map(str, pages_found))}"
        
        # Step 3: Build message exactly like your working code
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
        
        # Step 4: Add images if present
        images_added = 0
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
                images_added += 1
        
        # Step 5: Call GPT-4V with exact same system prompt
        try:
            _, openai_client = await ensure_clients()
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
            
        except Exception as e:
            final_answer = f"Error calling vision model: {str(e)}\n\nPlease check your OpenAI API key and connection."
        
        return [TextContent(type="text", text=final_answer)]
    
    elif name == "get_page_direct":
        page_number = arguments["page_number"]

        # Ensure clients are connected
        weaviate_client, _ = await ensure_clients()
        collection = weaviate_client.collections.get("Cook_Engineering_Manual")

        response = await collection.query.fetch_objects(
            filters=Filter.by_property("page").equal(page_number),
            limit=10
        )
        
        if not response.objects:
            return [TextContent(
                type="text",
                text=f"No content found for page {page_number}. The manual contains pages 1-150."
            )]
        
        # Combine content from this page
        page_content = []
        
        for obj in response.objects:
            props = obj.properties
            page_content.append(f"[{props['section']}]\n\n{props['content']}")
        
        text_response = f"Content from Page {page_number}:\n\n" + "\n\n---\n\n".join(page_content)
        
        results = [TextContent(type="text", text=text_response)]
        
        # Add images if present
        for obj in response.objects:
            if obj.properties.get("has_critical_visual"):
                results.append(ImageContent(
                    type="image",
                    data=obj.properties["visual_content"],
                    mimeType="image/png"
                ))
        
        return results
    
    raise ValueError(f"Unknown tool: {name}")

async def main():
    """Run the MCP server."""
    from mcp.server.stdio import stdio_server
    
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )

if __name__ == "__main__":
    asyncio.run(main())