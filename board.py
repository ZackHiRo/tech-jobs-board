#!/usr/bin/env python3
"""Daily remote tech-jobs board in Notion.

Pulls fresh remote roles in AI/ML, data, software, DevOps and security from free
sources that allow republishing with credit, gives each one a category, seniority
and region, and adds the new ones to a Notion database that visitors can filter.

  python3 board.py setup --parent <notion page id>   # one time: create the database
  python3 board.py run                               # fetch, classify, add new rows, prune old ones
  python3 board.py run --dry-run                     # same, but write jobs.json/jobs.csv instead of Notion

Needs NOTION_TOKEN (and NOTION_DATABASE_ID for `run`). Standard library only.
"""
import csv
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "config.json").read_text())
UA = {"User-Agent": "tech-jobs-board/1.0 (+https://github.com/; daily, cached)"}

NOW = datetime.now(timezone.utc)
CUTOFF = NOW - timedelta(days=CFG["max_age_days"])
CATEGORIES = [(c["name"], re.compile(c["title"], re.I)) for c in CFG["categories"]]
EXCLUDE = re.compile(CFG["title_exclude"], re.I)

SENIORITY = [  # first match wins
    ("Intern", r"\b(intern|internship|stagiaire|apprentice|working student)\b"),
    ("Lead / Staff+", r"\b(staff|principal|lead|head of|distinguished|architect)\b"),
    ("Manager", r"\b(manager|director|vp|vice president|chief|cto)\b"),
    ("Senior", r"\b(senior|sr\.?|iii|iv)\b"),
    ("Junior", r"\b(junior|jr\.?|entry[- ]level|graduate|new grad|associate)\b"),
]
SENIORITY = [(n, re.compile(p, re.I)) for n, p in SENIORITY]

REGIONS = [  # every region whose pattern matches the location text is tagged
    ("Worldwide", r"\b(worldwide|anywhere|global|any location)\b"),
    ("Americas", r"\b(us|usa|u\.s\.|united states|america|americas|north america|canada|mexico|brazil|latam|latin america|"
                 r"argentina|colombia|chile|peru|uruguay|costa rica|anguilla|caribbean|est|pst|cst)\b"),
    ("Europe", r"\b(europe|eu|emea|uk|united kingdom|england|scotland|germany|france|spain|portugal|netherlands|belgium|"
               r"poland|ireland|italy|sweden|norway|denmark|finland|switzerland|austria|czech\w*|slovakia|hungary|"
               r"romania|bulgaria|greece|croatia|serbia|ukraine|estonia|latvia|lithuania|cyprus|malta|luxembourg|cet|gmt)\b"),
    ("Africa & Middle East", r"\b(africa|emea|morocco|egypt|nigeria|kenya|ghana|south africa|middle east|israel|turkey|"
                             r"uae|dubai|saudi|qatar|mena|tunisia|algeria)\b"),
    ("Asia-Pacific", r"\b(apac|asia|india|singapore|japan|korea|taiwan|hong kong|china|australia|new zealand|"
                     r"philippines|indonesia|malaysia|thailand|vietnam|pakistan|bangladesh|sri lanka)\b"),
]
REGIONS = [(n, re.compile(p, re.I)) for n, p in REGIONS]


# ---------- helpers ----------

def get(url, as_json=True, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if as_json else body


def strip_html(s, n=400):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n]


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def as_list(v):
    if isinstance(v, list):
        return [str(x) for x in v]
    return [v] if v else []


def date_of(v):
    """Unix seconds, unix ms or ISO string -> aware datetime (or None)."""
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            v = int(v)
            return datetime.fromtimestamp(v / 1000 if v > 10**11 else v, timezone.utc)
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def job(source, title, company, location, posted, url, listing="", salary="", level=""):
    return {"source": source, "title": strip_html(title, 200), "company": strip_html(company, 120),
            "location": strip_html(location, 200) or "Remote", "posted": posted, "url": url,
            "listing": listing or url, "salary": salary, "level_hint": level}


def salary_of(lo, hi, cur, period=""):
    try:
        lo, hi = int(float(lo or 0)), int(float(hi or 0))
    except ValueError:
        return ""
    if not lo and not hi:
        return ""
    fmt = lambda n: f"{n // 1000}k" if n >= 1000 else str(n)
    rng = f"{fmt(lo)}–{fmt(hi)}" if lo and hi and lo != hi else fmt(lo or hi)
    return f"{rng} {cur or ''} {'/' + period if period and period != 'annual' else ''}".strip()


# ---------- sources (each returns a list of job dicts; failures only log) ----------

