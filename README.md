# PvP Reference Feed

A page built from the **PvP Reference Library** Notion database.

- **This week**: recent additions as cards, each with a 7-second muted gameplay loop cut from the game's Steam trailer.
- **Library**: every game in the database as a searchable, sortable text table.

Notion stays the source of truth. Star, edit and add games there; this page is read-only.

## How it updates

Every Monday at 05:00 UTC (12:00 Bangkok) the workflow:

1. reads the rows. It uses the Notion API if a `NOTION_TOKEN` secret is set, and otherwise the `data.json` snapshot in this repo;
2. fetches each recent addition's trailer from Steam and cuts a short loop with ffmpeg;
3. rebuilds `index.html` and deploys it to GitHub Pages.

The page and clips are rebuilt on every run, and clips are cached between runs.

To refresh by hand: **Actions → Build reference feed → Run workflow**.

## Going live with Notion (later)

1. A Notion workspace admin creates a read-only internal connection and shares the "PvP Reference Library" database with it.
2. Add its secret here: **Settings → Secrets and variables → Actions → New repository secret**, named `NOTION_TOKEN`.
3. The next run reads Notion directly, and `data.json` is ignored.

## Visibility

This repo is on a personal account, so the page is **public** to anyone with the link. The page carries a `noindex` tag, so search engines are asked not to list it. For a private page, move the repo to an organization on GitHub Enterprise Cloud and set **Settings → Pages → Visibility** to Private.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `FEED_DAYS` | `28` | How many days of additions the feed shows |
| `NOTION_DATABASE_ID` | the PvP Reference Library | Database to read |

To edit the layout, change `template.html`. To test locally, run `python3 build.py --no-clips`.
