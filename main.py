import os
import re
import io
import json
import time
import html
import urllib.parse
import asyncio
import random
import subprocess
from datetime import datetime, timezone, timedelta

import feedparser
import requests
import edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from google import genai
from google.genai import types

# --- CONFIGURATION ---
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
META_ACCESS_TOKEN = os.environ["META_ACCESS_TOKEN"]
FB_PAGE_ID = os.environ["FB_PAGE_ID"]
IG_USER_ID = os.environ["IG_USER_ID"]
PREVIEW_ONLY = os.environ.get("PREVIEW_ONLY", "false").lower() == "true"

IST = timezone(timedelta(hours=5, minutes=30))
HISTORY_FILE = "posted_history.json"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "te-IN,te;q=0.9,en-US;q=0.8,en;q=0.7",
}

CATEGORY_FEEDS = {
    "NELLORE CITY": [
        "https://www.eenadu.net/rss/andhra-pradesh.xml",
        "https://www.sakshi.com/rss/andhra-pradesh",
        "https://news.google.com/rss/search?q=Nellore+OR+%E0%B0%A8%E0%B1%86%E0%B0%B2%E0%B1%8D%E0%B0%B2%E0%B1%82%E0%B0%B0%E0%B1%81+when:1d&hl=te&gl=IN&ceid=IN:te",
    ],
    "JOBS ALERT": [
        "https://news.google.com/rss/search?q=APPSC+OR+DSC+OR+Andhra+Pradesh+jobs+when:1d&hl=te&gl=IN&ceid=IN:te",
        "https://news.google.com/rss/search?q=Andhra+Pradesh+govt+jobs+notification+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    ],
    "CRICKET": [
        "https://www.espncricinfo.com/rss/content/story/feeds/0.xml",
        "https://news.google.com/rss/search?q=India+cricket+OR+IPL+when:1d&hl=te&gl=IN&ceid=IN:te",
    ],
    "TOLLYWOOD": [
        "https://telugu.samayam.com/entertainment/rssfeeds/48332856.cms",
        "https://news.google.com/rss/search?q=Tollywood+movie+release+OR+trailer+when:1d&hl=te&gl=IN&ceid=IN:te",
    ],
    "TRENDING": [
        "https://news.google.com/rss/search?q=Gold+rate+today+Andhra+Pradesh+when:1d&hl=te&gl=IN&ceid=IN:te",
        "https://www.eenadu.net/rss/andhra-pradesh.xml",
    ],
}

POLITICS_BLOCKLIST = [
    "జగన్", "చంద్రబాబు", "పవన్ కళ్యాణ్", "లోకేష్", "వైసీపీ", "టీడీపీ", "జనసేన",
    "బీజేపీ", "కాంగ్రెస్", "ఎమ్మెల్యే", "ఎంపీ", "మంత్రి", "అసెంబ్లీ", "ఎన్నికలు",
    "jagan", "chandrababu", "pawan kalyan", "lokesh", "ysrcp", "tdp", "janasena",
    "bjp", "congress", "mla", "mp", "minister", "assembly", "election", "politics",
]

GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            pass
    return []


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-350:], f, ensure_ascii=False, indent=2)