def himalayas():
    out = []
    for q in CFG["himalayas_queries"]:
        for page in range(1, CFG["himalayas_pages"] + 1):
            try:
                data = get("https://himalayas.app/jobs/api/search?" +
                           urllib.parse.urlencode({"q": q, "sort": "recent", "page": page}))
            except Exception as e:
                print(f"  himalayas '{q}' p{page} failed: {e}", file=sys.stderr)
                break
            jobs = data.get("jobs", [])
            for x in jobs:
                posted = date_of(x.get("pubDate"))
                if not posted or posted < CUTOFF:
                    continue
                restr = as_list(x.get("locationRestrictions"))
                out.append(job("Himalayas", x.get("title"), x.get("companyName"),
                               "Remote, " + (", ".join(restr) if restr else "worldwide"), posted,
                               x.get("applicationLink") or x.get("guid"), x.get("guid"),
                               salary_of(x.get("minSalary"), x.get("maxSalary"), x.get("currency"), x.get("salaryPeriod")),
                               " ".join(as_list(x.get("seniority")))))
            if not jobs:
                break
            time.sleep(1.2)
    return out


def jobicy():
    out = []
    for ind in CFG["jobicy_industries"]:
        try:
            data = get("https://jobicy.com/api/jobs?" + urllib.parse.urlencode({"count": 100, "industry": ind}))
        except Exception as e:
            print(f"  jobicy '{ind}' failed: {e}", file=sys.stderr)
            continue
        for x in data.get("jobs", []):
            posted = date_of(x.get("pubDate"))
            if not posted or posted < CUTOFF:
                continue
            out.append(job("Jobicy", x.get("jobTitle"), x.get("companyName"), "Remote, " + (x.get("jobGeo") or "see listing"),
                           posted, x.get("url"), x.get("url"),
                           salary_of(x.get("salaryMin") or x.get("annualSalaryMin"), x.get("salaryMax") or x.get("annualSalaryMax"),
                                     x.get("salaryCurrency")), x.get("jobLevel") or ""))
        time.sleep(1.5)
    return out


def remoteok():
    try:
        data = get("https://remoteok.com/api")
    except Exception as e:
        print(f"  remoteok failed: {e}", file=sys.stderr)
        return []
    out = []
    for x in data[1:]:  # first item is the legal notice
        posted = date_of(x.get("epoch"))
        if not posted or posted < CUTOFF:
            continue
        # Remote OK's terms: link to the Remote OK page and name Remote OK as the source
        out.append(job("Remote OK", x.get("position"), x.get("company"), "Remote, " + (x.get("location") or "see listing"),
                       posted, x.get("url"), x.get("url"), salary_of(x.get("salary_min"), x.get("salary_max"), "USD")))
    return out


def company_boards():
    out = []
    for co in CFG["greenhouse"]:
        try:
            for x in get(f"https://boards-api.greenhouse.io/v1/boards/{co}/jobs").get("jobs", []):
                loc = (x.get("location") or {}).get("name", "")
                posted = date_of(x.get("first_published") or x.get("updated_at"))
                if "remote" in loc.lower() and posted and posted >= CUTOFF:
                    out.append(job("Company site", x["title"], x.get("company_name") or co.title(), loc, posted, x["absolute_url"]))
        except Exception as e:
            print(f"  greenhouse {co} failed: {e}", file=sys.stderr)
    for co in CFG["ashby"]:
        try:
            data = get(f"https://api.ashbyhq.com/posting-api/job-board/{co}?includeCompensation=true")
            for x in data.get("jobs", []):
                posted = date_of(x.get("publishedAt"))
                if (x.get("workplaceType") or "").lower() == "remote" and posted and posted >= CUTOFF and x.get("isListed", True):
                    comp = (x.get("compensation") or {}).get("compensationTierSummary") or ""
                    out.append(job("Company site", x["title"], co.title(), x.get("location") or "Remote", posted,
                                   x["jobUrl"], salary=comp))
        except Exception as e:
            print(f"  ashby {co} failed: {e}", file=sys.stderr)
    for co in CFG["lever"]:
        try:
            for x in get(f"https://api.lever.co/v0/postings/{co}?mode=json"):
                posted = date_of(x.get("createdAt"))
                if (x.get("workplaceType") or "").lower() == "remote" and posted and posted >= CUTOFF:
                    out.append(job("Company site", x["text"], co.title(), (x.get("categories") or {}).get("location", "Remote"),
                                   posted, x["hostedUrl"]))
        except Exception as e:
            print(f"  lever {co} failed: {e}", file=sys.stderr)
    return out


