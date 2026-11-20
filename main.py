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
from PIL import Image, ImageDraw, ImageFont
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
NUM_SHOTS = 10

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
4. NATURAL SPOKEN TELUGU VOICEOVER ("narration_te"):
   - Write ONE smooth, engaging, conversational 50-to-60 word Telugu voiceover script (around 18-22 seconds spoken).
   - Use everyday spoken Nellore Telugu (e.g., "మన నెల్లూరు వాళ్లకి...", "మన ఆటో అన్నలకు...").
5. EXACTLY 10 DIFFERENT VISUAL SHOT QUERIES ("shot_queries" array of 10 unique strings):
   - Provide 10 distinct 2-to-3 word English stock video/photo search phrases matching the story's scenes (e.g. ["Indian auto rickshaw", "Indian city traffic", "smiling Indian driver", "Indian rupee cash", "smartphone banking", "Nellore highway", "yellow rickshaw street", "golden hour city", "Indian commuters", "Andhra Pradesh road"]).
   - All 10 queries MUST be different!
6. METADATA:
   - "category_badge": One of "NELLORE CITY", "JOBS ALERT", "CRICKET", "TOLLYWOOD", "TRENDING".
   - "caption": Detailed, helpful Instagram & Facebook caption in Telugu + English with emojis, bullet points, source credit, and hashtags.
   - "bgm_mood": One of "emotional", "breaking", "devotional", "general".

Return strictly valid JSON with keys:
"selected_link", "category_badge", "hook_te", "narration_te", "shot_queries" (array of 10 unique strings), "caption", "bgm_mood"
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
                        temperature=0.45,
                    ),
                )
                data = json.loads(response.text)
                if (
                    data.get("selected_link")
                    and data.get("narration_te")
                    and isinstance(data.get("shot_queries"), list)
                    and len(data["shot_queries"]) >= 6
                ):
                    return data
            except Exception as e:
                last_err = e
                print(f"Gemini ({model_name}) attempt {attempt + 1} failed: {e}")
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"All Gemini models failed: {last_err}")


def fetch_mixkit_video_pool(queries, max_clips=10):
    """Downloads real HD stock MP4 video clips from Mixkit (zero API key, zero watermarks)."""
    found_urls = []
    for q in queries:
        if len(found_urls) >= max_clips * 2:
            break
        try:
            slug = urllib.parse.quote_plus(q.strip())
            r = requests.get(f"https://mixkit.co/free-stock-video/{slug}/", headers=BROWSER_HEADERS, timeout=12)
            for link in re.findall(r'https://assets\.mixkit\.co/videos/[^"\'\s]+\.mp4', r.text):
                if link not in found_urls:
                    found_urls.append(link)
        except Exception:
            continue

    downloaded = []
    for idx, vid_url in enumerate(found_urls):
        if len(downloaded) >= max_clips:
            break
        raw_mp4 = f"mixkit_raw_{idx}.mp4"
        try:
            vr = requests.get(vid_url, headers=BROWSER_HEADERS, timeout=20)
            if vr.status_code == 200 and len(vr.content) > 220_000:
                with open(raw_mp4, "wb") as f:
                    f.write(vr.content)
                norm_mp4 = f"clip_vid_{len(downloaded)}.mp4"
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-stream_loop", "-1", "-i", raw_mp4, "-t", "3.5",
                        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p",
                        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", norm_mp4,
                    ],
                    check=True,
                )
                downloaded.append(norm_mp4)
                print(f"Downloaded real Mixkit HD video clip #{len(downloaded)}: {norm_mp4}")
        except Exception:
            continue
    return downloaded


