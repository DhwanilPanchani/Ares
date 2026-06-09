"""
Tool: web_search

Searches the web using DuckDuckGo (no API key required) and returns the top 5
results formatted as a human-readable string.

LangChain uses the docstring as the tool description sent to the model,
so it must be clear and actionable.
"""

from __future__ import annotations

from ddgs import DDGS
from langchain_core.tools import tool


@tool
def web_search(query: str) -> str:
    """
    Search the web for up-to-date information on any topic.

    Use this tool when you need current facts, recent events, or any
    information that is not in your training data. Returns the top 5
    search results with their titles, URLs, and descriptions.

    Args:
        query: A clear search query string (e.g. "Python asyncio tutorial 2024").

    Returns:
        Formatted string of search results, or an error message.
    """
    try:
        results = DDGS().text(query, max_results=5)
        if not results:
            short_query = " ".join(query.split()[:4])
            if short_query and short_query != query:
                results = DDGS().text(short_query, max_results=5)
    except Exception as e:
        return f"Search error: {str(e)}"

    if not results:
        return f"No results found for: {query}"

    return "\n---\n".join(
        f"Title: {r.get('title', '')}\nURL: {r.get('href', '')}\nSummary: {r.get('body', '')}\n"
        for r in results
    )