def hacker_news():
    """Top-level replies in the latest 'Ask HN: Who is hiring?' thread that mention remote."""
    try:
        s = get("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&query=" +
                urllib.parse.quote("Who is hiring"))
        story = next(h for h in s["hits"] if h["title"].startswith("Ask HN: Who is hiring"))
        data = get(f"https://hn.algolia.com/api/v1/search_by_date?tags=comment,story_{story['objectID']}"
                   f"&numericFilters=created_at_i%3E{int(CUTOFF.timestamp())}&hitsPerPage=1000")
    except Exception as e:
        print(f"  hacker news failed: {e}", file=sys.stderr)
        return []
    out = []
    for h in data.get("hits", []):
        if str(h.get("parent_id")) != str(story["objectID"]):
            continue
        text = strip_html(h.get("comment_text"), 3000)
        first_line = text.split("  ")[0]
        parts = [p.strip() for p in first_line.split("|")]
        if len(parts) < 3 or not re.search(r"\bremote\b", first_line, re.I):
            continue  # free-form posts can't be parsed reliably
        role = next((p for p in parts[1:] if any(r.search(p) for _, r in CATEGORIES)), parts[1])
        loc = next((p for p in parts[1:] if re.search(r"remote", p, re.I)), "Remote")
        out.append(job("Hacker News", role, parts[0], loc, date_of(h["created_at"]),
                       f"https://news.ycombinator.com/item?id={h['objectID']}"))
    return out


# ---------- classification ----------

def classify(j):
    if j["source"] == "Jobicy" and " | " in j["title"]:
        j["title"] = j["title"].split(" | ")[0]  # Jobicy repeats the location in the title
    t = j["title"]
    if EXCLUDE.search(t):
        return None
    j["category"] = next((name for name, rx in CATEGORIES if rx.search(t)), None)
    if not j["category"]:
        return None
    # the title decides; the source's own level field only fills in when the title says nothing
    hint = j.pop("level_hint", "")
    j["seniority"] = (next((name for name, rx in SENIORITY if rx.search(t)), None)
                      or next((name for name, rx in SENIORITY if name != "Manager" and rx.search(hint)), "Mid / Not stated"))
    j["regions"] = [name for name, rx in REGIONS if rx.search(j["location"])] or ["See listing"]
    return j


def collect():
    raw = []
    for name, fn in [("himalayas", himalayas), ("jobicy", jobicy), ("remoteok", remoteok),
                     ("company boards", company_boards), ("hacker news", hacker_news if CFG["hacker_news"] else list)]:
        got = fn()
        print(f"{name}: {len(got)} fresh", file=sys.stderr)
        raw += got
    jobs, keys = [], set()
    for j in sorted(raw, key=lambda j: j["posted"], reverse=True):
        if not j["url"] or not classify(j):
            continue
        k = f"{norm(j['company'])}|{norm(j['title'])}"
        if k in keys or j["url"] in keys:
            continue
        keys.update({k, j["url"]})
        jobs.append(j)
    return jobs


# ---------- Notion ----------

