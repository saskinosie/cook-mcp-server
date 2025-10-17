"""
MCP Server with SSE Transport for Remote Access
Implements the Model Context Protocol over HTTP with Server-Sent Events
"""
from mcp.server.fastmcp import FastMCP
import os
from dotenv import load_dotenv
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
    # Run with SSE transport on port 8080
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", 8080)))