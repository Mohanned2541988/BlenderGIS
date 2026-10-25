"""Language-model adapters.

template  - offline structure generator (no AI; fills scaffolding the user edits)
anthropic - Claude via the official `anthropic` Python SDK (optional: pip install anthropic)
ollama    - local models through Ollama's native /api/chat endpoint
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .secrets import get_secret


class ProviderError(RuntimeError):
    pass


class TemplateLLM:
    name = "template"
    label = "Offline template (no AI)"
    external = False
    is_ai = False

    def available(self, settings):
        return True, "Always available. Produces editable scaffolding, not AI-written text."

    def complete(self, system, prompt, settings, max_tokens=4000, want_json=False):
        raise ProviderError("The offline template provider cannot write prose. Configure Claude or Ollama "
                            "in Settings, or write the text yourself.")


class AnthropicLLM:
    name = "anthropic"
    label = "Anthropic Claude (cloud)"
    external = True
    is_ai = True

    def available(self, settings):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "Python package missing: run  pip3 install anthropic"
        key, _ = get_secret("ANTHROPIC_API_KEY")
        if not key:
            return False, "No ANTHROPIC_API_KEY in environment or Keychain"
        return True, f"Model {settings['anthropic'].get('model')}"

    def _client(self):
        import anthropic
        key, _ = get_secret("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError("ANTHROPIC_API_KEY is not configured")
        return anthropic, anthropic.Anthropic(api_key=key, timeout=300.0, max_retries=2)

    def _create(self, settings, **kwargs):
        anthropic, client = self._client()
        cfg = settings.get("anthropic", {})
        try:
            # Server-side fallback lets the API retry on another model if a request is declined.
            resp = client.beta.messages.create(
                model=cfg.get("model") or "claude-opus-5-5",
                output_config={"effort": cfg.get("effort") or "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                **kwargs)
        except anthropic.AuthenticationError:
            raise ProviderError("Anthropic rejected the API key (401).")
        except anthropic.PermissionDeniedError:
            raise ProviderError("The API key lacks permission for this model.")
        except anthropic.NotFoundError:
            raise ProviderError("Model not found - check the model name in Settings.")
        except anthropic.RateLimitError:
            raise ProviderError("Rate limited by Anthropic - wait a minute and retry.")
        except anthropic.BadRequestError as e:
            raise ProviderError(f"Anthropic rejected the request: {getattr(e, 'message', e)}")
        except anthropic.APIStatusError as e:
            raise ProviderError(f"Anthropic API error {e.status_code}.")
        except anthropic.APIConnectionError:
            raise ProviderError("Could not reach api.anthropic.com - check your internet connection.")
        if resp.stop_reason == "refusal":
            raise ProviderError("The model declined this request.")
        return resp

    def complete(self, system, prompt, settings, max_tokens=16000, want_json=False):
        resp = self._create(settings, max_tokens=max_tokens, system=system,
                            messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        if resp.stop_reason == "max_tokens":
            raise ProviderError("Response was cut off (max tokens). Try a shorter request.")
        return text

    def web_search(self, query, settings, max_results=8):
        """Search with Claude's server-side web search tool. Only URLs that the search tool actually
        returned are kept, so the model cannot invent citations."""
        system = ("You are a research assistant. Use web search to find reputable, citable sources. "
                  "After searching, reply with ONLY a JSON array of objects with keys url, title, "
                  "published (date string or empty), summary (2 sentences, neutral, what the source says). "
                  "Include only URLs that appeared in your search results.")
        resp = self._create(settings, max_tokens=8000, system=system,
                            messages=[{"role": "user", "content": f"Find sources about: {query}"}],
                            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 4}])
        seen = {}
        for b in resp.content:
            if getattr(b, "type", "") == "web_search_tool_result":
                content = getattr(b, "content", None)
                if isinstance(content, list):
                    for r in content:
                        url = getattr(r, "url", None)
                        if url:
                            seen[url] = {"url": url, "title": getattr(r, "title", "") or url,
                                         "published": getattr(r, "page_age", "") or "", "summary": ""}
                else:
                    code = getattr(content, "error_code", "unknown")
                    raise ProviderError(f"Web search failed: {code}")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            for item in extract_json(text):
                if isinstance(item, dict) and item.get("url") in seen:
                    seen[item["url"]].update({k: item.get(k) or seen[item["url"]][k]
                                              for k in ("title", "published", "summary")})
        except ProviderError:
            pass
        return list(seen.values())[:max_results]


class OllamaLLM:
    name = "ollama"
    label = "Ollama (local, free)"
    external = False
    is_ai = True

    def available(self, settings):
        url = settings["ollama"]["url"].rstrip("/")
        try:
            with urllib.request.urlopen(url + "/api/tags", timeout=3) as r:
                models = [m.get("name") for m in json.loads(r.read()).get("models", [])]
        except (urllib.error.URLError, OSError, ValueError):
            return False, f"Ollama not reachable at {url} (install from ollama.com and run it)"
        want = settings["ollama"]["model"]
        if want not in models and f"{want}:latest" not in models:
            return False, f"Model {want} not pulled. Run: ollama pull {want}"
        return True, f"Model {want}"

    def complete(self, system, prompt, settings, max_tokens=4000, want_json=False):
        cfg = settings["ollama"]
        body = {"model": cfg["model"], "stream": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "options": {"num_predict": max_tokens}}
        if want_json:
            body["format"] = "json"
        req = urllib.request.Request(cfg["url"].rstrip("/") + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read())["message"]["content"]
        except urllib.error.HTTPError as e:
            raise ProviderError(f"Ollama error {e.code}: {e.read()[:200]!r}")
        except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
            raise ProviderError(f"Ollama request failed: {e}")


LLMS = {p.name: p for p in (TemplateLLM(), AnthropicLLM(), OllamaLLM())}


def get_llm(settings) -> object:
    return LLMS.get(settings["providers"].get("llm"), LLMS["template"])


def extract_json(text: str):
    """Parse the first JSON object/array in a model reply (tolerates ``` fences and prose)."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    starts = sorted((text.find(o), o) for o in ("[", "{") if text.find(o) != -1)
    for start, opener in starts:
        depth, in_str, esc = 0, False, False
        closer = "]" if opener == "[" else "}"
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
    raise ProviderError("The model did not return valid JSON. Try again.")
