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
LinkedIn guest API ─┐                    ┌─→ read full posting ─┐
                    ├─→ normalize → dedup ┤                     ├─→ filter → Telegram
ATS boards ─────────┘                    └─→ score ─────────────┘
                                                    └─→ archive (radar-state branch)
```

State lives on an orphan `radar-state` branch, committed by the Actions bot.
GitHub counts contributions only from the default branch, so the automated
ledger commits never reach the owner's profile graph, and `main` stays limited
to real code changes.

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

Run the setup script and follow its prompts. It creates the connection,
discovers your chat id and stores both GitHub secrets for you:

```bash
python scripts/setup_telegram.py
```

Before running it, create the bot: message [@BotFather](https://t.me/BotFather)
on Telegram, send `/newbot`, and copy the token it replies with.

The workflow is already scheduled, so there is nothing else to enable. The first
24 hours are a silent warm-up while the backlog settles; alerts start after that.

**Optional: run it on time**

GitHub throttles scheduled workflows hard on public repositories. A `*/15`
schedule was measured firing every 50 minutes on average, with gaps up to 73,
which is how a job can be an hour old before it reaches you.

`trigger/` is a Cloudflare Worker that asks GitHub for a run every 10 minutes
through `workflow_dispatch`, which is not throttled that way. To deploy it:

```bash
cd trigger
npx wrangler secret put GITHUB_TOKEN   # fine-grained PAT, Actions: read+write
npx wrangler deploy
```

The token needs access to this repository only, with the **Actions** permission
set to read and write. Visiting the deployed worker's URL triggers a run
immediately, which is the quickest way to confirm it works.

## Local use

```bash
pip install -r requirements.txt

python -m src.main --dry-run              # print results, change nothing
python -m src.main --dry-run --explain    # show how each score was built
python -m src.main --test-notify          # send one sample card to Telegram
python -m src.main                        # full run: notify and write state
```

## Tuning

Open `config.yml`. Everything you are likely to change is in the block at the
top; the machinery below it rarely needs touching.

**Want jobs abroad** — flip one line:

```yaml
scopes:
  global: true
```

While it is false those queries are skipped entirely, so they cost no requests.

**Seniority** — `experience.max_years` drops jobs whose description demands more
years than you have, and `blocked_titles` drops titles that announce a level
above yours. Titles alone are not enough: a plain "Frontend Developer" was
measured demanding four years while an "Associate" posting asked for two, which
is why the real filter reads the body of the posting.

**Location** — `location.commutable` lists where you can physically work.
Remote jobs skip this check entirely; anything on-site or hybrid must be within
reach. Istanbul postings frequently name only a district ("Şişli", "Kartal")
and never the city, so districts are listed individually. A vague location such
as "Türkiye" is kept rather than guessed at.

**Roles and technologies** — `scoring.role` is what the job *is*; `scoring.bonus`
refines an already-relevant match. Role terms are matched in the title and at
half weight in the description, which is how postings titled "Software Engineer"
still surface when the body asks for Next.js.

**Too many notifications** — raise `scoring.notify_threshold`, or add the
offending term to `scoring.veto`.

**Missing jobs you wanted** — run `--dry-run --explain` to see the exact terms
behind every score and which filter rejected what.

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
