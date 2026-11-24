# Nellore Daily 2.0

Telugu-first, quality-gated Nellore district news and recruitment. Every eligible run checks sources even when the daily publishing limit has been reached. Empty runs are expected.

## Preview

The pull request workflow runs policy tests and renders a fictional Telugu story into a branded 1080×1350 JPEG and a narrated 1080×1920 MP4 with burned-in captions. It also uploads the SRT and social caption. The workflow receives no social tokens. Use Node.js 22+, Python 3.11+, FFmpeg, eSpeak NG, and Noto Sans Telugu with a RAQM-enabled Pillow build.

```sh
python -m pip install -r requirements.txt
node --test daily/policy.test.mjs
node daily/run.mjs --fixture
node daily/run.mjs --collect-only
node daily/run.mjs
```

Set `PYTHON` to the Python executable if required. Preview media is under `.nellore/`. Use `TELUGU_VOICE=edge` for the existing optional online Telugu voice; the default offline voice is more synthetic and works without credentials. The reel uses branded cards with captions timed to each narrated segment. Each publication sends the reel to both platforms and generates a separate poster for editorial use.

## Publication checks

- News requires two direct publisher feeds with matching normalized headlines, a Telugu headline with a Nellore district location, and timestamps within the previous 24 hours. Only the corroborated headline is used in the script. The bot may skip valid stories that use different headline wording. Different domains can still syndicate the same story.
- Jobs come from the [official district recruitment board](https://spsnellore.ap.gov.in/notice_category/recruitment/). A current listing must link to an official PDF with a matching explicit application deadline. Listings are skipped on the closing day because the cutoff time may be unclear. Eligibility is transcribed when available as a labeled line of text. Scanned PDFs, ambiguous dates, and unsupported notice formats need manual review.
- URL cleanup, legacy posted history, and headline similarity reduce repeat stories. The live ledger keeps pending and submitted channel results as well as uncertain outcomes for manual reconciliation. The limit is two stories per IST date.

## Scheduling and launch

Install the included systemd service and timer on an already-running Linux host or an existing free host that supports persistent storage. Place the checkout and virtual environment under `/opt/nellore-daily`, create the dedicated service user `nellore-daily`, and place both unit files in `/etc/systemd/system`. Run `systemd-analyze calendar '*-*-* 05..22:00,30:00 Asia/Kolkata' '*-*-* 23:00:00 Asia/Kolkata'` on that host to check the timer syntax, then run `systemctl daemon-reload` and `systemctl enable --now nellore-daily.timer`. The timer has 37 scheduled slots, starting at 05:00 and ending at 23:00 IST.

The checked-in service only runs preview. At authorized launch, set `NELLORE_LIVE=true`, `META_GRAPH_VERSION`, `IG_USER_ID`, `FB_PAGE_ID`, `META_IG_TOKEN`, and `META_PAGE_TOKEN` in a root-only `/etc/nellore-daily.env` and use a systemd override to append `--live` to the service command. Confirm the Instagram professional account and Facebook Page have granted the requisite publishing permissions. Reconcile uncertain Meta submissions and check for stale state locks before retries. The retained legacy `main.py` is superseded by the new runner.

The default pipeline uses offline TTS and local rendering with no metered content generation or upload storage. The incremental software and API budget is ₹0 per month on an existing host with available capacity. Keep any incremental hosting and connectivity cost within ₹300 per month before enabling live operation. This change does not provision a host or purchase services.

