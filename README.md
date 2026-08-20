# Job Radar

Watches LinkedIn and public ATS job boards for new developer postings and sends
scored cards to Telegram. Runs on GitHub Actions, costs nothing, and never
touches your LinkedIn account.

## Why this exists

Refreshing LinkedIn manually does not work, and not because you are not looking
often enough:

- **LinkedIn indexes new postings 18-48 hours late.** The "just posted" label
  reflects when LinkedIn's crawler found the job, not when the company
  published it. No amount of refreshing beats that delay.
- **Search results are ranked by relevance, not date.** Promoted posts take the
  top slots and push fresh listings down, which is why the same search returns
  different results ten minutes apart.
- **LinkedIn's own job alert emails are a once-daily digest** sent at a fixed
  hour, so they are already stale on arrival.

This project attacks all three. It queries LinkedIn with `sortBy=DD` so results
come back by date with no personalisation, and it reads company ATS boards
directly, which list a role one to three days before LinkedIn indexes it.

## How it works

```
LinkedIn guest API ─┐
                    ├─→ normalize → dedup → score → Telegram
ATS boards ─────────┘                   └─→ data/jobs.jsonl (archive)
```

**Sources**

- `linkedin` — the public `/jobs-guest/` endpoint LinkedIn serves to logged-out
  visitors for SEO and embedded widgets. No cookie, no session, no account.
  Because nothing is ever logged in, there is no account to restrict.
- `ats` — the documented public JSON endpoints of Greenhouse, Lever and Ashby,
  which companies use to embed their openings on their own sites.

**Scoring** is rule-based and lives entirely in `config.yml`:

| Tier | Meaning |
| --- | --- |
| `role` | What the job *is* (frontend, react, fullstack). The primary signal. |
| `bonus` | Refinements (remote, typescript). Only counted once a role matched. |
| `negative` | Counts against, but can be outweighed. |
| `veto` | Disqualifying. Cannot be outscored. |

Anything scoring below `notify_threshold` is still archived to
`data/jobs.jsonl` — it just does not buzz your phone.

## Setup

**1. Create a Telegram bot**

Message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the
token. Then message your own bot once and get your chat id:

```bash
curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
```

Look for `"chat":{"id":...}` in the response.

**2. Add repository secrets**

In *Settings → Secrets and variables → Actions*, add:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

**3. Enable the workflow**

The schedule starts automatically. Trigger the first run by hand from the
*Actions* tab so the ledger gets seeded.

The first run seeds silently: it records the existing backlog without notifying,
sends a single "radar armed" confirmation, and starts alerting from the next run.

## Local use

```bash
pip install -r requirements.txt

python -m src.main --dry-run              # print results, change nothing
python -m src.main --dry-run --explain    # show how each score was built
python -m src.main --test-notify          # send one sample card to Telegram
python -m src.main                        # full run: notify and write state
```

## Tuning

Everything lives in `config.yml`.

**Change what you are looking for** — edit `sources.linkedin.queries`. Each
entry is a keyword plus a `geo_id` (`102105699` is Turkey, `92000000` is
worldwide). The endpoint returns 10 results per page and stops past 40, so
coverage comes from several narrow queries rather than one broad one.

**Too many notifications** — raise `scoring.notify_threshold`, or add the
offending term to `scoring.veto`.

**Missing jobs you wanted** — check whether a `negative` or `veto` term is
catching them. `--dry-run --explain` prints the exact terms behind every score.

**Watch another company's ATS** — find its board URL (`jobs.lever.co/<slug>`,
`boards.greenhouse.io/<slug>`, `jobs.ashbyhq.com/<slug>`) and add the slug to
`sources.ats.companies`.

## Notes and limits

- **No auto-apply, by design.** The guest endpoint needs no cookie, so the bot
  never authenticates as you and there is no account to restrict. Automating
  Easy Apply would require your session cookie, breaches LinkedIn's User
  Agreement, and in 2026 leads to permanent account restrictions. Notification
  plus one click is nearly as fast and carries none of that risk.
- **Telegram over WhatsApp.** The Bot API is free with no per-message cost and
  no business verification. WhatsApp's Business API bills per conversation.
- **The guest endpoint is not a supported product.** It works today and is
  rate-limited rather than blocked, but LinkedIn could change it. The ATS
  sources are independent and keep working if it does.
- **Rate limiting is handled, not ignored.** Requests are paced with random
  delays under a per-run budget, and a `429`/`999` ends the run cleanly and
  sends a warning instead of failing silently.
