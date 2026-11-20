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
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "te-IN,te;q=0.9,en-US;q=0.8,en;q=0.7",
}

CATEGORY_FEEDS = {
    "NELLORE CITY": [
        "https://www.eenadu.net/rss/andhra-pradesh.xml",
        "https://www.sakshi.com/rss/andhra-pradesh",
        "https://news.google.com/rss/search?q=Nellore+city+OR+%E0%B0%A8%E0%B1%86%E0%B0%B2%E0%B1%8D%E0%B0%B2%E0%B1%82%E0%B0%B0%E0%B1%81+when:1d&hl=te&gl=IN&ceid=IN:te",
    ],
    "JOBS ALERT": [
        "https://news.google.com/rss/search?q=APPSC+OR+DSC+OR+Andhra+Pradesh+jobs+notification+when:1d&hl=te&gl=IN&ceid=IN:te",
        "https://news.google.com/rss/search?q=Andhra+Pradesh+govt+jobs+notification+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    ],
    "CRICKET": [
        "https://www.espncricinfo.com/rss/content/story/feeds/0.xml",
        "https://news.google.com/rss/search?q=India+cricket+OR+IPL+Telugu+when:1d&hl=te&gl=IN&ceid=IN:te",
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

GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
XFADE_TRANSITIONS = ["smoothleft", "dissolve", "wipeleft", "slideright"]


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


def extract_rss_media(entry):
    for key in ("media_content", "media_thumbnail"):
        media = entry.get(key)
        if isinstance(media, list) and media and media[0].get("url"):
            return media[0]["url"]
    for link in entry.get("links", []):
        if "image" in link.get("type", "") and link.get("href"):
            return link["href"]
    raw_desc = entry.get("summary", "") or entry.get("description", "")
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw_desc, re.I)
    if m and m.group(1).startswith("http"):
        return m.group(1)
    return ""


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
                        "rss_image": extract_rss_media(entry),
                    })
            except Exception as e:
                print(f"Feed warning ({category}): {e}")
    return articles[:20]


