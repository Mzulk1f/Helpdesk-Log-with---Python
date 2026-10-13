"""AI helpers for Deskly.

* ``complete()``  - tiny Claude Messages API client (standard library only, no extra packages).
* ``Index``       - local "similar tickets" search (TF-IDF + cosine). Runs entirely on this
                    machine, needs no API key and sends nothing anywhere.

Configuration (environment variables):
    ANTHROPIC_API_KEY   your key; without it only the local features work
    AI_MODEL            default claude-haiku-5-5 (fast and cheapest)
    AI_BASE_URL         default https://api.anthropic.com
    AI_TIMEOUT          seconds, default 25
"""
import json
import math
import os
import re
import urllib.error
import urllib.request
from collections import Counter

API_VERSION = "2023-06-01"


def api_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def configured() -> bool:
    return bool(api_key())


def model() -> str:
    return os.environ.get("AI_MODEL", "claude-haiku-5-5").strip() or "claude-haiku-5-5"


class AIError(Exception):
    """A problem talking to the AI service, with a message that is safe to show to users."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _post(body: dict) -> dict:
    base = os.environ.get("AI_BASE_URL", "https://api.anthropic.com").rstrip("/")
    req = urllib.request.Request(
        base + "/v1/messages", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"x-api-key": api_key(), "anthropic-version": API_VERSION, "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=float(os.environ.get("AI_TIMEOUT", "25"))) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AIError("The AI service rejected the API key. Check ANTHROPIC_API_KEY.", 502)
        if e.code in (429, 529):
            raise AIError("The AI service is busy. Please try again in a moment.", 503)
        if e.code == 404:
            raise AIError("The AI model was not found. Check AI_MODEL.", 502)
        if e.code == 400:
            raise _BadRequest()
        raise AIError(f"The AI service returned an error ({e.code}).", 502)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AIError("Could not reach the AI service. Check the internet connection.", 502)
    except ValueError:
        raise AIError("The AI service sent an unreadable reply.", 502)


class _BadRequest(Exception):
    pass


def complete(system: str, user: str, max_tokens: int = 300) -> str:
    """Send one prompt to Claude and return the text of the reply.

    Newer models think before answering, and that thinking uses up max_tokens, which can leave no room for
    the answer itself. These tasks are short, so thinking is switched off. If the model refuses that setting,
    we retry without it and with a much larger token limit instead.
    """
    if not configured():
        raise AIError("AI is not configured. Set ANTHROPIC_API_KEY to enable it.", 503)
    body = {"model": model(), "max_tokens": max_tokens, "system": system,
            "thinking": {"type": "disabled"}, "messages": [{"role": "user", "content": user}]}
    try:
        data = _post(body)
    except _BadRequest:
        body.pop("thinking")
        body["max_tokens"] = max_tokens + 4000
        try:
            data = _post(body)
        except _BadRequest:
            raise AIError("The AI service did not accept the request. Check AI_MODEL.", 502)
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    if not text:
        if data.get("stop_reason") == "refusal":
            raise AIError("The AI declined to answer this request.", 502)
        if data.get("stop_reason") == "max_tokens":
            raise AIError("The AI ran out of room before it wrote an answer. Please try again.", 502)
        raise AIError("The AI service returned an empty reply.", 502)
    return text


def extract_json(text: str):
    """Pull the first JSON object out of a model reply (tolerates code fences / extra words)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


# ----------------------------------------------------- local similar-ticket search
STOP = set("""a an the and or but if then so of to in on at by for with from as is are was were be been being it its this that
these those i we you he she they them my our your their me us him her not no yes do does did done have has had having can could
should would will just very also there here when where what which who how why than too into out up down over under again still
please pls hi hello dear thanks thank any some all more most other such only own same few each both get got getting need needs
needed want wants cannot cant wont isnt doesnt dont didnt unable""".split())


def _stem(word: str) -> str:
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokenize(text: str) -> list:
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 1 and w not in STOP]


def _weight(counter: Counter, idf: dict, unseen: float) -> dict:
    return {t: (1 + math.log(n)) * idf.get(t, unseen) for t, n in counter.items()}


class Index:
    """TF-IDF index over (id, text) documents; ``query`` returns the closest ids with a 0-1 score."""

    def __init__(self, docs):
        counters = [(i, Counter(tokenize(text))) for i, text in docs]
        n = len(counters)
        df = Counter(t for _, c in counters for t in c)
        self.idf = {t: math.log((1 + n) / (1 + d)) + 1 for t, d in df.items()}
        self.unseen = math.log(1 + n) + 1
        self.vecs = []
        for i, c in counters:
            w = _weight(c, self.idf, self.unseen)
            norm = math.sqrt(sum(v * v for v in w.values())) or 1.0
            self.vecs.append((i, {t: v / norm for t, v in w.items()}))

    def query(self, text: str, k: int = 5, min_score: float = 0.18, exclude=None):
        c = Counter(tokenize(text))
        if not c:
            return []
        w = _weight(c, self.idf, self.unseen)
        norm = math.sqrt(sum(v * v for v in w.values())) or 1.0
        hits = []
        for i, vec in self.vecs:
            if i == exclude:
                continue
            score = sum(v * vec.get(t, 0.0) for t, v in w.items() if t in vec) / norm
            if score >= min_score:
                hits.append((round(score, 3), i))
        hits.sort(reverse=True)
        return [(i, s) for s, i in hits[:k]]


_cache = {"key": None, "index": None}


def get_index(key, build_docs) -> Index:
    """Rebuild the index only when the underlying tickets changed (``key`` changes)."""
    if _cache["key"] != key or _cache["index"] is None:
        _cache["index"] = Index(build_docs())
        _cache["key"] = key
    return _cache["index"]