def clean_html(raw_html):
    text = re.sub(r"<[^>]+>", " ", raw_html or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def contains_politics(text):
    lowered = text.lower()
    return any(word in lowered for word in POLITICS_BLOCKLIST)


def is_recent_entry(entry, max_age_hours=24):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return True
    try:
        return ((time.time() - time.mktime(parsed)) / 3600.0) <= max_age_hours
    except Exception:
        return True


def fetch_latest_news(history):
    articles = []
    seen_links = set(history)
    categories = list(CATEGORY_FEEDS.keys())
    slot = int(time.time() // 3600) % len(categories)
    ordered_cats = categories[slot:] + categories[:slot]

    for category in ordered_cats:
        for url in CATEGORY_FEEDS[category]:
            try:
                resp = requests.get(url, timeout=15, headers=BROWSER_HEADERS)
                feed = feedparser.parse(resp.content)
                for entry in feed.entries[:6]:
                    if not is_recent_entry(entry, max_age_hours=24):
                        continue
                    link = entry.get("link", "").strip()
                    title = clean_html(entry.get("title", ""))
                    summary = clean_html(entry.get("summary", ""))
                    if not link or not title or link in seen_links:
                        continue
                    if contains_politics(f"{title} {summary}"):
                        continue
                    seen_links.add(link)
                    articles.append({
                        "category": category,
                        "title": title,
                        "summary": summary[:350],
                        "published": entry.get("published", ""),
                        "link": link,
                    })
            except Exception as e:
                print(f"Feed warning ({category}): {e}")
    return articles[:20]


def generate_storyboard_with_gemini(articles):
    client = genai.Client(api_key=GEMINI_API_KEY)
    now_ist = datetime.now(IST).strftime("%B %d, %Y at %I:%M %p IST")
    preferred_cat = articles[0]["category"] if articles else "NELLORE CITY"

    prompt = f"""
You are the creative director for 'Nellore Daily' on Instagram Reels.
Current exact date & time: {now_ist}.
Review these fresh news items from today:
{json.dumps(articles, ensure_ascii=False)}

STRICT RULES:
1. ZERO POLITICS: Never pick political parties, leaders, speeches, or elections.
2. TOPIC: Prefer '{preferred_cat}' if a strong fresh story is available; otherwise pick the best fresh story on Nellore city updates/schemes/food, AP/Nellore Jobs, Cricket, Tollywood, or Gold/Silver & trending facts.
3. HOOK TITLE ("hook_te"):
   - Super punchy 2-to-3 word Telugu emotional hook shown big in the upper third (like "అదిరిపోయే శుభవార్త!" or "ఒక ఎమోషన్!").
4. NATURAL SPOKEN TELUGU VOICEOVER:
   - Write a warm, conversational 45-to-55 word Telugu script split into 4 scenes.
   - Use everyday spoken Nellore Telugu (e.g., "మన నెల్లూరు వాళ్లకి...", "మన ఆటో అన్నలకు...").
5. REAL VIDEO FOOTAGE & BACKUP VISUALS FOR EACH SCENE:
   - For each of the 4 scenes, provide:
     a) "video_query": A single simple English noun/topic keyword for stock footage search (e.g., "India", "traffic", "cricket", "gold", "students", "cooking", "ocean", "cinema", "temple").
     b) "visual_prompt": A vivid 9:16 vertical scene description in South India / Andhra Pradesh with clean open sky at the top third.
   - Make all 4 scenes visually distinct!
6. METADATA:
   - "category_badge": One of "NELLORE CITY", "JOBS ALERT", "CRICKET", "TOLLYWOOD", "TRENDING".
   - "caption": Detailed, helpful Instagram & Facebook caption in Telugu + English with emojis, bullet points, source credit, and hashtags.
   - "bgm_mood": One of "emotional", "breaking", "devotional", "general".

Return strictly valid JSON with keys:
"selected_link", "category_badge", "hook_te", "scenes" (array of 4 objects with "narration_te", "video_query", "visual_prompt"), "caption", "bgm_mood"
"""

    last_err = None
    for model_name in GEMINI_MODELS:
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.4,
                    ),
                )
                data = json.loads(response.text)
                if (
                    data.get("selected_link")
                    and isinstance(data.get("scenes"), list)
                    and len(data["scenes"]) >= 3
                ):
                    return data
            except Exception as e:
                last_err = e
                print(f"Gemini ({model_name}) attempt {attempt + 1} failed: {e}")
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"All Gemini models failed: {last_err}")