def notion(method, path, body=None, tries=4):
    for attempt in range(tries):
        req = urllib.request.Request(
            f"https://api.notion.com/v1/{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
                     "Notion-Version": "2022-06-28", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(float(e.headers.get("Retry-After") or 2 ** attempt))
                continue
            raise SystemExit(f"Notion {method} {path} -> {e.code}: {e.read().decode()[:300]}")


def select(names_colors):
    return {"select": {"options": [{"name": n, "color": c} for n, c in names_colors]}}


def setup(parent):
    cats = [(c["name"], c["color"]) for c in CFG["categories"]]
    props = {
        "Role": {"title": {}},
        "Company": {"rich_text": {}},
        "Category": select(cats),
        "Seniority": select([("Intern", "gray"), ("Junior", "green"), ("Mid / Not stated", "default"),
                             ("Senior", "blue"), ("Lead / Staff+", "purple"), ("Manager", "orange")]),
        "Open to": {"multi_select": {"options": [{"name": n, "color": c} for n, c in [
            ("Worldwide", "green"), ("Africa & Middle East", "yellow"), ("Europe", "blue"),
            ("Americas", "red"), ("Asia-Pacific", "purple"), ("See listing", "gray")]]}},
        "Location": {"rich_text": {}},
        "Salary": {"rich_text": {}},
        "Posted": {"date": {}},
        "Apply": {"url": {}},
        "Source": select([("Himalayas", "blue"), ("Jobicy", "green"), ("Remote OK", "red"),
                          ("Company site", "purple"), ("Hacker News", "orange")]),
        "Source page": {"url": {}},
        "Added": {"created_time": {}},
    }
    db = notion("POST", "databases", {
        "parent": {"type": "page_id", "page_id": parent},
        "title": [{"text": {"content": "Remote Tech Jobs"}}],
        "description": [{"text": {"content": "Fresh remote roles in AI/ML, data, software, DevOps and security, "
                                              "updated daily. Listings via Himalayas, Jobicy, Remote OK, Hacker News "
                                              "and company career pages: apply on the original posting."}}],
        "is_inline": True,
        "properties": props,
    })
    print(f"Created database {db['id']}\nAdd it as a secret: NOTION_DATABASE_ID={db['id'].replace('-', '')}")


def existing_rows(db):
    rows, cursor = [], None
    while True:
        res = notion("POST", f"databases/{db}/query",
                     {"page_size": 100, **({"start_cursor": cursor} if cursor else {})})
        rows += res["results"]
        if not res.get("has_more"):
            return rows
        cursor = res["next_cursor"]


def text_of(prop):
    return "".join(i.get("plain_text", "") for i in prop.get("title") or prop.get("rich_text") or [])


def rt(v):
    return {"rich_text": [{"text": {"content": (v or "")[:1900]}}]} if v else {"rich_text": []}


def push(jobs, db):
    rows = existing_rows(db)
    seen = set()
    for r in rows:
        p = r["properties"]
        seen.add(f"{norm(text_of(p['Company']))}|{norm(text_of(p['Role']))}")
        if p["Apply"].get("url"):
            seen.add(p["Apply"]["url"])

    new = [j for j in jobs if j["url"] not in seen and f"{norm(j['company'])}|{norm(j['title'])}" not in seen]
    new = new[: CFG["max_new_per_run"]]
    for j in reversed(new):  # oldest first, so the newest rows are created last
        notion("POST", "pages", {"parent": {"database_id": db}, "properties": {
            "Role": {"title": [{"text": {"content": j["title"][:200]}}]},
            "Company": rt(j["company"]),
            "Category": {"select": {"name": j["category"]}},
            "Seniority": {"select": {"name": j["seniority"]}},
            "Open to": {"multi_select": [{"name": r} for r in j["regions"]]},
            "Location": rt(j["location"]),
            "Salary": rt(j["salary"]),
            "Posted": {"date": {"start": j["posted"].date().isoformat()}},
            "Apply": {"url": j["url"]},
            "Source": {"select": {"name": j["source"]}},
            "Source page": {"url": j["listing"]},
        }})
        time.sleep(0.35)  # Notion allows ~3 requests/second

    # archive rows posted more than keep_days ago, so the board stays fresh and fast
    old = NOW - timedelta(days=CFG["keep_days"])
    pruned = 0
    for r in rows:
        posted = date_of((r["properties"]["Posted"].get("date") or {}).get("start"))
        if posted and posted < old:
            notion("PATCH", f"pages/{r['id']}", {"archived": True})
            pruned += 1
            time.sleep(0.35)
    print(f"Added {len(new)} new jobs, archived {pruned} older than {CFG['keep_days']} days. "
          f"Board now has {len(rows) + len(new) - pruned} jobs.")


def dump(jobs):
    for j in jobs:
        j["posted"] = j["posted"].date().isoformat()
    (HERE / "jobs.json").write_text(json.dumps(jobs, ensure_ascii=False, indent=1))
    for j in jobs:
        j["regions"] = ", ".join(j["regions"])
    cols = ["title", "company", "category", "seniority", "regions", "location", "salary", "posted", "source", "url", "listing"]
    with open(HERE / "jobs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(jobs)
    by = {}
    for j in jobs:
        by[j["category"]] = by.get(j["category"], 0) + 1
    print(f"{len(jobs)} jobs -> jobs.json / jobs.csv")
    for k, v in sorted(by.items(), key=lambda x: -x[1]):
        print(f"  {v:4d}  {k}")


def load_dotenv():
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and not k.strip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def main():
    load_dotenv()
    args = sys.argv[1:]
    if args[:1] == ["setup"] and "--parent" in args:
        ref = args[args.index("--parent") + 1]
        # accepts a bare ID (with or without dashes) or a full page URL
        m = re.search(r"([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:[?#]|$)", ref)
        setup(m.group(1).replace("-", "") if m else ref)
    elif args[:1] == ["run"]:
        jobs = collect()
        if "--dry-run" in args:
            dump(jobs)
        else:
            if not os.environ.get("NOTION_TOKEN") or not os.environ.get("NOTION_DATABASE_ID"):
                sys.exit("Set NOTION_TOKEN and NOTION_DATABASE_ID (or use --dry-run).")
            push(jobs, os.environ["NOTION_DATABASE_ID"])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
