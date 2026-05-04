# HEARTBEAT.md — Schedule & Run Protocol

## WAKE UP ROUTINE

Read the current UTC time. Match it to the schedule tier below.
Execute all tasks assigned to the current tier.
Send the Telegram status report at the end of every run.

---

## SCHEDULE TIERS

### CRITICAL — Every 2 Hours
- **Bot 1: Site Watchdog** → Read `data/watchdog/urls.txt`. Check status, speed, schema. Diff against last scan. Alert on any 4xx/5xx.
- **Bot 4: X Engagement Agent** → Scan for urgent pain-point keywords. Draft replies. Save to `data/x-agent/drafts/`.

### HIGH — Every 4 Hours
- **Bot 2: Reddit Scout** → Scan target subreddits for buyer-intent threads. Draft replies. Save to `data/scout/drafts/`.

### MEDIUM — Every 6 Hours
- **Bot 6: Review Sentinel** → Check for new reviews on Google and Yelp. Draft replies. Alert on negative reviews.

### DAILY — Every 12 Hours
- **Bot 3: Citation Tracker** → Run all queries in `data/citation/queries.txt` through Perplexity and ChatGPT. Update `data/citation/scorecard.json`.
- **Bot 5: Content Gap Hunter** → Compare new citations to `data/content-gap/our-content.json`. Update `data/content-gap/gaps.md`.

---

## WEEKLY TASKS

| Day | Task |
|-----|------|
| Sunday | Bot 1: Generate full technical health report from scan history |
| Monday | Bot 3: Generate GEO scorecard summary — citation rate, top competitors cited, trending queries |
| Friday | Bot 5: Generate 3 content briefs from top-scoring gaps, save to `data/content-gap/briefs/` |
| Monthly (1st) | Bot 6: Full NAP consistency audit across all local directories |

---

## TELEGRAM REPORT FORMAT

Send this block to Telegram after **every** run:

```
[SEO WAR ROOM STATUS]
Time: {UTC datetime}
Bot(s) Run: {list of bots executed}
───────────────────────
Alerts: {NONE or list of urgent issues}
Drafts Pending Approval: {total count}
───────────────────────
Notes: {any anomaly or change from last run}
```

---

## FAILURE PROTOCOL

- If an API call fails, retry once after 30 seconds.
- If retry fails, log the error to `logs/{bot-name}-errors.log` and send a Telegram alert.
- Never skip the Telegram report — even if all tasks fail, send the failure summary.
- On partial failure, mark which bots succeeded and which failed in the report.
