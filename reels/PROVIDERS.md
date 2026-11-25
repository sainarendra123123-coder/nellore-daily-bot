# Reel generation preview

The preview uses five scene prompts and assembles five eight-second clips into one vertical MP4. Public Hugging Face Spaces are tried in order for each scene. A failed or unusable result advances to the next Space. The preview stops if all providers fail. The available public demos are quota-limited and their results require review. A GitHub-hosted runner does not provide a guaranteed GPU allocation.

A plan is a JSON object with these fields.

- `scenes` is an array of five objects. Each needs a `prompt`. An optional `clip` points to a reviewed AI-generated local MP4 representing that scene.
- `narration` points to a reviewed 25–40 second recording in conversational Telugu and everyday English.
- `captions` points to a UTF-8 SRT file timed to the narration.

Asset paths are relative to the plan file. Run `python3 reels/render_preview.py plan.json preview.mp4` on a host with FFmpeg, ffprobe and Noto Sans Telugu. The GitHub Actions workflow can also render a plan committed to its selected branch once the workflow is available for dispatch. Review the rendered video for topic accuracy, new footage, caption timing and pronunciation before submitting it to the existing publishing queue.

Google Flow exports can be used as plan clips. Flow web credits and Google Veo API billing are separate. ZSky's free tier is browser-based and its developer API is paid. Browser exports from other providers can be used if their terms allow the intended use. Hark's earlier tutorial was used as a storyboard reference.

The preview workflow has no Meta secrets and never publishes. Production use still needs reliable motion generation, audio review, and a durable public delivery URL accepted by the existing Instagram publisher.
