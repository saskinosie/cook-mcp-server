"""
Streamlit UI for Cook Engineering Manual Search
Quick interface for client testing
"""
import streamlit as st
import requests
import json

# Configuration
API_URL = "https://cook-mcp-server-production.up.railway.app"

st.set_page_config(
    page_title="Cook Engineering Manual Search",
    page_icon="🔧",
    layout="wide"
)

st.title("🔧 Cook Engineering Manual Search")
st.markdown("Search technical specifications, formulas, charts, and guidelines from the Cook Engineering Handbook")

# Sidebar
with st.sidebar:
    st.header("About")
    st.markdown("""
    This tool searches the Cook Engineering Handbook for:
    - Technical specifications
    - Formulas and calculations
    - Charts and diagrams
    - HVAC system guidelines
    - Wind and seismic zone information
    """)

    st.divider()

    tool_choice = st.radio(
        "Search Type:",
        ["Search by Question", "Get Specific Page"],
        index=0
    )

# Main content
if tool_choice == "Search by Question":
    st.header("Search by Question")

    # Example queries
    with st.expander("💡 Example Questions"):
        st.markdown("""
        - What is the friction loss for round elbows?
        - Is Missouri a high wind zone?
        - What are the motor efficiency requirements?
        - What is the seismic zone for California?
        - How do I calculate duct sizing?
        """)

    # Query input
    query = st.text_area(
        "Enter your technical question:",
        height=100,
        placeholder="e.g., What is the friction loss for round elbows?"
    )

    if st.button("🔍 Search", type="primary", use_container_width=True):
        if not query:
            st.warning("Please enter a question")
        else:
            with st.spinner("Searching the engineering manual..."):
                try:
                    response = requests.post(
                        f"{API_URL}/tools/search_engineering_manual",
                        json={"query": query},
                        timeout=30
                    )

                    if response.status_code == 200:
                        result = response.json()
                        st.success("✅ Search complete!")

                        # Display result
                        st.markdown("### Answer:")
                        st.markdown(result.get("result", "No result returned"))

                    else:
                        st.error(f"Error: {response.status_code} - {response.text}")

                except requests.exceptions.Timeout:
                    st.error("Request timed out. Please try again.")
                except Exception as e:
                    st.error(f"Error: {str(e)}")

else:  # Get Specific Page
    st.header("Get Specific Page")

    st.markdown("Retrieve content from a specific page number (1-150)")

    page_number = st.number_input(
        "Page Number:",
        min_value=1,
        max_value=150,
        value=1,
        step=1
    )

    if st.button("📄 Get Page", type="primary", use_container_width=True):
        with st.spinner(f"Retrieving page {page_number}..."):
            try:
                response = requests.post(
                    f"{API_URL}/tools/get_page_direct",
                    json={"page_number": page_number},
                    timeout=30
                )

                if response.status_code == 200:
                    result = response.json()
                    st.success(f"✅ Page {page_number} retrieved!")

                    # Display result
                    st.markdown("### Content:")
                    st.markdown(result.get("result", "No content returned"))

                else:
                    st.error(f"Error: {response.status_code} - {response.text}")

            except requests.exceptions.Timeout:
                st.error("Request timed out. Please try again.")
            except Exception as e:
                st.error(f"Error: {str(e)}")

# Footer
st.divider()
st.markdown("""
<div style='text-align: center; color: gray; font-size: 0.8em;'>
    Powered by Cook Engineering Manual MCP | Deployed on Railway
</div>
""", unsafe_allow_html=True)