def fetch_distinct_photo_motion_pool(queries, needed):
    """Fetches distinct watermark-free photos from Wikimedia + Bing and animates each into a 9:16 motion clip."""
    candidate_urls = []
    for q in queries:
        if len(candidate_urls) >= needed * 3:
            break
        try:
            r = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": f"filetype:bitmap {q}",
                    "gsrnamespace": "6",
                    "gsrlimit": "8",
                    "prop": "imageinfo",
                    "iiprop": "url",
                    "iiurlwidth": "1280",
                    "format": "json",
                },
                headers={"User-Agent": "NelloreDailyBot/13.0 (https://instagram.com/nelloredaily)"},
                timeout=10,
            ).json()
            for p in r.get("query", {}).get("pages", {}).values():
                if p.get("imageinfo"):
                    u = p["imageinfo"][0].get("thumburl") or p["imageinfo"][0].get("url")
                    if u and u not in candidate_urls and any(u.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png")):
                        candidate_urls.append(u)
        except Exception:
            pass

        try:
            q_enc = urllib.parse.quote_plus(f"{q} photography")
            resp = requests.get(
                f"https://www.bing.com/images/search?q={q_enc}&qft=+filterui:imagesize-large+filterui:photo-photo",
                headers=BROWSER_HEADERS,
                timeout=10,
            )
            for u in re.findall(r'murl&quot;:&quot;(https?://[^&]+?\.(?:jpg|jpeg|png))', resp.text, re.I):
                if u not in candidate_urls:
                    candidate_urls.append(u)
        except Exception:
            pass

    motion_clips = []
    motion_filters = [
        "scale=1220:2168,crop=1080:1920:'min(140, n*1.8)':'min(248, n*3.0)',format=yuv420p",
        "scale=1220:2168,crop=1080:1920:'max(0, 140-n*1.8)':'max(0, 248-n*3.0)',format=yuv420p",
        "scale=1240:2204,crop=1080:1920:'min(160, n*2.0)':142,format=yuv420p",
        "scale=1240:2204,crop=1080:1920:'max(0, 160-n*2.0)':142,format=yuv420p",
    ]

    for u in candidate_urls:
        if len(motion_clips) >= needed:
            break
        try:
            vr = requests.get(u, headers=BROWSER_HEADERS, timeout=10)
            if vr.status_code == 200 and len(vr.content) > 22_000:
                img = Image.open(io.BytesIO(vr.content)).convert("RGB")
                if img.width >= 500 and img.height >= 350:
                    idx = len(motion_clips)
                    jpg_path = f"pool_img_{idx}.jpg"
                    mp4_path = f"pool_mot_{idx}.mp4"
                    img.save(jpg_path, quality=95)
                    vf = motion_filters[idx % len(motion_filters)]
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-t", "3.5",
                            "-i", jpg_path, "-vf", vf,
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", mp4_path,
                        ],
                        check=True,
                    )
                    motion_clips.append(mp4_path)
                    print(f"Created distinct motion shot #{len(motion_clips)}: {mp4_path}")
        except Exception:
            continue

    return motion_clips


