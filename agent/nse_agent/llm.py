"""Optional free LLM layer (Gemini free tier). It only summarises text it is given; every number in an
email comes from the rules engine, never from the model."""
import json
import urllib.request


def _gemini(cfg, prompt: str, timeout: float = 40) -> str | None:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{cfg.gemini_model}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400}}
    req = urllib.request.Request(url, json.dumps(body).encode(), {
        "Content-Type": "application/json", "x-goog-api-key": cfg.gemini_api_key}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return None


def enabled(cfg) -> bool:
    return cfg.llm_provider == "gemini" and bool(cfg.gemini_api_key)


def summarise_news(cfg, headlines: list[str], context: str) -> str | None:
    """Two or three plain sentences on what the headlines mean for Indian markets today, or None."""
    if not enabled(cfg) or not headlines:
        return None
    prompt = (
        "You are a careful Indian equity market desk analyst. Using ONLY the headlines and market snapshot below, "
        "write at most 3 short sentences: what happened, which way it likely pushes NIFTY or the named stocks, "
        "and what to watch. Do not invent numbers, prices or facts that are not in the text. Do not give buy or "
        "sell advice.\n\nMarket snapshot: " + context + "\n\nHeadlines:\n- " + "\n- ".join(headlines[:8])
    )
    return _gemini(cfg, prompt)