def search_archive_mp4(query, out_mp4, used_ids):
    """Fetches real public-domain MP4 video footage from Internet Archive (zero API key)."""
    try:
        r = requests.get(
            "https://archive.org/advancedsearch.php",
            params={
                "q": f"({query}) AND mediatype:(movies) AND format:(MPEG4)",
                "fl[]": ["identifier", "title"],
                "rows": "12",
                "output": "json",
            },
            headers=BROWSER_HEADERS,
            timeout=12,
        ).json()
        for doc in r.get("response", {}).get("docs", []):
            ident = doc.get("identifier")
            if not ident or ident in used_ids:
                continue
            meta = requests.get(f"https://archive.org/metadata/{ident}/files", timeout=10).json()
            for f in meta.get("result", []):
                name = f.get("name", "")
                size = int(f.get("size", 0) or 0)
                if name.lower().endswith(".mp4") and 250_000 < size < 18_000_000:
                    dl_url = f"https://archive.org/download/{ident}/{urllib.parse.quote(name)}"
                    raw_dl = f"tmp_{ident}.mp4"
                    vr = requests.get(dl_url, headers=BROWSER_HEADERS, timeout=25)
                    if vr.status_code == 200 and len(vr.content) > 250_000:
                        with open(raw_dl, "wb") as fp:
                            fp.write(vr.content)
                        subprocess.run(
                            ["ffmpeg", "-y", "-ss", "2", "-i", raw_dl, "-t", "8", "-an", "-c:v", "libx264", "-preset", "veryfast", out_mp4],
                            check=True,
                        )
                        used_ids.add(ident)
                        print(f"Downloaded real Archive.org MP4 ({query}): {out_mp4}")
                        return out_mp4
    except Exception as e:
        print(f"Archive.org video notice ({query}): {e}")
    return None


def search_wikimedia_mp4(keyword, out_mp4, used_ids):
    """Fetches real video footage from Wikimedia Commons using a single clean keyword."""
    single_kw = (keyword.split()[0] if keyword else "India").strip()
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": f"filetype:video {single_kw}",
                "gsrnamespace": "6",
                "gsrlimit": "15",
                "prop": "videoinfo|imageinfo",
                "iiprop": "url|size",
                "format": "json",
            },
            headers={"User-Agent": "NelloreDailyBot/7.0 (https://instagram.com/nelloredaily)"},
            timeout=15,
        ).json()
        for page_id, p in r.get("query", {}).get("pages", {}).items():
            vid_id = f"wm_{page_id}"
            if vid_id in used_ids:
                continue
            info = (p.get("videoinfo") or p.get("imageinfo") or [{}])[0]
            url = info.get("url", "")
            if url and any(url.lower().endswith(ext) for ext in (".mp4", ".webm", ".ogv")):
                raw_dl = f"tmp_{page_id}.video"
                vr = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
                if vr.status_code == 200 and len(vr.content) > 150_000:
                    with open(raw_dl, "wb") as f:
                        f.write(vr.content)
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", raw_dl, "-t", "8", "-an", "-c:v", "libx264", "-preset", "veryfast", out_mp4],
                        check=True,
                    )
                    used_ids.add(vid_id)
                    print(f"Downloaded real Wikimedia video ({single_kw}): {out_mp4}")
                    return out_mp4
    except Exception as e:
        print(f"Wikimedia video notice ({single_kw}): {e}")
    return None