def gather_ten_distinct_clips(shot_queries, category="NELLORE CITY"):
    """Collects 10 completely distinct, watermark-free clips (real HD Mixkit videos + unique motion shots)."""
    queries = [q for q in shot_queries if q]
    cat_defaults = {
        "CRICKET": ["cricket match", "stadium lights", "sports crowd", "cricket ball", "celebration"],
        "TOLLYWOOD": ["cinema projector", "movie theater", "city night lights", "festival crowd", "film camera"],
        "JOBS ALERT": ["job interview", "office laptop", "students studying", "corporate building", "graduation"],
        "TRENDING": ["gold jewelry", "gold coins", "indian market", "city traffic", "sunrise clouds"],
        "NELLORE CITY": ["indian street", "city traffic", "temple bells", "river sunset", "cooking food"],
    }
    queries.extend(cat_defaults.get(category.upper(), cat_defaults["NELLORE CITY"]))

    clips = fetch_mixkit_video_pool(queries, max_clips=NUM_SHOTS)
    if len(clips) < NUM_SHOTS:
        needed = NUM_SHOTS - len(clips)
        clips.extend(fetch_distinct_photo_motion_pool(queries, needed=needed))

    if not clips:
        raise RuntimeError("Could not collect any video or motion clips for the 10-shot reel.")

    while len(clips) < NUM_SHOTS:
        clips.append(clips[len(clips) % max(1, len(clips))])

    return clips[:NUM_SHOTS]


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

    for y in range(0, 560):
        alpha = int(115 * (1 - y / 560))
        draw.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))
    for y in range(1450, 1920):
        prog = (y - 1450) / 470
        alpha = int(150 * prog)
        draw.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))

    # 1. Top-Left Minimalist Watermark ('• NELLORE DAILY')
    wm_font = load_font(25, "english")
    draw.ellipse([72, 86, 86, 100], fill=(250, 204, 21, 245))
    draw.text((98, 79), "NELLORE DAILY", fill=(255, 255, 255, 240), font=wm_font)

    # 2. Big Bold Telugu Hook Headline in Upper Third
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

    # 3. Bottom Translucent Pill with Live Word-by-Word Yellow Highlight
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


async def generate_voice_with_word_timings(narration_te, out_audio="voice.mp3"):
    full_script = re.sub(r"#\S+", "", narration_te or "")
    full_script = re.sub(r"[•|★☆🔥⚠️✅📍📅⏰🎬🏏💼👇]", " ", full_script)
    full_script = re.sub(r"\s+", " ", full_script).strip()

    communicate = edge_tts.Communicate(full_script, "te-IN-ShrutiNeural", rate="-2%")
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


def build_ten_shot_fast_reel(total_dur, word_events, clip_paths, hook_te, bgm_path=None, out_video="reel.mp4"):
    """Joins 10 fast-cut clips (~1.8-2.2s per shot) with crisp cross-dissolves + live karaoke captions."""
    num_clips = len(clip_paths)
    trans_dur = 0.25
    shot_dur = (total_dur + trans_dur * (num_clips - 1)) / num_clips

    trimmed = []
    for idx, cpath in enumerate(clip_paths):
        t_out = f"trim_{idx}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-stream_loop", "-1", "-i", cpath,
                "-t", f"{shot_dur:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", t_out,
            ],
            check=True,
        )
        trimmed.append(t_out)

    inputs = []
    for t_clip in trimmed:
        inputs.extend(["-i", t_clip])

    transitions = ["fade", "dissolve", "smoothleft", "smoothright", "fadeblack"]
    filter_parts = []
    prev = "[0:v]"
    offset = shot_dur - trans_dur
    for i in range(1, num_clips):
        tr = transitions[(i - 1) % len(transitions)]
        out_lbl = f"[v{i}]" if i < num_clips - 1 else "[vmerged]"
        filter_parts.append(
            f"{prev}[{i}:v]xfade=transition={tr}:duration={trans_dur:.2f}:offset={max(0.3, offset):.2f}{out_lbl}"
        )
        prev = out_lbl
        offset += shot_dur - trans_dur

    subprocess.run(
        [
            "ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filter_parts),
            "-map", "[vmerged]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "base_video.mp4",
        ],
        check=True,
    )

    ov_fps = 10
    total_ov_frames = int(total_dur * ov_fps) + 1
    hook_end_t = min(5.5, total_dur * 0.35)

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
    clip_paths = gather_ten_distinct_clips(story["shot_queries"], category=cat_badge)
    total_dur, word_events = asyncio.run(generate_voice_with_word_timings(story["narration_te"]))
    bgm_file = pick_bgm(story.get("bgm_mood", "emotional"))

    build_ten_shot_fast_reel(
        total_dur=total_dur,
        word_events=word_events,
        clip_paths=clip_paths,
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
    print(f"Hourly 10-Shot Fast-Cut Reel completed! (IG={ig_ok}, FB={fb_ok})")


if __name__ == "__main__":
    main()
