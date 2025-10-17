# mcp_cook_server.py
from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent
import asyncio
import os
from dotenv import load_dotenv
import weaviate
from weaviate_agents.query import QueryAgent
from openai import OpenAI
import base64

load_dotenv()

# Initialize connections
weaviate_client = weaviate.connect_to_weaviate_cloud(
    cluster_url=os.getenv("WEAVIATE_URL"),
    auth_credentials=weaviate.auth.Auth.api_key(os.getenv("WEAVIATE_API_KEY")),
    headers={
        "X-OpenAI-Api-Key": os.getenv("OPENAI_API_KEY"),
        "X-Cohere-Api-Key": os.getenv("COHERE_KEY")
    }
)

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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
        query = arguments["query"]
        
        # Step 1: QueryAgent search
        qa = QueryAgent(
            client=weaviate_client,
            collections=["Cook_Engineering_Manual"]
        )
        qa_response = qa.ask(query)
        
        # Step 2: Fetch full objects with images
        collection = weaviate_client.collections.get("Cook_Engineering_Manual")
        full_objects = []
        
        for source in qa_response.sources:
            try:
                obj = collection.query.fetch_object_by_id(
                    source.object_id,
                    return_properties=[
                        "content", "section", "page", "content_type",
                        "has_critical_visual", "visual_content"
                    ]
                )
                full_objects.append(obj)
            except Exception as e:
                print(f"Error fetching object: {e}")
        
        # Step 3: Check if images present
        has_images = any(obj.properties.get("has_critical_visual") for obj in full_objects)
        
        if has_images:
            # Build context for vision model
            text_context = []
            for obj in full_objects:
                props = obj.properties
                text_context.append(
                    f"[{props['section']} - Page {props['page']}]\n{props['content'][:500]}..."
                )
            
            # Prepare message for vision model
            message_content = [
                {
                    "type": "text",
                    "text": f"""Question: {query}

Initial search results:
{qa_response.final_answer}

Additional context:
{chr(10).join(text_context)}

Please provide a comprehensive answer. Examine any images carefully for maps, charts, or diagrams."""
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
            vision_response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a technical assistant. Examine images carefully for specific information."
                    },
                    {
                        "role": "user",
                        "content": message_content
                    }
                ],
                max_tokens=1500
            )
            
            final_answer = vision_response.choices[0].message.content
            
            # Return text + images to Claude Desktop
            results = [TextContent(type="text", text=final_answer)]
            
            # Optionally include the images in response so Claude can see them
            for obj in full_objects:
                if obj.properties.get("has_critical_visual"):
                    results.append(ImageContent(
                        type="image",
                        data=obj.properties["visual_content"],
                        mimeType="image/png"
                    ))
            
            return results
        else:
            # No images, return text answer
            return [TextContent(type="text", text=qa_response.final_answer)]
    
    elif name == "get_page_direct":
        page_number = arguments["page_number"]
        
        collection = weaviate_client.collections.get("Cook_Engineering_Manual")
        
        # Query for objects on this page
        response = collection.query.fetch_objects(
            filters={"path": ["page"], "operator": "Equal", "valueInt": page_number},
            limit=5
        )
        
        if not response.objects:
            return [TextContent(type="text", text=f"No content found for page {page_number}")]
        
        # Combine content from this page
        page_content = []
        images = []
        
        for obj in response.objects:
            props = obj.properties
            page_content.append(f"[{props['section']}]\n{props['content']}")
            
            if props.get("has_critical_visual"):
                images.append(props["visual_content"])
        
        results = [TextContent(
            type="text",
            text=f"Page {page_number}:\n\n" + "\n\n---\n\n".join(page_content)
        )]
        
        # Add images
        for img in images:
            results.append(ImageContent(
                type="image",
                data=img,
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