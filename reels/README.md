# Nellore Daily standalone Instagram publishing

The GitHub workflow runs hourly from 05:00–23:00 Asia/Kolkata and publishes at most one reviewed reel per run. Empty slots are skipped. GitHub may delay scheduled jobs. The workflow uses the repository's existing Meta access token and Instagram account ID secrets. Keep Meta access valid independently of ChatGPT.

Add an entry to queue.json on main only after reviewing the entire video, verifying its sources and date, confirming permission to publish, and checking that it has not already been posted. Use natural Telugu understood across Andhra Pradesh and Telangana, with simple familiar English where useful. Include Telugu narration, timed captions, relevant AI motion, an AI disclosure, and the channel follow line. Use an approved durable HTTPS MP4 host already allowlisted by publish.mjs. Do not use private links or put credentials in the queue.

```json
{
  "id": "unique-story-id",
  "approved": true,
  "videoUrl": "https://d2ol7oe51mr4n9.cloudfront.net/path/to/reviewed.mp4",
  "caption": "తెలుగు శీర్షిక. దృశ్యాలు ఏఐతో రూపొందించినవి.",
  "publishAfter": "2026-11-25T05:00:00+05:30",
  "expiresAt": "2026-11-26T05:00:00+05:30"
}
```

Set new publication and expiry times for each entry. Keep IDs unique and never reuse one for a new story. Run the workflow manually with live unchecked to validate the queue and verify the account. An entry marked pending-review in ledger.json needs manual reconciliation against Instagram before retrying. Maintain the ledger to preserve duplicate protection.

To stop, go to GitHub Actions → Nellore Daily standalone Instagram → … → Disable workflow. Cancel any in-progress run separately if needed. To resume, enable that workflow. Neither operation requires ChatGPT.

The workflow publishes completed media. Video generation and editorial review happen before queueing. The legacy workflow should stay disabled.
