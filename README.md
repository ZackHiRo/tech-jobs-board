# Remote Tech Jobs Board (Notion + GitHub Actions)

A free, self-updating job board in Notion, plus an MCP server so you can search it from Claude. Every morning a GitHub Action pulls fresh **remote** roles in
AI & ML, data science, data engineering, analytics, software, DevOps and security, tags each one with a
**Category**, **Seniority** and the regions it is **Open to**, and adds the new ones to a Notion database
that anyone can filter. Jobs older than 30 days are archived automatically.

No servers, no API keys to buy, no LLM costs: classification is plain rules in `config.json`.

## Where the jobs come from

| Source | What it covers | Their terms (summary) |
|---|---|---|
| [Himalayas](https://himalayas.app/api) | Remote jobs, with country restrictions and salaries | Credit Himalayas with a visible link, use the `applicationLink` as the Apply link, don't pass jobs on to other job boards |
| [Jobicy](https://jobicy.com/jobs-rss-feed) | Remote jobs by industry | Credit Jobicy with a link; Apply links go to the Jobicy page |
| [Remote OK](https://remoteok.com/api) | Remote jobs | Link to the Remote OK page and name Remote OK as the source |
| Company career pages | Remote roles from Greenhouse, Ashby and Lever boards listed in `config.json` | Public job-board APIs that employers publish for this purpose |
| [Hacker News "Who is hiring"](https://news.ycombinator.com/submitted?id=whoishiring) | Monthly thread, remote posts in `Company \| Role \| Location` format | Public |

The board keeps a **Source** column and links to the original listing, which is what these terms ask for.
Sources deliberately **not** used: LinkedIn (scraping breaks its terms), Remotive (its terms forbid using
the listings to collect sign-ups or emails), and freehire.me (redistribution needs a paid API plan).
If you add a source, read its terms first.

## Set it up (about 10 minutes)

1. **Create the Notion integration.** Go to notion.so/profile/integrations → *New integration* (internal)
   → copy the token (`ntn_...`).
2. **Create the database.** Make an empty Notion page, open ••• → *Connections* → add your integration.
   Copy the page ID (the 32 characters at the end of its URL), then run:
   ```bash
   NOTION_TOKEN=ntn_... python3 board.py setup --parent <page id>
   ```
   It prints `NOTION_DATABASE_ID=...`.
   (Already have a board with the same columns? Just connect the integration to it and use its ID.)
3. **Use this repository as a template** on GitHub (or fork it), then go to *Settings → Secrets and
   variables → Actions* and add two secrets: `NOTION_TOKEN` and `NOTION_DATABASE_ID`.
4. **Run it once** from the *Actions* tab → *Update job board* → *Run workflow*. After that it runs every
   day at 06:00 UTC.
5. **Share it.** In Notion: *Share → Publish* to make the page public (read-only).

Try it locally without Notion: `python3 board.py run --dry-run` writes `jobs.json` and `jobs.csv`.
(On macOS with python.org Python, you may need `export SSL_CERT_FILE=$(python3 -m certifi)` first.)

## Use it from Claude (MCP server)

`mcp_server.py` lets Claude, Cursor or any MCP client search the same jobs in plain language:
*"Find junior data engineering jobs open to Africa & Middle East posted this week."*

It runs the same fetch-and-classify pipeline on your own machine, directly from the sources, so it needs no
Notion token or API key. Results are cached for 6 hours in `~/.cache/tech-jobs-board/`. The first search after
install takes about 45 seconds while the cache fills; after that, searches are instant.

| Tool | What it does |
|---|---|
| `search_jobs` | Filter by keywords, category, seniority, region (`open_to`) and posting age. Newest first, with apply links. |
| `list_filters` | Valid categories, seniority levels and regions, with job counts |
| `board_status` | When the data was fetched and how many jobs came from each source |
| `refresh_jobs` | Fetch the latest jobs now (at most every 3 hours) |

**Install:** `pip install -r requirements-mcp.txt` (Python 3.10+).

**Claude Desktop:** Settings → Developer → Edit Config, then add (use your own path):
```json
{
  "mcpServers": {
    "remote-tech-jobs": {
      "command": "python3",
      "args": ["/absolute/path/to/tech-jobs-board/mcp_server.py"]
    }
  }
}
```
Restart Claude Desktop. **Claude Code:** `claude mcp add remote-tech-jobs -- python3 /absolute/path/to/tech-jobs-board/mcp_server.py`

The same source terms apply: results carry the source and a link to the original listing, so keep them when you
show results to others.

## Customise

Everything is in `config.json`:

- `categories`: name, Notion colour and a title regex for each category. First match wins, so order matters.
- `title_exclude`: titles to drop (sales, recruiting, data-labelling gigs, product management...).
- `himalayas_queries`, `jobicy_industries`: what to search.
- `greenhouse`, `ashby`, `lever`: company board slugs (the part after `boards.greenhouse.io/`,
  `jobs.ashbyhq.com/`, `jobs.lever.co/`).
- `max_age_days` (only add jobs posted in the last N days), `keep_days` (archive after N days),
  `max_new_per_run`.

## Columns

Role · Company · Category · Seniority · Open to · Location · Salary · Posted · Apply · Source · Source page · Added

*Open to* is derived from the location text, so "Remote, United States" is tagged Americas only. Always
check the listing before applying.
