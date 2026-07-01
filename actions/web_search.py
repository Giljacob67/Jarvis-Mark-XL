# web_search.py
# Gemini grounded-search replaced with DuckDuckGo + Ollama LLM summarization.
import json
from urllib.parse import urlencode

import requests

from core.paths import API_CONFIG_PATH
from core.paths import BASE_DIR


def _ddg_search(query: str, max_results: int = 6) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title":   r.get("title",  ""),
                "snippet": r.get("body",   ""),
                "url":     r.get("href",   ""),
            })
    return results


def _brave_search(
    query: str,
    api_key: str,
    max_results: int = 6,
    country: str = "BR",
) -> list[dict]:
    params = {
        "q": query,
        "count": max(1, min(max_results, 20)),
        "country": country,
        "search_lang": "pt-BR",
        "spellcheck": "true",
    }
    url = f"https://api.search.brave.com/res/v1/web/search?{urlencode(params)}"
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
    }
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    raw_results = data.get("web", {}).get("results", []) or []
    out: list[dict] = []
    for r in raw_results[:max_results]:
        out.append(
            {
                "title": r.get("title", ""),
                "snippet": r.get("description", ""),
                "url": r.get("url", ""),
            }
        )
    return out


def _load_search_config() -> dict:
    try:
        return json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_search_provider(params: dict, cfg: dict) -> str:
    requested = (params.get("provider", "auto") or "auto").strip().lower()
    if requested in ("auto", "brave", "duckduckgo"):
        if requested == "auto":
            return "brave" if (cfg.get("brave_api_key", "").strip()) else "duckduckgo"
        return requested
    return "duckduckgo"


def _search_with_provider(query: str, provider: str, cfg: dict, max_results: int = 6) -> tuple[list[dict], str]:
    if provider == "brave":
        key = (cfg.get("brave_api_key", "") or "").strip()
        if not key:
            raise RuntimeError("Brave API key missing.")
        country = (cfg.get("brave_search_country", "BR") or "BR").strip().upper()
        return _brave_search(query, api_key=key, max_results=max_results, country=country), "brave"
    return _ddg_search(query, max_results=max_results), "duckduckgo"


def _format_ddg(query: str, results: list[dict]) -> str:
    if not results:
        return f"No results found for: {query}"
    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        if r.get("title"):   lines.append(f"{i}. {r['title']}")
        if r.get("snippet"): lines.append(f"   {r['snippet']}")
        if r.get("url"):     lines.append(f"   {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()


def _llm_summarize(query: str, raw_results: str) -> str:
    try:
        from core.llm_client import call_llm_text
        system = (
            "You are JARVIS. Summarize web search results clearly and concisely. "
            "Answer the user's query directly. Be factual. Address user as 'sir'."
        )
        prompt = (
            f"User question: {query}\n\n"
            f"Web search results:\n{raw_results[:4000]}\n\n"
            "Answer the question based on these results:"
        )
        return call_llm_text(prompt, system=system)
    except Exception:
        return raw_results


def _compare(items: list[str], aspect: str, provider: str, cfg: dict) -> str:
    all_results: dict[str, list] = {}
    for item in items:
        try:
            results, _ = _search_with_provider(f"{item} {aspect}", provider, cfg, max_results=3)
            all_results[item] = results
        except Exception:
            # Fallback to DDG keeps compare mode available even if Brave fails.
            try:
                all_results[item] = _ddg_search(f"{item} {aspect}", max_results=3)
            except Exception:
                all_results[item] = []

    lines = [f"Comparison — {aspect.upper()}", "─" * 40]
    for item in items:
        lines.append(f"\n▸ {item}")
        for r in all_results.get(item, [])[:2]:
            if r.get("snippet"):
                lines.append(f"  • {r['snippet']}")
    raw = "\n".join(lines)
    return _llm_summarize(f"Compare {', '.join(items)} regarding {aspect}", raw)


def web_search(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    query  = params.get("query", "").strip()
    mode   = params.get("mode",  "search").lower().strip()
    items  = params.get("items", [])
    aspect = params.get("aspect", "general").strip() or "general"
    cfg = _load_search_config()
    provider = _resolve_search_provider(params, cfg)

    if not query and not items:
        return "Please provide a search query, sir."

    if items and mode != "compare":
        mode = "compare"

    if player:
        player.write_log(f"[Search] {query or ', '.join(items)}")

    print(f"[WebSearch] 🔍 Query: {query!r}  Mode: {mode}")

    try:
        if mode == "compare" and items:
            print(f"[WebSearch] 📊 Comparing: {items}")
            return _compare(items, aspect, provider, cfg)

        try:
            results, used_provider = _search_with_provider(query, provider, cfg)
        except Exception as e:
            print(f"[WebSearch] ⚠ Provider '{provider}' failed ({e}) — fallback DDG")
            results, used_provider = _ddg_search(query), "duckduckgo"

        raw = _format_ddg(query, results)
        print(f"[WebSearch] ✅ {used_provider.upper()}: {len(results)} result(s).")
        # Let Ollama summarise the results for a cleaner spoken response
        return _llm_summarize(query, raw)

    except Exception as e:
        print(f"[WebSearch] ❌ Failed: {e}")
        return f"Search failed, sir: {e}"
