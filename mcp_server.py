#!/usr/bin/env python3
"""MCP server: search fresh remote tech jobs from an AI assistant (Claude, Cursor, ...).

It runs the same fetch + classify pipeline as board.py on your own machine, straight from the sources
(Himalayas, Jobicy, Remote OK, Hacker News, company career pages), and caches the result for a few hours.
No Notion token or API key needed.

  python3 mcp_server.py        # stdio transport; register it in your MCP client (see README)
"""
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

try:   # python.org builds on macOS ship without CA certificates; certifi comes with the mcp package
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

import board
import countries

CACHE = Path.home() / ".cache" / "tech-jobs-board" / "jobs.json"
TTL_HOURS = 6          # refresh in the background when the cache is older than this
MIN_REFRESH_GAP = 3    # hours; keeps request volume polite towards the sources
WAIT_FIRST_LOAD = 50   # seconds a tool waits for the very first fetch before answering

CATEGORIES = [c["name"] for c in board.CFG["categories"]]
SENIORITIES = [name for name, _ in board.SENIORITY] + ["Mid / Not stated"]
REGIONS = [name for name, _ in board.REGIONS] + ["See listing"]

mcp = FastMCP("remote-tech-jobs")
_lock = threading.Lock()
_loaded = threading.Event()
_state = {"jobs": [], "fetched_at": None, "sources": {}, "refreshing": False, "error": None}


def _log(*a):
    print(*a, file=sys.stderr)   # stdout belongs to the MCP protocol


def _load_cache():
    try:
        data = json.loads(CACHE.read_text())
        _state.update(jobs=data["jobs"], fetched_at=data["fetched_at"], sources=data.get("sources", {}))
        _loaded.set()
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass


def _age_hours():
    if not _state["fetched_at"]:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(_state["fetched_at"])).total_seconds() / 3600


def _refresh():
    """Fetch and classify everything (about a minute), then swap it in and write the cache."""
    try:
        board.NOW = datetime.now(timezone.utc)
        board.CUTOFF = board.NOW - timedelta(days=board.CFG["max_age_days"])
        jobs = board.collect()
        out, sources = [], {}
        for j in jobs:
            sources[j["source"]] = sources.get(j["source"], 0) + 1
            out.append({**j, "posted": j["posted"].date().isoformat()})
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _state.update(jobs=out, fetched_at=fetched_at, sources=sources, error=None)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"fetched_at": fetched_at, "sources": sources, "jobs": out}))
        _log(f"refreshed: {len(out)} jobs")
    except Exception as e:   # keep serving the old cache if a refresh fails
        _state["error"] = f"{type(e).__name__}: {e}"
        _log("refresh failed:", _state["error"])
    finally:
        _state["refreshing"] = False
        _loaded.set()


def _start_refresh(force=False):
    with _lock:
        age = _age_hours()
        if _state["refreshing"]:
            return "already running"
        if age is not None and age < (MIN_REFRESH_GAP if force else TTL_HOURS):
            return f"cache is {age:.1f} h old, no refresh needed"
        _state["refreshing"] = True
    threading.Thread(target=_refresh, daemon=True).start()
    return "started"


def _ensure_data():
    _start_refresh()
    _loaded.wait(WAIT_FIRST_LOAD)


def _pick(value, allowed, field):
    """Match a user-supplied filter value to an allowed one, loosely ('ai', 'AI/ML' -> 'AI & ML Engineering')."""
    if not value:
        return None
    v = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    for a in allowed:
        an = re.sub(r"[^a-z0-9]+", " ", a.lower()).strip()
        if v == an or v in an.split() or an.startswith(v):
            return a
    raise ValueError(f"Unknown {field} '{value}'. Use one of: {', '.join(allowed)}")


def _notes(country, raw_country, unclear):
    notes = []
    if not _state["jobs"]:
        notes.append("Still fetching jobs for the first time (about a minute). Try again shortly.")
    if country and country not in countries.ALL:
        notes.append(f"'{raw_country}' is not in the country list, so only exact mentions and worldwide jobs match.")
    if country and unclear:
        notes.append(f"{unclear} more jobs only say 'Remote' with no country; check those listings yourself.")
    return " ".join(notes) or None


