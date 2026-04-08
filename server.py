"""Zalando MCP Server — Search products with pre-configured sizes."""

import json
import os
import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings
from pydantic import BaseModel, Field
from typing import Optional

mcp = FastMCP(
    "zalando_mcp",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)

# Ilyas's sizes
USER_SIZES = {
    "tops": "XL",
    "bottoms": "XL",
    "shoes": "47.5",
    "chest_cm": 116,
    "waist_cm": 113,
    "inseam_cm": 80,
}

ZALANDO_API = "https://www.zalando.fr/api/catalog/articles"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


class SearchInput(BaseModel):
    """Input for searching Zalando products."""
    query: str = Field(..., description="Search query (e.g. 'chino noir homme', 't-shirt hugo boss')", min_length=1)
    limit: Optional[int] = Field(default=10, description="Number of results (1-25)", ge=1, le=25)


class SizeCheckInput(BaseModel):
    """Input for checking user sizes."""
    category: Optional[str] = Field(default=None, description="Category: 'tops', 'bottoms', or 'shoes'")


@mcp.tool(
    name="zalando_search",
    annotations={
        "title": "Search Zalando Products",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zalando_search(params: SearchInput) -> str:
    """Search for products on Zalando France. Returns product names, prices, brands, URLs and available sizes.

    Args:
        params (SearchInput): Search parameters with query and optional limit.

    Returns:
        str: JSON list of products with name, brand, price, url, and image.
    """
    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as client:
        try:
            response = await client.get(
                ZALANDO_API,
                params={
                    "query": params.query,
                    "limit": params.limit,
                    "offset": 0,
                    "sort": "popularity",
                },
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError:
            return await _fallback_search(client, params.query, params.limit)
        except Exception:
            return await _fallback_search(client, params.query, params.limit)

    articles = data.get("articles", [])
    if not articles:
        return await _fallback_search(client, params.query, params.limit)

    results = []
    for item in articles[: params.limit]:
        results.append({
            "name": item.get("name", ""),
            "brand": item.get("brand_name", ""),
            "price": item.get("price", {}).get("formatted", ""),
            "original_price": item.get("price", {}).get("original", ""),
            "url": f"https://www.zalando.fr/{item.get('url_key', '')}",
            "image": item.get("media", [{}])[0].get("uri", "") if item.get("media") else "",
            "recommended_size": _get_recommended_size(params.query),
        })

    return json.dumps({
        "count": len(results),
        "user_sizes": USER_SIZES,
        "products": results,
    }, ensure_ascii=False, indent=2)


async def _fallback_search(client: httpx.AsyncClient, query: str, limit: int) -> str:
    """Fallback: build a Zalando search URL for the user."""
    search_url = f"https://www.zalando.fr/homme/?q={query.replace(' ', '+')}"
    return json.dumps({
        "note": "Could not fetch API results directly. Here is the search link:",
        "search_url": search_url,
        "recommended_size": _get_recommended_size(query),
        "user_sizes": USER_SIZES,
    }, ensure_ascii=False, indent=2)


def _get_recommended_size(query: str) -> str:
    """Recommend a size based on the query category."""
    q = query.lower()
    shoe_words = ["chaussure", "basket", "sneaker", "boot", "sandale"]
    bottom_words = ["pantalon", "chino", "jean", "short", "jogging", "jogger"]

    if any(w in q for w in shoe_words):
        return f"{USER_SIZES['shoes']} EU"
    elif any(w in q for w in bottom_words):
        return f"{USER_SIZES['bottoms']} (tour hanches {USER_SIZES['waist_cm']}cm, entrejambe {USER_SIZES['inseam_cm']}cm)"
    else:
        return f"{USER_SIZES['tops']} (poitrine {USER_SIZES['chest_cm']}cm)"


@mcp.tool(
    name="zalando_my_sizes",
    annotations={
        "title": "Get User Sizes",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def zalando_my_sizes(params: SizeCheckInput) -> str:
    """Returns the user's body measurements and recommended sizes.

    Args:
        params (SizeCheckInput): Optional category filter.

    Returns:
        str: JSON with user sizes and measurements.
    """
    if params.category and params.category in USER_SIZES:
        return json.dumps({
            "category": params.category,
            "recommended_size": USER_SIZES[params.category],
        }, indent=2)

    return json.dumps({
        "all_sizes": USER_SIZES,
        "notes": {
            "tops": "XL — Hugo Boss taille grand, Zara/Bershka prendre XL voire XXL",
            "bottoms": "XL — Jack & Jones chinos, vérifier entrejambe 80cm",
            "shoes": "47.5 EU / 13 US / 12.5 UK — ref New Balance 2002R",
        }
    }, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="streamable_http")
