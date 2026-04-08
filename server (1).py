"""Zalando MCP Server — Search products with pre-configured sizes and auto-filtering."""

import json
import os
import re
import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings
from pydantic import BaseModel, Field
from typing import Optional

mcp = FastMCP(
    "zalando_mcp",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)

# ============================================================
# USER PROFILE — Ilyas Benmlih
# ============================================================
USER_SIZES = {
    "tops": "XL",
    "bottoms": "XL",
    "shoes_eu": "47.5",
    "shoes_us": "13",
    "shoes_uk": "12.5",
}

USER_MEASUREMENTS = {
    "poitrine_cm": 116,
    "epaules_cm": 52,
    "bras_cm": 64,
    "hanches_cm": 113,
    "entrejambe_cm": 80,
    "cuisse_cm": 67,
}

# Brand-specific size adjustments
BRAND_SIZE_GUIDE = {
    "hugo boss": {"tops": "XL", "bottoms": "XL", "note": "Taille normalement"},
    "zara": {"tops": "XXL", "bottoms": "XXL", "note": "Taille petit, prendre une taille au-dessus"},
    "bershka": {"tops": "XXL", "bottoms": "XXL", "note": "Taille petit comme Zara"},
    "h&m": {"tops": "XL", "bottoms": "XL", "note": "Taille normalement"},
    "jack & jones": {"tops": "XL", "bottoms": "XL", "note": "Chinos OK en XL, vérifier entrejambe 80cm"},
    "jack and jones": {"tops": "XL", "bottoms": "XL", "note": "Chinos OK en XL, vérifier entrejambe 80cm"},
    "nike": {"tops": "XL", "bottoms": "XL", "shoes": "47.5", "note": "Taille normalement"},
    "adidas": {"tops": "XL", "bottoms": "XL", "shoes": "47.5", "note": "Taille normalement"},
    "new balance": {"shoes": "47.5", "note": "Ref: 2002R en 47.5 EU = parfait"},
    "uniqlo": {"tops": "XL", "bottoms": "XL", "note": "Coupe asiatique, peut tailler petit"},
    "ralph lauren": {"tops": "XL", "bottoms": "XL", "note": "Taille normalement, coupe classique"},
    "tommy hilfiger": {"tops": "XL", "bottoms": "XL", "note": "Taille normalement"},
    "selected homme": {"tops": "XL", "bottoms": "XL", "note": "Coupe slim, vérifier les mesures"},
    "asos": {"tops": "XL", "bottoms": "XL", "note": "Taille normalement, bien vérifier le guide"},
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


# ============================================================
# CATEGORY & SIZE DETECTION
# ============================================================
SHOE_WORDS = ["chaussure", "basket", "sneaker", "boot", "sandale", "mocassin", "derby", "espadrille", "tong", "mule", "trainer"]
BOTTOM_WORDS = ["pantalon", "chino", "jean", "short", "jogging", "jogger", "bermuda", "cargo", "slim", "skinny", "droit", "regular"]
TOP_WORDS = ["t-shirt", "tshirt", "tee", "polo", "chemise", "sweat", "hoodie", "veste", "blouson", "manteau", "pull", "gilet", "survetement"]


def _detect_category(query: str) -> str:
    q = query.lower()
    if any(w in q for w in SHOE_WORDS):
        return "shoes"
    elif any(w in q for w in BOTTOM_WORDS):
        return "bottoms"
    else:
        return "tops"


def _get_size_for_query(query: str) -> dict:
    """Get the right size based on query category and detected brand."""
    category = _detect_category(query)
    q = query.lower()

    # Check if a known brand is in the query
    brand_info = None
    for brand, sizes in BRAND_SIZE_GUIDE.items():
        if brand in q:
            brand_info = {"brand": brand, **sizes}
            break

    if category == "shoes":
        size = USER_SIZES["shoes_eu"]
        size_label = f"{size} EU ({USER_SIZES['shoes_us']} US / {USER_SIZES['shoes_uk']} UK)"
    elif category == "bottoms":
        size = brand_info.get("bottoms", USER_SIZES["bottoms"]) if brand_info else USER_SIZES["bottoms"]
        size_label = f"{size} (hanches {USER_MEASUREMENTS['hanches_cm']}cm, entrejambe {USER_MEASUREMENTS['entrejambe_cm']}cm)"
    else:
        size = brand_info.get("tops", USER_SIZES["tops"]) if brand_info else USER_SIZES["tops"]
        size_label = f"{size} (poitrine {USER_MEASUREMENTS['poitrine_cm']}cm)"

    return {
        "category": category,
        "size": size,
        "size_label": size_label,
        "brand_note": brand_info.get("note", "") if brand_info else "",
    }


def _build_search_url(query: str, size_info: dict) -> str:
    """Build a Zalando search URL with size filter."""
    q = query.replace(" ", "+")
    base = f"https://www.zalando.fr/homme/?q={q}"

    # Add size parameter
    size = size_info["size"]
    category = size_info["category"]

    if category == "shoes":
        return f"{base}&taille={size}"
    else:
        return f"{base}&taille={size}"


# ============================================================
# PRODUCT EXTRACTION
# ============================================================
def _extract_products_from_html(html: str, limit: int) -> list:
    """Extract product data from Zalando search page HTML using multiple methods."""
    products = []

    # Method 1: JSON-LD
    ld_json_matches = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    for match in ld_json_matches:
        try:
            data = json.loads(match.strip())
            if isinstance(data, dict) and data.get("@type") == "ItemList":
                for item in data.get("itemListElement", [])[:limit]:
                    product = item.get("item", item)
                    products.append({
                        "name": product.get("name", ""),
                        "brand": product.get("brand", {}).get("name", "") if isinstance(product.get("brand"), dict) else str(product.get("brand", "")),
                        "price": str(product.get("offers", {}).get("lowPrice", product.get("offers", {}).get("price", ""))),
                        "url": product.get("url", ""),
                        "image": product.get("image", ""),
                    })
            elif isinstance(data, list):
                for item in data[:limit]:
                    if item.get("@type") in ("Product",):
                        products.append({
                            "name": item.get("name", ""),
                            "brand": item.get("brand", {}).get("name", "") if isinstance(item.get("brand"), dict) else str(item.get("brand", "")),
                            "price": str(item.get("offers", {}).get("lowPrice", item.get("offers", {}).get("price", ""))),
                            "url": item.get("url", ""),
                            "image": item.get("image", ""),
                        })
        except json.JSONDecodeError:
            continue

    if products:
        return products[:limit]

    # Method 2: __NEXT_DATA__
    next_data_match = re.search(
        r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    if next_data_match:
        try:
            data = json.loads(next_data_match.group(1).strip())
            props = data.get("props", {}).get("pageProps", {})
            articles = props.get("articles", props.get("products", props.get("items", [])))
            if isinstance(articles, list):
                for item in articles[:limit]:
                    products.append({
                        "name": item.get("name", item.get("title", "")),
                        "brand": item.get("brand_name", item.get("brand", "")),
                        "price": item.get("price", {}).get("formatted", str(item.get("price", ""))) if isinstance(item.get("price"), dict) else str(item.get("price", "")),
                        "url": f"https://www.zalando.fr/{item.get('url_key', item.get('slug', ''))}" if not str(item.get("url", "")).startswith("http") else item.get("url", ""),
                        "image": item.get("image_url", ""),
                    })
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    if products:
        return products[:limit]

    # Method 3: Inline script with articles array
    script_matches = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    for script in script_matches:
        if '"articles"' in script or '"products"' in script or '"items"' in script:
            try:
                # Find JSON-like structures
                for key in ["articles", "products", "items"]:
                    pattern = rf'"{key}"\s*:\s*(\[.*?\])'
                    arr_match = re.search(pattern, script, re.DOTALL)
                    if arr_match:
                        items = json.loads(arr_match.group(1))
                        for item in items[:limit]:
                            if isinstance(item, dict) and item.get("name"):
                                products.append({
                                    "name": item.get("name", ""),
                                    "brand": item.get("brand_name", item.get("brand", "")),
                                    "price": str(item.get("price", {}).get("formatted", item.get("price", ""))) if isinstance(item.get("price"), dict) else str(item.get("price", "")),
                                    "url": f"https://www.zalando.fr/{item.get('url_key', '')}",
                                    "image": "",
                                })
            except (json.JSONDecodeError, TypeError):
                continue

    if products:
        return products[:limit]

    # Method 4: HTML card parsing
    card_pattern = re.compile(
        r'href=["\'](https://www\.zalando\.fr/[^"\']+\.html)["\'].*?'
        r'alt=["\']([^"\']+)["\']',
        re.DOTALL
    )
    seen_urls = set()
    for match in card_pattern.finditer(html):
        url, alt_text = match.groups()
        if url not in seen_urls and alt_text:
            seen_urls.add(url)
            price_match = re.search(r'(\d+[.,]\d{2})\s*€', html[match.start():match.start() + 2000])
            price = f"{price_match.group(1)} €" if price_match else ""
            products.append({
                "name": alt_text,
                "brand": "",
                "price": price,
                "url": url,
                "image": "",
            })

    return products[:limit]


# ============================================================
# TOOLS
# ============================================================
class SearchInput(BaseModel):
    """Input for searching Zalando products."""
    query: str = Field(..., description="Search query (e.g. 'chino noir homme', 't-shirt hugo boss homme', 'sneakers blanc')", min_length=1)
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
    """Search for products on Zalando France. Automatically filters by the user's size and provides brand-specific sizing advice.

    Args:
        params (SearchInput): Search parameters with query and optional limit.

    Returns:
        str: JSON with products, size recommendation, and filtered search URL.
    """
    size_info = _get_size_for_query(params.query)
    search_url = _build_search_url(params.query, size_info)
    query_with_size = f"{params.query} taille {size_info['size']}"
    query_encoded = params.query.replace(" ", "+")

    async with httpx.AsyncClient(headers=HEADERS, timeout=20.0, follow_redirects=True) as client:
        # Try 1: Zalando API with size
        try:
            api_response = await client.get(
                "https://www.zalando.fr/api/catalog/articles",
                params={
                    "query": params.query,
                    "limit": params.limit,
                    "offset": 0,
                    "sort": "popularity",
                },
            )
            if api_response.status_code == 200:
                data = api_response.json()
                articles = data.get("articles", [])
                if articles:
                    results = []
                    for item in articles[:params.limit]:
                        brand_name = item.get("brand_name", "")
                        brand_advice = BRAND_SIZE_GUIDE.get(brand_name.lower(), {})
                        results.append({
                            "name": item.get("name", ""),
                            "brand": brand_name,
                            "price": item.get("price", {}).get("formatted", ""),
                            "url": f"https://www.zalando.fr/{item.get('url_key', '')}",
                            "image": item.get("media", [{}])[0].get("uri", "") if item.get("media") else "",
                            "size_advice": brand_advice.get("note", "Prendre taille standard"),
                        })
                    return json.dumps({
                        "count": len(results),
                        "size_recommendation": size_info,
                        "filtered_search_url": search_url,
                        "products": results,
                    }, ensure_ascii=False, indent=2)
        except Exception:
            pass

        # Try 2: Scrape HTML
        try:
            html_response = await client.get(search_url)
            if html_response.status_code == 200:
                products = _extract_products_from_html(html_response.text, params.limit)
                if products:
                    # Add brand-specific advice
                    for p in products:
                        brand = p.get("brand", "").lower()
                        advice = BRAND_SIZE_GUIDE.get(brand, {})
                        p["size_advice"] = advice.get("note", "Prendre taille standard")
                    return json.dumps({
                        "count": len(products),
                        "size_recommendation": size_info,
                        "filtered_search_url": search_url,
                        "products": products,
                    }, ensure_ascii=False, indent=2)
        except Exception:
            pass

        # Try 3: Scrape without size filter
        try:
            basic_url = f"https://www.zalando.fr/homme/?q={query_encoded}"
            html_response = await client.get(basic_url)
            if html_response.status_code == 200:
                products = _extract_products_from_html(html_response.text, params.limit)
                if products:
                    for p in products:
                        brand = p.get("brand", "").lower()
                        advice = BRAND_SIZE_GUIDE.get(brand, {})
                        p["size_advice"] = advice.get("note", "Prendre taille standard")
                    return json.dumps({
                        "count": len(products),
                        "size_recommendation": size_info,
                        "filtered_search_url": search_url,
                        "products": products,
                    }, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # Fallback
    return json.dumps({
        "note": "Impossible de récupérer les produits directement.",
        "filtered_search_url": search_url,
        "search_url_no_filter": f"https://www.zalando.fr/homme/?q={query_encoded}",
        "size_recommendation": size_info,
        "tip": "Ouvre le lien filtré ci-dessus. Ta taille est déjà sélectionnée.",
    }, ensure_ascii=False, indent=2)


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
    """Returns the user's body measurements and recommended sizes per brand.

    Args:
        params (SizeCheckInput): Optional category filter.

    Returns:
        str: JSON with user sizes, measurements, and brand-specific advice.
    """
    if params.category:
        cat = params.category.lower()
        relevant_brands = {}
        for brand, info in BRAND_SIZE_GUIDE.items():
            if cat in info:
                relevant_brands[brand] = {
                    "size": info[cat],
                    "note": info.get("note", ""),
                }
        return json.dumps({
            "category": cat,
            "default_size": USER_SIZES.get(cat, USER_SIZES.get("tops")),
            "brands": relevant_brands,
            "measurements": USER_MEASUREMENTS,
        }, ensure_ascii=False, indent=2)

    return json.dumps({
        "default_sizes": USER_SIZES,
        "measurements": USER_MEASUREMENTS,
        "brand_guide": BRAND_SIZE_GUIDE,
        "notes": {
            "general": "Taille standard XL, sauf Zara/Bershka qui taillent petit (prendre XXL)",
            "chaussures": "47.5 EU partout, référence = New Balance 2002R",
            "pantalons": "Toujours vérifier entrejambe 80cm",
        },
    }, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app = mcp.streamable_http_app()
    uvicorn.run(app, host="0.0.0.0", port=port)