def generate_storyboard_with_gemini(articles):
    client = genai.Client(api_key=GEMINI_API_KEY)
    now_ist = datetime.now(IST).strftime("%B %d, %Y at %I:%M %p IST")
    preferred_cat = articles[0]["category"] if articles else "NELLORE CITY"

    prompt = f"""
You are the lead Telugu visual storyteller for 'Nellore Daily'.
Current exact date & time: {now_ist}.
Review these fresh news items from today:
{json.dumps(articles, ensure_ascii=False)}

STRICT RULES:
1. ZERO POLITICS: Never pick political parties, leaders, speeches, or elections.
2. TOPIC: Prefer '{preferred_cat}' if a strong fresh story is available; otherwise pick the best fresh story on Nellore city updates/offers, AP/Nellore Jobs, Cricket, Tollywood, or Gold/Silver & trending facts.
3. NATURAL SPOKEN TELUGU:
   - Write one smooth, conversational 45-to-55 word Telugu voiceover split into 4 scenes.
   - Use everyday spoken Telugu (friendly Nellore tone, simple clear words, no stiff dubbing Telugu).
   - Keep each scene's "subtitle_te" SHORT (4 to 7 Telugu words max) so it looks crisp and readable on mobile.
4. SPECIFIC PHOTO SEARCH QUERIES FOR EACH SCENE:
   - For each of the 4 scenes, give a clear 2-to-4 word English photo search phrase ("image_query") that directly matches that scene's visual (e.g., "Indian cricket stadium match", "Telugu cinema movie theatre", "Gold jewellery showroom India", "Nellore city road traffic", "Indian students exam hall").
   - Make all 4 "image_query" strings DIFFERENT so each scene shows a distinct visual!
5. METADATA:
   - "category_badge": One of "NELLORE CITY", "JOBS ALERT", "CRICKET", "TOLLYWOOD", "TRENDING".
   - "headline_te": Short 4-to-6 word Telugu headline.
   - "caption": Engaging Instagram & Facebook caption in Telugu + English with source credit and hashtags.
   - "bgm_mood": One of "breaking", "emotional", "devotional", "general".

Return strictly valid JSON with keys:
"selected_link", "category_badge", "headline_te", "scenes" (array of 4 objects with "narration_te", "subtitle_te", "image_query"), "caption", "bgm_mood"
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
                        temperature=0.35,
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
                time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"All Gemini models failed: {last_err}")


def extract_article_og_image(article_url):
    """Follows redirects and extracts the real news article cover image (og:image)."""
    if not article_url:
        return ""
    try:
        r = requests.get(article_url, headers=BROWSER_HEADERS, timeout=12, allow_redirects=True)
        html_text = r.text
        patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
        ]
        for pat in patterns:
            m = re.search(pat, html_text, re.I)
            if m:
                img_url = html.unescape(m.group(1)).strip()
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                if img_url.startswith("http") and "google.com" not in img_url and "gstatic.com" not in img_url:
                    return img_url
    except Exception as e:
        print(f"og:image extraction warning: {e}")
    return ""


def search_web_images(query, max_results=12):
    """Searches Wikimedia Commons + Bing Images for real, high-resolution photos."""
    urls = []
    short_q = " ".join(query.split()[:4])

    # 1. Wikimedia Commons bitmap search
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": f"filetype:bitmap {short_q}",
                "gsrnamespace": "6",
                "gsrlimit": str(max_results),
                "prop": "imageinfo",
                "iiprop": "url",
                "iiurlwidth": "1280",
                "format": "json",
            },
            headers={"User-Agent": "NelloreDailyBot/2.0 (https://github.com/sainarendra123123-coder)"},
            timeout=12,
        ).json()
        for p in r.get("query", {}).get("pages", {}).values():
            if p.get("imageinfo"):
                u = p["imageinfo"][0].get("thumburl") or p["imageinfo"][0].get("url")
                if u and any(u.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png")):
                    urls.append(u)
    except Exception as e:
        print(f"Wikimedia search warning: {e}")

    # 2. Bing Images high-res photo search
    try:
        q_enc = urllib.parse.quote_plus(f"{query} India photo")
        resp = requests.get(
            f"https://www.bing.com/images/search?q={q_enc}&qft=+filterui:imagesize-large+filterui:photo-photo",
            headers=BROWSER_HEADERS,
            timeout=12,
        )
        found = re.findall(r'murl&quot;:&quot;(https?://[^&]+?\.(?:jpg|jpeg|png))', resp.text, re.I)
        for u in found:
            if u not in urls:
                urls.append(u)
    except Exception as e:
        print(f"Bing image search warning: {e}")

    return urls[:max_results]


def download_verified_photo(url, out_path):
    try:
        r = requests.get(url, timeout=10, headers=BROWSER_HEADERS)
        if r.status_code == 200 and len(r.content) > 15000:
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            # Reject tiny icons, banners, or extreme aspect ratios
            if img.width >= 480 and img.height >= 320:
                aspect = img.width / float(img.height)
                if 0.4 <= aspect <= 2.6:
                    img.save(out_path, quality=95)
                    print(f"Downloaded photo ({img.width}x{img.height}): {out_path}")
                    return out_path
    except Exception:
        pass
    return None


def gather_scene_images(scenes, primary_article_image=""):
    image_paths = []
    used_urls = set()

    if primary_article_image:
        p = download_verified_photo(primary_article_image, "scene_raw_0.jpg")
        if p:
            image_paths.append(p)
            used_urls.add(primary_article_image)

    fallback_queries = [
        "Andhra Pradesh city landscape",
        "India news broadcast studio",
        "Indian street market crowd",
        "Hyderabad Nellore highway India",
    ]

    for idx, scene in enumerate(scenes):
        if len(image_paths) > idx:
            continue
        out_file = f"scene_raw_{idx}.jpg"
        query = scene.get("image_query", fallback_queries[idx % len(fallback_queries)])
        candidates = search_web_images(query)
        if len(candidates) < 3:
            candidates.extend(search_web_images(fallback_queries[idx % len(fallback_queries)]))

        picked = None
        for u in candidates:
            if u not in used_urls:
                picked = download_verified_photo(u, out_file)
                if picked:
                    used_urls.add(u)
                    break

        if picked:
            image_paths.append(picked)
        elif image_paths:
            # Reuse an earlier verified photo rather than creating a blank blue screen
            image_paths.append(image_paths[-1])
        else:
            img = Image.new("RGB", (1080, 1920), (18, 26, 46))
            img.save(out_file)
            image_paths.append(out_file)

    return image_paths[: len(scenes)]


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
                # Enable Raqm complex script shaping for Telugu vowels & conjuncts
                return ImageFont.truetype(p, size, layout_engine=ImageFont.Layout.RAQM)
            except Exception:
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def draw_shaped_text(draw, xy, text, font, fill):
    try:
        draw.text(xy, text, fill=fill, font=font, features=["kern", "liga", "ccmp"])
    except Exception:
        draw.text(xy, text, fill=fill, font=font)


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, current = [], ""
    for w in words:
        test = f"{current} {w}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


def compose_scene_frame(bg_img_path, badge, headline_te, subtitle_te, scene_idx, total_scenes, out_png):
    src = Image.open(bg_img_path).convert("RGB")
    src = ImageEnhance.Contrast(src).enhance(1.08)
    src = ImageEnhance.Color(src).enhance(1.16)
    src = ImageEnhance.Sharpness(src).enhance(1.15)

    # 1. Blurred atmospheric full-bleed background (prevents awkward side cropping)
    bg_ratio = max(1080 / src.width, 1920 / src.height)
    bg_resized = src.resize((int(src.width * bg_ratio), int(src.height * bg_ratio)), Image.LANCZOS)
    b_left = (bg_resized.width - 1080) // 2
    b_top = (bg_resized.height - 1920) // 2
    canvas = (
        bg_resized.crop((b_left, b_top, b_left + 1080, b_top + 1920))
        .filter(ImageFilter.GaussianBlur(radius=24))
        .convert("RGBA")
    )

    # Darken background slightly so foreground card pops
    dark_tint = Image.new("RGBA", (1080, 1920), (8, 12, 24, 115))
    canvas = Image.alpha_composite(canvas, dark_tint)

    # 2. Sharp, well-framed news photo card in the center-upper stage
    fg_w = 990
    fg_h = 840
    fg_ratio = max(fg_w / src.width, fg_h / src.height)
    fg_resized = src.resize((int(src.width * fg_ratio), int(src.height * fg_ratio)), Image.LANCZOS)
    f_left = (fg_resized.width - fg_w) // 2
    f_top = (fg_resized.height - fg_h) // 2
    fg_crop = fg_resized.crop((f_left, f_top, f_left + fg_w, f_top + fg_h)).convert("RGBA")

    # Rounded mask for the photo frame
    mask = Image.new("L", (fg_w, fg_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, fg_w, fg_h], radius=28, fill=255)
    canvas.paste(fg_crop, (45, 360), mask)

    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle([45, 360, 45 + fg_w, 360 + fg_h], radius=28, outline=(255, 255, 255, 90), width=3)

    # 3. Top Brand Header Bar
    draw.rounded_rectangle([45, 65, 1035, 155], radius=20, fill=(220, 38, 38, 245))
    te_brand_font = load_font(40, "telugu")
    en_brand_font = load_font(34, "english")
    draw_shaped_text(draw, (78, 86), "నెల్లూరు డైలీ", te_brand_font, (255, 255, 255, 255))
    draw.text((665, 91), "NELLORE DAILY", fill=(255, 255, 255, 255), font=en_brand_font)

    # 4. Category Pill & Scene Progress Bars
    badge_colors = {
        "NELLORE CITY": (14, 165, 233, 245),
        "JOBS ALERT": (22, 163, 74, 245),
        "CRICKET": (37, 99, 235, 245),
        "TOLLYWOOD": (147, 51, 234, 245),
        "TRENDING": (234, 88, 12, 245),
    }
    pill_col = badge_colors.get(badge.upper(), (220, 38, 38, 245))
    draw.rounded_rectangle([45, 178, 440, 234], radius=14, fill=pill_col)
    badge_font = load_font(27, "english")
    draw.text((70, 190), f"LIVE  |  {badge.upper()}", fill=(255, 255, 255, 255), font=badge_font)

    bar_w = 54
    start_x = 1035 - (total_scenes * (bar_w + 10))
    for i in range(total_scenes):
        col = (250, 204, 21, 255) if i <= scene_idx else (255, 255, 255, 110)
        draw.rounded_rectangle(
            [start_x + i * (bar_w + 10), 200, start_x + i * (bar_w + 10) + bar_w, 212],
            radius=6,
            fill=col,
        )

    # 5. Story Headline Banner above Photo
    head_font = load_font(42, "telugu")
    head_lines = wrap_text(draw, headline_te, head_font, 980)
    hy = 258
    for hline in head_lines[:1]:
        draw_shaped_text(draw, (52, hy), hline, head_font, (250, 204, 21, 255))

    # 6. Bottom Newsroom Subtitle Card (above Instagram Reels UI safe zone)
    sub_font = load_font(52, "telugu")
    lines = wrap_text(draw, subtitle_te, sub_font, 900)
    display_lines = lines[:2] if lines else [subtitle_te]
    card_h = 54 + len(display_lines) * 76
    card_bottom = 1610
    card_top = card_bottom - card_h

    draw.rounded_rectangle(
        [45, card_top, 1035, card_bottom],
        radius=26,
        fill=(10, 15, 28, 238),
        outline=(250, 204, 21, 255),
        width=4,
    )
    # Red accent stripe on the left of subtitle card
    draw.rounded_rectangle([45, card_top + 18, 61, card_bottom - 18], radius=8, fill=(220, 38, 38, 255))

    y = card_top + 24
    for line in display_lines:
        bbox = draw.textbbox((0, 0), line, font=sub_font)
        x = (1080 - (bbox[2] - bbox[0])) // 2
        draw_shaped_text(draw, (x, y), line, sub_font, (255, 255, 255, 255))
        y += 76

    canvas.convert("RGB").save(out_png, quality=95)


async def generate_natural_voiceover(scenes, out_audio="voice.mp3"):
    full_script = " ".join(s["narration_te"].strip() for s in scenes)
    full_script = re.sub(r"#\S+", "", full_script)
    full_script = re.sub(r"[•|★☆🔥⚠️✅📍📅⏰🎬🏏💼👇]", " ", full_script)
    full_script = re.sub(r"\s+", " ", full_script).strip()

    communicate = edge_tts.Communicate(full_script, "te-IN-ShrutiNeural", rate="-2%")
    await communicate.save(out_audio)

    probe = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            out_audio,
        ]
    ).decode().strip()

    total_dur = max(float(probe), 10.0)
    counts = [max(1, len(s["narration_te"].split())) for s in scenes]
    total_words = sum(counts)
    return [max(2.6, (c / total_words) * total_dur + 0.35) for c in counts]


def build_fast_cinema_video(scenes, durations, image_paths, badge, headline_te, bgm_path=None, out_video="reel.mp4"):
    scene_clips = []
    total_scenes = len(scenes)

    for idx, (scene, dur, img_path) in enumerate(zip(scenes, durations, image_paths)):
        composed_png = f"composed_{idx}.png"
        compose_scene_frame(
            img_path,
            badge,
            headline_te,
            scene.get("subtitle_te", ""),
            idx,
            total_scenes,
            composed_png,
        )
        clip_out = f"clip_{idx}.mp4"
        if idx % 2 == 0:
            vf = "scale=1160:2062,crop=1080:1920:'min(80, n*1.1)':'min(142, n*1.8)',format=yuv420p"
        else:
            vf = "scale=1160:2062,crop=1080:1920:'max(0, 80-n*1.1)':'max(0, 142-n*1.8)',format=yuv420p"

        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-t", f"{dur:.2f}",
            "-i", composed_png, "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", clip_out,
        ]
        subprocess.run(cmd, check=True)
        scene_clips.append(clip_out)

    trans_dur = 0.4
    inputs = []
    for c in scene_clips:
        inputs.extend(["-i", c])

    filter_parts = []
    prev = "[0:v]"
    offset = durations[0] - trans_dur
    for i in range(1, len(scene_clips)):
        tr = XFADE_TRANSITIONS[(i - 1) % len(XFADE_TRANSITIONS)]
        out_lbl = f"[v{i}]" if i < len(scene_clips) - 1 else "[vmerged]"
        filter_parts.append(
            f"{prev}[{i}:v]xfade=transition={tr}:duration={trans_dur:.2f}:offset={max(0.5, offset):.2f}{out_lbl}"
        )
        prev = out_lbl
        offset += durations[i] - trans_dur

    subprocess.run(
        [
            "ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filter_parts),
            "-map", "[vmerged]", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", "stitched.mp4",
        ],
        check=True,
    )

    if bgm_path and os.path.exists(bgm_path):
        cmd_final = [
            "ffmpeg", "-y", "-i", "stitched.mp4", "-i", "voice.mp3", "-stream_loop", "-1",
            "-i", bgm_path, "-filter_complex",
            "[2:a]volume=0.14[bgm];[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", out_video,
        ]
    else:
        cmd_final = [
            "ffmpeg", "-y", "-i", "stitched.mp4", "-i", "voice.mp3",
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", out_video,
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
    print("Selected Story:", story.get("headline_te"))

    primary_img = ""
    for art in articles:
        if art["link"] == story["selected_link"]:
            primary_img = art.get("rss_image", "")
            break

    # Scrape the real news article og:image if RSS didn't include a photo
    if not primary_img:
        primary_img = extract_article_og_image(story["selected_link"])

    image_paths = gather_scene_images(story["scenes"], primary_img)
    durations = asyncio.run(generate_natural_voiceover(story["scenes"]))
    bgm_file = pick_bgm(story.get("bgm_mood", "general"))

    build_fast_cinema_video(
        scenes=story["scenes"],
        durations=durations,
        image_paths=image_paths,
        badge=story.get("category_badge", "NELLORE CITY"),
        headline_te=story.get("headline_te", "తాజా వార్తలు"),
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
    print(f"Hourly Cinema Reel completed! (IG={ig_ok}, FB={fb_ok})")


if __name__ == "__main__":
    main()