def generate_imagen_motion_clip(prompt, out_mp4):
    """Unbreakable fallback: generates a 9:16 AI visual via Imagen 3 and turns it into a smooth moving clip."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    try:
        res = client.models.generate_images(
            model="imagen-3.0-generate-002",
            prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="9:16",
                output_mime_type="image/jpeg",
            ),
        )
        if res.generated_images:
            img_bytes = res.generated_images[0].image.image_bytes
            img_path = out_mp4.replace(".mp4", ".jpg")
            Image.open(io.BytesIO(img_bytes)).convert("RGB").save(img_path, quality=95)
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-t", "8", "-i", img_path,
                    "-vf", "scale=1180:2098,crop=1080:1920:'min(100, n*0.6)':'min(178, n*1.0)',format=yuv420p",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out_mp4,
                ],
                check=True,
            )
            print(f"Generated Imagen 9:16 motion clip fallback: {out_mp4}")
            return out_mp4
    except Exception as e:
        print(f"Imagen fallback notice: {e}")
    return None


def gather_scene_videos(scenes, category="NELLORE CITY"):
    video_paths = []
    used_ids = set()

    fallbacks = {
        "CRICKET": ["cricket", "stadium", "sports", "India"],
        "TOLLYWOOD": ["cinema", "theater", "festival", "India"],
        "JOBS ALERT": ["students", "university", "library", "office"],
        "TRENDING": ["gold", "market", "traffic", "India"],
        "NELLORE CITY": ["India", "temple", "river", "cooking"],
    }
    cat_fallbacks = fallbacks.get(category.upper(), fallbacks["NELLORE CITY"])

    for idx, scene in enumerate(scenes):
        out_mp4 = f"raw_clip_{idx}.mp4"
        q = scene.get("video_query", cat_fallbacks[idx % len(cat_fallbacks)])
        v_prompt = scene.get("visual_prompt", "Sunny street in Nellore Andhra Pradesh with clear blue sky, 9:16 vertical")

        picked = (
            search_archive_mp4(q, out_mp4, used_ids)
            or search_wikimedia_mp4(q, out_mp4, used_ids)
            or search_archive_mp4(cat_fallbacks[idx % len(cat_fallbacks)], out_mp4, used_ids)
            or search_wikimedia_mp4(cat_fallbacks[idx % len(cat_fallbacks)], out_mp4, used_ids)
            or generate_imagen_motion_clip(v_prompt, out_mp4)
        )

        if picked:
            video_paths.append(picked)
        elif video_paths:
            video_paths.append(video_paths[-1])
        else:
            raise RuntimeError("All video and image fallbacks failed.")

    return video_paths


def load_font(size, script="telugu"):
    if script == "english":
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    else:
        paths = [
            "/usr/share/fonts/truetype/noto/NotoSansTelugu-Bold.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansTelugu-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf",
        ]
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size, layout_engine=ImageFont.Layout.RAQM)
            except Exception:
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def draw_shaped_text(draw, xy, text, font, fill):
    try:
        draw.text(xy, text, fill=fill, font=font, features=["kern", "liga", "ccmp"])
    except Exception:
        draw.text(xy, text, fill=fill, font=font)


def render_transparent_overlay(hook_te, caption_words, highlight_idx, show_hook, out_png):
    canvas = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    for y in range(0, 520):
        alpha = int(115 * (1 - y / 520))
        draw.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))
    for y in range(1450, 1920):
        prog = (y - 1450) / 470
        alpha = int(145 * prog)
        draw.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))

    wm_font = load_font(25, "english")
    draw.ellipse([72, 86, 86, 100], fill=(250, 204, 21, 245))
    draw.text((98, 79), "NELLORE DAILY", fill=(255, 255, 255, 240), font=wm_font)

    if show_hook and hook_te:
        hook_font = load_font(74, "telugu")
        words = hook_te.split()
        lines, cur = [], ""
        for w in words:
            test = f"{cur} {w}".strip()
            if draw.textbbox((0, 0), test, font=hook_font)[2] <= 940:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)

        hy = 340
        for line in lines[:2]:
            bbox = draw.textbbox((0, 0), line, font=hook_font)
            hx = (1080 - (bbox[2] - bbox[0])) // 2
            draw_shaped_text(draw, (hx + 3, hy + 4), line, hook_font, (0, 0, 0, 165))
            draw_shaped_text(draw, (hx, hy), line, hook_font, (255, 255, 255, 255))
            hy += 96

    if caption_words:
        cap_font = load_font(44, "telugu")
        space_w = draw.textbbox((0, 0), " ", font=cap_font)[2]
        word_widths = [draw.textbbox((0, 0), w, font=cap_font)[2] for w in caption_words]
        total_w = sum(word_widths) + space_w * max(0, len(caption_words) - 1)

        pad_x, pad_y = 34, 18
        pill_w = min(980, total_w + pad_x * 2)
        pill_h = 84
        px1 = (1080 - pill_w) // 2
        py1 = 1595
        draw.rounded_rectangle(
            [px1, py1, px1 + pill_w, py1 + pill_h],
            radius=20,
            fill=(15, 18, 25, 175),
        )

        cx = (1080 - total_w) // 2
        cy = py1 + pad_y
        for idx, (word, ww) in enumerate(zip(caption_words, word_widths)):
            color = (250, 204, 21, 255) if idx == highlight_idx else (255, 255, 255, 250)
            draw_shaped_text(draw, (cx, cy), word, cap_font, color)
            cx += ww + space_w

    canvas.save(out_png)


async def generate_voice_with_word_timings(scenes, out_audio="voice.mp3"):
    full_script = " ".join(s["narration_te"].strip() for s in scenes)
    full_script = re.sub(r"#\S+", "", full_script)
    full_script = re.sub(r"[•|★☆🔥⚠️✅📍📅⏰🎬🏏💼👇]", " ", full_script)
    full_script = re.sub(r"\s+", " ", full_script).strip()

    communicate = edge_tts.Communicate(full_script, "te-IN-ShrutiNeural", rate="-3%")
    word_events = []
    with open(out_audio, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                word_events.append({
                    "text": chunk["text"],
                    "start": chunk["offset"] / 10_000_000.0,
                    "end": (chunk["offset"] + chunk["duration"]) / 10_000_000.0,
                })

    probe = subprocess.check_output(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", out_audio,
        ]
    ).decode().strip()
    total_dur = max(float(probe), 10.0)

    if len(word_events) < 5:
        words = full_script.split()
        step = total_dur / max(1, len(words))
        word_events = [
            {"text": w, "start": i * step, "end": (i + 1) * step}
            for i, w in enumerate(words)
        ]

    return total_dur, word_events


def build_real_video_reel(scenes, total_dur, word_events, video_paths, hook_te, bgm_path=None, out_video="reel.mp4"):
    num_scenes = len(video_paths)
    scene_dur = total_dur / num_scenes
    norm_clips = []

    for idx, vpath in enumerate(video_paths):
        norm_out = f"norm_{idx}.mp4"
        cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", vpath,
            "-t", f"{scene_dur:.2f}",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", norm_out,
        ]
        subprocess.run(cmd, check=True)
        norm_clips.append(norm_out)

    with open("concat.txt", "w") as cf:
        for nc in norm_clips:
            cf.write(f"file '{nc}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy", "base_video.mp4"],
        check=True,
    )

    ov_fps = 10
    total_ov_frames = int(total_dur * ov_fps) + 1
    hook_end_t = scene_dur * 2.0

    chunks = []
    for i in range(0, len(word_events), 4):
        group = word_events[i : i + 4]
        chunks.append({
            "words": [g["text"] for g in group],
            "starts": [g["start"] for g in group],
            "start_t": group[0]["start"],
            "end_t": group[-1]["end"] + 0.35,
        })

    os.makedirs("ov_frames", exist_ok=True)
    cache = {}
    for f_idx in range(total_ov_frames):
        t = f_idx / ov_fps
        show_hook = t <= hook_end_t
        active_chunk, active_word_idx = None, -1
        for c_idx, ch in enumerate(chunks):
            if ch["start_t"] <= t <= ch["end_t"]:
                active_chunk = c_idx
                for w_i, ws in enumerate(ch["starts"]):
                    if t >= ws:
                        active_word_idx = w_i
                break

        key = (show_hook, active_chunk, active_word_idx)
        if key not in cache:
            cached_png = f"ov_frames/cache_{int(show_hook)}_{active_chunk}_{active_word_idx}.png"
            words_list = chunks[active_chunk]["words"] if active_chunk is not None else []
            render_transparent_overlay(hook_te, words_list, active_word_idx, show_hook, cached_png)
            cache[key] = cached_png

        os.symlink(os.path.abspath(cache[key]), f"ov_frames/ov_{f_idx:05d}.png")

    if bgm_path and os.path.exists(bgm_path):
        cmd_final = [
            "ffmpeg", "-y",
            "-i", "base_video.mp4",
            "-framerate", str(ov_fps), "-i", "ov_frames/ov_%05d.png",
            "-i", "voice.mp3",
            "-stream_loop", "-1", "-i", bgm_path,
            "-filter_complex",
            "[0:v][1:v]overlay=0:0:shortest=1[vout];[3:a]volume=0.14[bgm];[2:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
            "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
            "-movflags", "+faststart", "-shortest", out_video,
        ]
    else:
        cmd_final = [
            "ffmpeg", "-y",
            "-i", "base_video.mp4",
            "-framerate", str(ov_fps), "-i", "ov_frames/ov_%05d.png",
            "-i", "voice.mp3",
            "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[vout]",
            "-map", "[vout]", "-map", "2:a",
            "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
            "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
            "-movflags", "+faststart", "-shortest", out_video,
        ]
    subprocess.run(cmd_final, check=True)


def pick_bgm(mood):
    if not os.path.isdir("bgm"):
        return None
    files = [f for f in os.listdir("bgm") if f.lower().endswith(".mp3")]
    if not files:
        return None
    for f in files:
        if mood and mood.lower() in f.lower():
            return os.path.join("bgm", f)
    return os.path.join("bgm", random.choice(files))


def publish_to_instagram(video_path, caption):
    r1 = requests.post(
        f"https://graph.facebook.com/v21.0/{IG_USER_ID}/media",
        data={
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption,
            "access_token": META_ACCESS_TOKEN,
        },
        timeout=30,
    ).json()
    creation_id = r1.get("id")
    upload_uri = r1.get("uri")
    if not creation_id or not upload_uri:
        raise RuntimeError(f"IG container error: {r1}")

    file_size = os.path.getsize(video_path)
    with open(video_path, "rb") as vf:
        up = requests.post(
            upload_uri,
            headers={
                "Authorization": f"OAuth {META_ACCESS_TOKEN}",
                "offset": "0",
                "file_size": str(file_size),
                "Content-Type": "application/octet-stream",
            },
            data=vf.read(),
            timeout=90,
        ).json()
    if not up.get("success"):
        raise RuntimeError(f"IG binary upload error: {up}")

    for _ in range(18):
        time.sleep(10)
        status = requests.get(
            f"https://graph.facebook.com/v21.0/{creation_id}",
            params={"fields": "status_code", "access_token": META_ACCESS_TOKEN},
            timeout=20,
        ).json()
        if status.get("status_code") == "FINISHED":
            break
        if status.get("status_code") == "ERROR":
            raise RuntimeError(f"IG processing failed: {status}")

    r2 = requests.post(
        f"https://graph.facebook.com/v21.0/{IG_USER_ID}/media_publish",
        data={"creation_id": creation_id, "access_token": META_ACCESS_TOKEN},
        timeout=30,
    ).json()
    if "id" not in r2:
        raise RuntimeError(f"IG publish error: {r2}")
    print("Instagram Reel Published:", r2)


def publish_to_facebook_page(video_path, caption):
    acct = requests.get(
        f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}",
        params={"fields": "access_token", "access_token": META_ACCESS_TOKEN},
        timeout=20,
    ).json()
    page_token = acct.get("access_token", META_ACCESS_TOKEN)
    with open(video_path, "rb") as vf:
        r = requests.post(
            f"https://graph-video.facebook.com/v21.0/{FB_PAGE_ID}/videos",
            data={"description": caption, "access_token": page_token},
            files={"source": vf},
            timeout=90,
        ).json()
    if "id" not in r:
        raise RuntimeError(f"FB publish error: {r}")
    print("Facebook Page Video Published:", r)


def main():
    history = load_history()
    articles = fetch_latest_news(history)
    if not articles:
        print("No fresh non-political articles found this hour. Exiting cleanly.")
        return

    story = generate_storyboard_with_gemini(articles)
    print("Selected Hook:", story.get("hook_te"))

    cat_badge = story.get("category_badge", "NELLORE CITY")
    video_paths = gather_scene_videos(story["scenes"], category=cat_badge)
    total_dur, word_events = asyncio.run(generate_voice_with_word_timings(story["scenes"]))
    bgm_file = pick_bgm(story.get("bgm_mood", "emotional"))

    build_real_video_reel(
        scenes=story["scenes"],
        total_dur=total_dur,
        word_events=word_events,
        video_paths=video_paths,
        hook_te=story.get("hook_te", "తాజా వార్త!"),
        bgm_path=bgm_file,
    )

    if PREVIEW_ONLY:
        print("PREVIEW_ONLY=true — download reel.mp4 from Artifacts to inspect!")
        return

    ig_ok, fb_ok = False, False
    try:
        publish_to_instagram("reel.mp4", story["caption"])
        ig_ok = True
    except Exception as e:
        print(f"Instagram publish warning: {e}")

    try:
        publish_to_facebook_page("reel.mp4", story["caption"])
        fb_ok = True
    except Exception as e:
        print(f"Facebook publish warning: {e}")

    if not ig_ok and not fb_ok:
        raise RuntimeError("Both Instagram and Facebook publishing failed.")

    history.append(story["selected_link"])
    save_history(history)
    print(f"Hourly Real-Video Reel completed! (IG={ig_ok}, FB={fb_ok})")


if __name__ == "__main__":
    main()
