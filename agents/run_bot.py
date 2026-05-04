#!/usr/bin/env python3
"""
Core bot runner. Called by every GitHub Actions workflow.
Usage: python agents/run_bot.py --bot <bot-name>
"""

import argparse
import json
import os
import sys
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

import anthropic

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
AGENTS = ROOT / "agents"
LOGS = ROOT / "logs"
KNOWLEDGE = ROOT / "knowledge-base"

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def send_telegram(message: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Skipped — token or chat ID not configured.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        print("[TELEGRAM] Message sent.")
    except Exception as e:
        print(f"[TELEGRAM] Failed: {e}")
        log_error("telegram", str(e))


def log_error(bot_name: str, error: str) -> None:
    LOGS.mkdir(exist_ok=True)
    log_file = LOGS / f"{bot_name}-errors.log"
    with open(log_file, "a") as f:
        f.write(f"[{now_utc()}] {error}\n")


def read_file(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def read_json(path: Path) -> dict | list:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def write_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def call_claude(system_prompt: str, user_message: str, max_tokens: int = 4096) -> str:
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text
        except Exception as e:
            if attempt == 0:
                print(f"[CLAUDE] Attempt 1 failed: {e}. Retrying in 30s...")
                time.sleep(30)
            else:
                raise


def fetch_urls(urls: list[str]) -> list[dict]:
    results = []
    for url in urls:
        try:
            start = time.time()
            resp = requests.get(url, timeout=15, allow_redirects=True)
            elapsed = int((time.time() - start) * 1000)
            results.append({
                "url": url,
                "status_code": resp.status_code,
                "response_time_ms": elapsed,
                "content_snippet": resp.text[:2000],
                "ssl_valid": url.startswith("https"),
            })
        except Exception as e:
            results.append({"url": url, "error": str(e), "status_code": 0, "response_time_ms": 0})
    return results


# ─── BOT RUNNERS ─────────────────────────────────────────────────────────────

def run_watchdog():
    bot = "watchdog"
    print(f"[{bot.upper()}] Starting...")

    urls_file = DATA / "watchdog" / "urls.txt"
    last_scan_file = DATA / "watchdog" / "last-scan.json"
    prompt_file = AGENTS / "prompts" / "watchdog.md"

    urls = [u.strip() for u in read_file(urls_file).splitlines() if u.strip() and not u.startswith("#")]
    if not urls:
        msg = "[BOT 1: SITE WATCHDOG]\nNo URLs configured in data/watchdog/urls.txt"
        send_telegram(msg)
        return

    fetched = fetch_urls(urls)
    last_scan = read_json(last_scan_file)
    system_prompt = read_file(prompt_file)

    user_message = json.dumps({
        "urls": fetched,
        "last_scan": last_scan,
        "current_time": now_utc(),
    }, indent=2)

    raw = call_claude(system_prompt, user_message)

    try:
        start = raw.find("{")
        result = json.loads(raw[start:raw.rfind("}") + 1])
    except Exception:
        result = {"raw_output": raw, "scan_time": now_utc()}

    write_json(last_scan_file, result)

    alerts = result.get("alerts", [])
    summary = result.get("summary", "Scan complete.")
    tg_msg = f"[BOT 1: SITE WATCHDOG]\nPages Checked: {len(urls)}\nAlerts: {len(alerts)}\n{summary}"
    send_telegram(tg_msg)
    print(f"[{bot.upper()}] Done. Alerts: {len(alerts)}")


def run_reddit_scout():
    bot = "scout"
    print(f"[{bot.upper()}] Starting...")

    prompt_file = AGENTS / "prompts" / "reddit-scout.md"
    brand_rules = read_file(KNOWLEDGE / "brand-rules.md")
    system_prompt = read_file(prompt_file)

    reddit_token = os.environ.get("REDDIT_CLIENT_ID", "")
    reddit_secret = os.environ.get("REDDIT_SECRET", "")

    threads = []
    if reddit_token and reddit_secret:
        threads = fetch_reddit_threads(reddit_token, reddit_secret)
    else:
        threads = [{"note": "Reddit API not configured. Add REDDIT_CLIENT_ID and REDDIT_SECRET to GitHub Secrets."}]

    user_message = json.dumps({
        "threads": threads,
        "brand_rules": brand_rules,
        "current_time": now_utc(),
    }, indent=2)

    raw = call_claude(system_prompt, user_message)

    drafts_dir = DATA / "scout" / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    try:
        start = raw.find("[")
        results = json.loads(raw[start:raw.rfind("]") + 1])
        draft_count = 0
        for item in results:
            if item.get("draft_reply") and item.get("fit_score", 0) >= 6:
                fname = f"{now_utc()[:10]}-{item['thread_id'][:8]}.md"
                (drafts_dir / fname).write_text(
                    f"# Draft Reply\n\n**Thread:** {item['thread_title']}\n**URL:** {item['thread_url']}\n**Subreddit:** {item['subreddit']}\n**Fit Score:** {item['fit_score']}/10\n\n---\n\n{item['draft_reply']}\n",
                    encoding="utf-8",
                )
                draft_count += 1
    except Exception:
        draft_count = 0
        (drafts_dir / f"{now_utc()[:10]}-raw.md").write_text(raw, encoding="utf-8")

    tg_msg = f"[BOT 2: REDDIT SCOUT]\nThreads Scanned: {len(threads)}\nDrafts Created: {draft_count}\nAction: Review data/scout/drafts/"
    send_telegram(tg_msg)
    print(f"[{bot.upper()}] Done. Drafts: {draft_count}")


def fetch_reddit_threads(client_id: str, secret: str) -> list[dict]:
    subreddits = ["SaaS", "marketing", "entrepreneur", "smallbusiness", "startups"]
    keywords = ["alternative to", "best tool for", "recommend a", "looking for software", "what do you use for"]
    threads = []
    try:
        auth = requests.auth.HTTPBasicAuth(client_id, secret)
        headers = {"User-Agent": "SEOWarRoom/1.0"}
        token_resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=auth, headers=headers,
            data={"grant_type": "client_credentials"}, timeout=10,
        )
        token = token_resp.json().get("access_token", "")
        if not token:
            return []

        api_headers = {"Authorization": f"Bearer {token}", "User-Agent": "SEOWarRoom/1.0"}
        for sub in subreddits:
            resp = requests.get(
                f"https://oauth.reddit.com/r/{sub}/new.json?limit=25",
                headers=api_headers, timeout=10,
            )
            for post in resp.json().get("data", {}).get("children", []):
                p = post["data"]
                text = (p.get("title", "") + " " + p.get("selftext", "")).lower()
                if any(kw in text for kw in keywords):
                    threads.append({
                        "thread_id": p["id"],
                        "subreddit": f"r/{sub}",
                        "title": p["title"],
                        "body": p.get("selftext", "")[:500],
                        "url": f"https://reddit.com{p['permalink']}",
                        "upvotes": p.get("ups", 0),
                    })
    except Exception as e:
        log_error("scout", str(e))
    return threads


def run_citation_tracker():
    bot = "citation"
    print(f"[{bot.upper()}] Starting...")

    queries_file = DATA / "citation" / "queries.txt"
    scorecard_file = DATA / "citation" / "scorecard.json"
    prompt_file = AGENTS / "prompts" / "citation-tracker.md"

    queries = [q.strip() for q in read_file(queries_file).splitlines() if q.strip() and not q.startswith("#")]
    brand_name = os.environ.get("BRAND_NAME", "Your Brand")
    system_prompt = read_file(prompt_file)

    perplexity_key = os.environ.get("PERPLEXITY_API_KEY", "")
    ai_responses = {}

    for query in queries[:10]:
        if perplexity_key:
            try:
                resp = requests.post(
                    "https://api.perplexity.ai/chat/completions",
                    headers={"Authorization": f"Bearer {perplexity_key}", "Content-Type": "application/json"},
                    json={"model": "llama-3.1-sonar-large-128k-online", "messages": [{"role": "user", "content": query}]},
                    timeout=20,
                )
                ai_responses[query] = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            except Exception as e:
                log_error(bot, f"Perplexity error for '{query}': {e}")
                ai_responses[query] = ""
        else:
            ai_responses[query] = f"[Perplexity API not configured for query: {query}]"

    user_message = json.dumps({
        "queries": queries,
        "ai_responses": ai_responses,
        "brand_name": brand_name,
        "last_scorecard": read_json(scorecard_file),
        "current_time": now_utc(),
    }, indent=2)

    raw = call_claude(system_prompt, user_message)

    try:
        start = raw.find("{")
        result = json.loads(raw[start:raw.rfind("}") + 1])
    except Exception:
        result = {"raw_output": raw, "run_time": now_utc()}

    scorecard = read_json(scorecard_file) if isinstance(read_json(scorecard_file), list) else []
    scorecard.append(result)
    write_json(scorecard_file, scorecard[-90:])

    geo = result.get("geo_score", {})
    rate = geo.get("citation_rate_percent", 0)
    tg_msg = (
        f"[BOT 3: CITATION TRACKER]\n"
        f"Queries Run: {len(queries)}\n"
        f"Brand Cited: {geo.get('brand_mentioned_count', 0)}/{geo.get('total_queries', len(queries))} ({rate}%)\n"
        f"Trend: {geo.get('trend', 'N/A').upper()}"
    )
    send_telegram(tg_msg)
    print(f"[{bot.upper()}] Done. Citation rate: {rate}%")


def run_x_agent():
    bot = "x-agent"
    print(f"[{bot.upper()}] Starting...")

    prompt_file = AGENTS / "prompts" / "x-engagement.md"
    brand_rules = read_file(KNOWLEDGE / "brand-rules.md")
    system_prompt = read_file(prompt_file)
    twitter_bearer = os.environ.get("TWITTER_BEARER_TOKEN", "")

    tweets = []
    if twitter_bearer:
        tweets = fetch_tweets(twitter_bearer)
    else:
        tweets = [{"note": "Twitter Bearer Token not configured. Add TWITTER_BEARER_TOKEN to GitHub Secrets."}]

    user_message = json.dumps({
        "tweets": tweets,
        "brand_rules": brand_rules,
        "current_time": now_utc(),
    }, indent=2)

    raw = call_claude(system_prompt, user_message)

    drafts_dir = DATA / "x-agent" / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    draft_count = 0
    try:
        start = raw.find("{")
        result = json.loads(raw[start:raw.rfind("}") + 1])
        for draft in result.get("drafts", []):
            if draft.get("fit_score", 0) >= 6 and draft.get("draft_reply"):
                fname = f"{now_utc()[:10]}-{draft['tweet_id'][:8]}.md"
                (drafts_dir / fname).write_text(
                    f"# X Draft Reply\n\n**Tweet:** {draft['tweet_text']}\n**Author:** {draft['tweet_author']}\n**URL:** {draft['tweet_url']}\n**Fit Score:** {draft['fit_score']}/10\n\n---\n\n{draft['draft_reply']}\n",
                    encoding="utf-8",
                )
                draft_count += 1

        trending = result.get("trending_topics", [])
        if trending:
            trending_file = DATA / "x-agent" / "trending.md"
            content = f"# Trending Topics — {now_utc()[:10]}\n\n"
            for t in trending:
                content += f"## {t['topic']}\n- Frequency: {t['frequency']}\n- Sentiment: {t['sentiment']}\n- Angle: {t['content_angle']}\n\n"
            trending_file.write_text(content, encoding="utf-8")
    except Exception:
        (drafts_dir / f"{now_utc()[:10]}-raw.md").write_text(raw, encoding="utf-8")

    tg_msg = f"[BOT 4: X ENGAGEMENT AGENT]\nTweets Scanned: {len(tweets)}\nDrafts Created: {draft_count}\nAction: Review data/x-agent/drafts/"
    send_telegram(tg_msg)
    print(f"[{bot.upper()}] Done. Drafts: {draft_count}")


def fetch_tweets(bearer_token: str) -> list[dict]:
    keywords = ["#SEO", "#GEO", "#AISearch", "need a tool that", "looking for seo", "ai overviews"]
    tweets = []
    headers = {"Authorization": f"Bearer {bearer_token}"}
    for kw in keywords[:3]:
        try:
            resp = requests.get(
                "https://api.twitter.com/2/tweets/search/recent",
                headers=headers,
                params={"query": f"{kw} -is:retweet lang:en", "max_results": 10, "tweet.fields": "public_metrics,author_id"},
                timeout=10,
            )
            for tweet in resp.json().get("data", []):
                m = tweet.get("public_metrics", {})
                tweets.append({
                    "tweet_id": tweet["id"],
                    "tweet_text": tweet["text"],
                    "tweet_author": tweet.get("author_id", "unknown"),
                    "tweet_url": f"https://x.com/i/web/status/{tweet['id']}",
                    "likes": m.get("like_count", 0),
                    "replies": m.get("reply_count", 0),
                })
        except Exception as e:
            log_error("x-agent", str(e))
    return tweets


def run_content_gap():
    bot = "content-gap"
    print(f"[{bot.upper()}] Starting...")

    our_content_file = DATA / "content-gap" / "our-content.json"
    scorecard_file = DATA / "citation" / "scorecard.json"
    gaps_file = DATA / "content-gap" / "gaps.md"
    prompt_file = AGENTS / "prompts" / "content-gap.md"
    system_prompt = read_file(prompt_file)

    our_content = read_json(our_content_file)
    scorecard = read_json(scorecard_file)
    recent_scorecard = scorecard[-10:] if isinstance(scorecard, list) else []

    user_message = json.dumps({
        "our_content": our_content,
        "citation_scorecard": recent_scorecard,
        "competitor_sources": [],
        "current_time": now_utc(),
    }, indent=2)

    raw = call_claude(system_prompt, user_message, max_tokens=6000)

    try:
        start = raw.find("{")
        result = json.loads(raw[start:raw.rfind("}") + 1])
        gaps = result.get("gaps", [])
        gap_md = f"# Content Gaps — Updated {now_utc()[:10]}\n\n"
        for g in sorted(gaps, key=lambda x: x.get("opportunity_score", 0), reverse=True):
            gap_md += f"## {g['topic']}\n- AI Citation Frequency: {g.get('cited_by_ai', 'N/A')}/10\n- Competitor Coverage: {g.get('competitor_coverage', 'N/A')}/5\n- Opportunity Score: **{g.get('opportunity_score', 'N/A')}**\n- Recommended Type: {g.get('recommended_content_type', 'N/A')}\n\n"
        gaps_file.write_text(gap_md, encoding="utf-8")

        total_gaps = result.get("total_gaps_found", len(gaps))
        coverage = result.get("our_content_coverage_pct", 0)
        top = gaps[0]["topic"] if gaps else "N/A"
        tg_msg = f"[BOT 5: CONTENT GAP HUNTER]\nGaps Found: {total_gaps}\nOur Coverage: {coverage}%\nTop Gap: \"{top}\"\nAction: Review data/content-gap/gaps.md"
    except Exception:
        gaps_file.write_text(raw, encoding="utf-8")
        tg_msg = "[BOT 5: CONTENT GAP HUNTER]\nRun complete. Review data/content-gap/gaps.md"

    send_telegram(tg_msg)
    print(f"[{bot.upper()}] Done.")


def run_review_sentinel():
    bot = "sentinel"
    print(f"[{bot.upper()}] Starting...")

    prompt_file = AGENTS / "prompts" / "review-sentinel.md"
    brand_rules = read_file(KNOWLEDGE / "brand-rules.md")
    brand_name = os.environ.get("BRAND_NAME", "Your Brand")
    system_prompt = read_file(prompt_file)

    reviews = [{"note": "Connect Google Business API or Yelp API via GOOGLE_BUSINESS_API_KEY / YELP_API_KEY secrets."}]

    user_message = json.dumps({
        "new_reviews": reviews,
        "brand_name": brand_name,
        "brand_rules": brand_rules,
        "last_check_time": now_utc(),
        "current_time": now_utc(),
    }, indent=2)

    raw = call_claude(system_prompt, user_message)

    drafts_dir = DATA / "sentinel" / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    urgent_alerts = []
    try:
        start = raw.find("{")
        result = json.loads(raw[start:raw.rfind("}") + 1])
        for draft in result.get("drafts", []):
            fname = f"{now_utc()[:10]}-{draft.get('platform','unknown')}-{draft.get('review_id','x')[:8]}.md"
            (drafts_dir / fname).write_text(
                f"# Review Reply Draft\n\n**Platform:** {draft.get('platform')}\n**Author:** {draft.get('author')}\n**Rating:** {draft.get('rating')}/5\n**Review:** {draft.get('review_text')}\n\n---\n\n**Draft Reply:**\n{draft.get('draft_reply')}\n",
                encoding="utf-8",
            )
            if draft.get("urgency") == "URGENT":
                urgent_alerts.append(draft)

        for alert in urgent_alerts:
            send_telegram(
                f"URGENT: NEW NEGATIVE REVIEW\nPlatform: {alert['platform']}\nRating: {alert['rating']}/5\nAuthor: {alert['author']}\nPreview: \"{alert['review_text'][:80]}\"\nDraft ready in data/sentinel/drafts/"
            )

        total = result.get("reviews_processed", 0)
        tg_msg = f"[BOT 6: REVIEW SENTINEL]\nReviews Processed: {total}\nNegative: {len(urgent_alerts)}\nStatus: {'SEE ALERTS' if urgent_alerts else 'ALL CLEAR'}"
    except Exception:
        tg_msg = "[BOT 6: REVIEW SENTINEL]\nRun complete. Review data/sentinel/drafts/"

    send_telegram(tg_msg)
    print(f"[{bot.upper()}] Done.")


# ─── DISPATCH ─────────────────────────────────────────────────────────────────

BOTS = {
    "watchdog": run_watchdog,
    "reddit-scout": run_reddit_scout,
    "citation-tracker": run_citation_tracker,
    "x-agent": run_x_agent,
    "content-gap": run_content_gap,
    "review-sentinel": run_review_sentinel,
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot", required=True, choices=list(BOTS.keys()), help="Which bot to run")
    args = parser.parse_args()

    try:
        BOTS[args.bot]()
    except Exception as e:
        error_msg = f"[SEO WAR ROOM ERROR]\nBot: {args.bot}\nError: {str(e)}\nTime: {now_utc()}"
        print(error_msg, file=sys.stderr)
        log_error(args.bot, str(e))
        send_telegram(error_msg)
        sys.exit(1)
