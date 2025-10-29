"""
MCP Server with Clerk OAuth Authentication
Remote MCP server for Cook Engineering Manual with proper OAuth flow
"""
import os
import logging
from typing import Any
from dotenv import load_dotenv

# MCP imports
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings
from mcp.server.auth.provider import TokenVerifier, AccessToken
from pydantic import AnyHttpUrl
import httpx

# Weaviate and OpenAI
import weaviate
from weaviate.classes.query import Filter
from openai import OpenAI

# Load environment - try .env.local first, then .env
load_dotenv('.env.local')
load_dotenv()  # Fallback to .env if needed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Clerk configuration
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY")
CLERK_PUBLISHABLE_KEY = os.getenv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY")
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")

# Extract Clerk domain from publishable key
def get_clerk_domain():
    """Extract Clerk instance domain from publishable key"""
    if not CLERK_PUBLISHABLE_KEY:
        raise ValueError("CLERK_PUBLISHABLE_KEY not set")

    parts = CLERK_PUBLISHABLE_KEY.split("_")
    if len(parts) >= 3:
        instance = parts[2]
        return f"https://{instance}.clerk.accounts.dev"
    raise ValueError("Invalid CLERK_PUBLISHABLE_KEY format")

CLERK_DOMAIN = get_clerk_domain()

# Initialize Weaviate client
weaviate_client = weaviate.connect_to_weaviate_cloud(
    cluster_url=os.getenv("WEAVIATE_URL"),
    auth_credentials=weaviate.auth.Auth.api_key(os.getenv("WEAVIATE_API_KEY")),
    headers={
        "X-OpenAI-Api-Key": os.getenv("OPENAI_API_KEY"),
        "X-Cohere-Api-Key": os.getenv("COHERE_KEY", "")
    }
)

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


class ClerkTokenVerifier(TokenVerifier):
    """Verify Clerk JWT tokens"""

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify the JWT token with Clerk"""
        try:
            async with httpx.AsyncClient() as client:
                # Verify session with Clerk
                response = await client.get(
                    f"{CLERK_DOMAIN}/v1/sessions/verify",
                    headers={
                        "Authorization": f"Bearer {CLERK_SECRET_KEY}",
                        "Content-Type": "application/json"
                    },
                    params={"token": token}
                )

                if response.status_code == 200:
                    session_data = response.json()
                    user_id = session_data.get("user_id", "unknown")

                    logger.info(f"Token verified for user: {user_id}")

                    return AccessToken(
                        access_token=token,
                        token_type="Bearer",
                        scope=["user"],
                        user_id=user_id
                    )
                else:
                    logger.warning(f"Token verification failed: {response.status_code}")
                    return None

        except Exception as e:
            logger.error(f"Token verification error: {e}")
            return None


# Create MCP server with OAuth
mcp = FastMCP(
    "Cook Engineering Manual",
    token_verifier=ClerkTokenVerifier(),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(CLERK_DOMAIN),
        resource_server_url=AnyHttpUrl(SERVER_URL),
        required_scopes=["user"],
    )
)


@mcp.tool()
async def search_engineering_manual(query: str) -> str:
    """
    Search the Cook Engineering Handbook for technical specifications, formulas, charts, and guidelines.

    Args:
        query: The technical question or search query

    Returns:
        Comprehensive answer based on the manual content, including information from images and charts
    """
    logger.info(f"Searching for: {query}")

    try:
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
        logger.error(f"Search error: {e}")
        return f"Error searching manual: {str(e)}"


@mcp.tool()
async def get_page_direct(page_number: int) -> str:
    """
    Retrieve a specific page from the Cook Engineering Handbook by page number.

    Args:
        page_number: Page number (1-150)

    Returns:
        Complete content from the specified page
    """
    logger.info(f"Fetching page: {page_number}")

    try:
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

    except Exception as e:
        logger.error(f"Page fetch error: {e}")
        return f"Error fetching page: {str(e)}"


if __name__ == "__main__":
    # Run the MCP server
    import uvicorn

    # Get SSE app from FastMCP instance
    app = mcp.sse_app()

    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting MCP OAuth server on port {port}")
    logger.info(f"Clerk domain: {CLERK_DOMAIN}")
    logger.info(f"Server URL: {SERVER_URL}")

    uvicorn.run(app, host="0.0.0.0", port=port)