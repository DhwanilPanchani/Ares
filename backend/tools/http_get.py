"""
Tool: http_get

Makes a GET request to any URL using httpx with a 10-second timeout.
Returns the response body truncated to 3000 characters so the agent's
context window isn't overwhelmed by large HTML pages.
Connection and HTTP errors return descriptive error strings.
"""

from __future__ import annotations

import logging

import httpx
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10
_MAX_RESPONSE_CHARS = 3000


@tool
async def http_get(url: str) -> str:
    """
    Make an HTTP GET request to a URL and return the response body.

    Use this tool to fetch the content of a specific webpage, API endpoint,
    or any publicly accessible URL. The response is truncated to 3000
    characters to fit in context. Prefer web_search for general research;
    use http_get when you have a specific URL to fetch.

    Args:
        url: The full URL to request (must start with http:// or https://).

    Returns:
        Response text (up to 3000 characters), or an error message.
    """
    if not url.startswith(("http://", "https://")):
        return f"Error: URL must start with http:// or https://. Got: {url!r}"

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            text = response.text
    except httpx.TimeoutException:
        return f"Error: request to {url!r} timed out after {_TIMEOUT_SECONDS} seconds."
    except httpx.HTTPStatusError as exc:
        return (
            f"Error: HTTP {exc.response.status_code} from {url!r}. "
            f"Response: {exc.response.text[:200]}"
        )
    except httpx.RequestError as exc:
        return f"Error: could not connect to {url!r}: {exc}"
    except Exception as exc:
        return f"Error: unexpected error fetching {url!r}: {exc}"

    if len(text) > _MAX_RESPONSE_CHARS:
        return text[:_MAX_RESPONSE_CHARS] + f"\n\n[... truncated — {len(text)} total chars]"
    return text
