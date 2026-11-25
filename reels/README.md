# Nellore Daily standalone Instagram publishing

GitHub Actions generates a 40-second Telugu reel from a fresh source and publishes it to @nelloredaily. The primary trigger is at seven minutes past each hour in India, and the second is at 22 minutes past. Both run around the clock. Once a run reaches the Instagram publish step, the workflow records the hour and the source in posted_history.json so a retry cannot publish a duplicate. Check the Actions log and Instagram before manually retrying an ambiguous publish response.

The workflow uses the repository's GEMINI_API_KEY, META_ACCESS_TOKEN and IG_USER_ID secrets, plus the optional PEXELS_API_KEY for stock footage. It uses Telugu narration and captioned graphics when stock footage is unavailable. GitHub briefly hosts the MP4 as a release asset for Meta to ingest. Actions also retains a rendered preview artifact for five days. No ChatGPT subscription is required.

In GitHub Actions, select **Nellore Daily standalone Instagram** and **Run workflow**. Leave live unchecked to generate a preview artifact. Enable live to generate and publish for the current hour. Use **Disable workflow** from the workflow menu to stop future runs. Cancel an in-progress run separately if needed. Keep the older **Nellore Daily News Reel Bot** workflow disabled to avoid competing publications.