@mcp.tool()
def search_jobs(query: str = "", category: str = "", seniority: str = "", open_to: str = "",
                open_to_country: str = "", posted_within_days: int = 14, limit: int = 15) -> dict:
    """Search fresh remote tech jobs (AI/ML, data, software, DevOps, security).

    Args:
        query: words that must all appear in the title, company or location, e.g. "python llm" or "Berlin".
        category: one of the categories from list_filters (e.g. "AI & ML Engineering", "Data Engineering").
        seniority: Intern, Junior, Mid / Not stated, Senior, Lead / Staff+ or Manager.
        open_to: region the employer hires from: Worldwide, Africa & Middle East, Europe, Americas, Asia-Pacific.
            Coarse: "Africa & Middle East" also includes roles limited to one country there.
        open_to_country: a country the candidate lives in, e.g. "Morocco". Keeps only jobs whose location names
            that country, says worldwide, or names a region that contains it (EMEA, Africa, MENA...). Prefer this
            over open_to when the user says where they are.
        posted_within_days: only jobs posted in the last N days (max 14).
        limit: max results (1-50), newest first.

    Every result links to the original posting; apply there, and check "location" for country limits.
    """
    _ensure_data()
    cat = _pick(category, CATEGORIES, "category")
    sen = _pick(seniority, SENIORITIES, "seniority")
    reg = _pick(open_to, REGIONS, "open_to")
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(posted_within_days, 14)))).date().isoformat()
    terms = [t for t in re.split(r"\s+", query.lower().strip()) if t]
    country = countries.canonical(open_to_country) if open_to_country.strip() else None

    hits, why, unclear = [], {}, 0
    for j in _state["jobs"]:
        if j["posted"] < since or (cat and j["category"] != cat) or (sen and j["seniority"] != sen):
            continue
        if reg and reg not in j["regions"]:
            continue
        hay = f"{j['title']} {j['company']} {j['location']}".lower()
        if not all(t in hay for t in terms):
            continue
        if country:
            reason = countries.match(j["location"], country)
            if reason in (None, "unclear"):
                unclear += reason == "unclear"
                continue
            why[j["url"]] = reason
        hits.append(j)
    hits.sort(key=lambda j: j["posted"], reverse=True)
    limit = max(1, min(limit, 50))
    return {
        "total_matches": len(hits),
        "showing": min(limit, len(hits)),
        "jobs": [{"title": j["title"], "company": j["company"], "category": j["category"],
                  "seniority": j["seniority"], "open_to": j["regions"], "location": j["location"],
                  "salary": j["salary"] or None, "posted": j["posted"], "apply_url": j["url"],
                  "source": j["source"], "source_page": j["listing"],
                  **({"open_to_country": why[j["url"]]} if country else {})} for j in hits[:limit]],
        "data_as_of": _state["fetched_at"],
        "note": _notes(country, open_to_country, unclear),
    }


@mcp.tool()
def list_filters() -> dict:
    """List the valid filter values for search_jobs, with how many current jobs each has."""
    _ensure_data()
    def count(key, values, multi=False):
        return {v: sum((v in j[key]) if multi else (j[key] == v) for j in _state["jobs"]) for v in values}
    return {"categories": count("category", CATEGORIES), "seniority": count("seniority", SENIORITIES),
            "open_to": count("regions", REGIONS, multi=True), "total_jobs": len(_state["jobs"]),
            "data_as_of": _state["fetched_at"]}


@mcp.tool()
def board_status() -> dict:
    """When the job data was last fetched, how many jobs came from each source, and whether a refresh is running."""
    age = _age_hours()
    return {"total_jobs": len(_state["jobs"]), "data_as_of": _state["fetched_at"],
            "age_hours": None if age is None else round(age, 1), "jobs_per_source": _state["sources"],
            "refreshing": _state["refreshing"], "last_error": _state["error"],
            "sources_credit": "Listings via Himalayas, Jobicy, Remote OK, Hacker News and company career pages."}


@mcp.tool()
def refresh_jobs() -> str:
    """Fetch the latest jobs now (takes about a minute; allowed at most every 3 hours)."""
    return _start_refresh(force=True)


def main():
    _load_cache()
    _start_refresh()   # warm up in the background so the first search is fast
    mcp.run()


if __name__ == "__main__":
    main()
