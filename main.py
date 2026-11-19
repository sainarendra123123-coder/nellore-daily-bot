import os
import re
import io
import json
import time
import urllib.parse
import asyncio
import random
import subprocess
from datetime import datetime, timezone, timedelta
import feedparser
import requests
import edge_tts
from bs4 import BeautifulSoup
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

# Direct publisher RSS feeds (real og:image photos + latest today news, zero politics)
CATEGORY_FEEDS = {
    "NELLORE CITY": [
        "https://www.eenadu.net/rss/andhra-pradesh.xml",
        "https://www.sakshi.com/rss/andhra-pradesh",
        "https://news.google.com/rss/search?q=Nellore+city+OR+%E0%B0%A8%E0%B1%86%E0%B0%B2%E0%B1%8D%E0%B0%B2%E0%B1%82%E0%B0%B0%E0%B1%81+when:1d&hl=te&gl=IN&ceid=IN:te"
    ],
    "JOBS ALERT": [
        "https://news.google.com/rss/search?q=APPSC+OR+DSC+OR+Andhra+Pradesh+jobs+notification+when:1d&hl=te&gl=IN&ceid=IN:te",
        "https://news.google.com/rss/search?q=Andhra+Pradesh+govt+jobs+notification+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
    ],
    "CRICKET": [
        "https://www.espncricinfo.com/rss/content/story/feeds/0.xml",
        "https://news.google.com/rss/search?q=India+cricket+OR+IPL+Telugu+when:1d&hl=te&gl=IN&ceid=IN:te"
    ],
    "TOLLYWOOD": [
        "https://telugu.samayam.com/entertainment/rssfeeds/48332856.cms",
        "https://news.google.com/rss/search?q=Tollywood+movie+release+OR+trailer+when:1d&hl=te&gl=IN&ceid=IN:te"
    ],
    "TRENDING": [
        "https://news.google.com/rss/search?q=Gold+rate+today+Andhra+Pradesh+when:1d&hl=te&gl=IN&ceid=IN:te",
        "https://www.eenadu.net/rss/andhra-pradesh.xml"
    ]
}

POLITICS_BLOCKLIST = [
    "జగన్", "చంద్రబాబు", "పవన్ కళ్యాణ్", "లోకేష్", "వైసీపీ", "టీడీపీ", "జనసేన",
    "బీజేపీ", "కాంగ్రెస్", "ఎమ్మెల్యే", "ఎంపీ", "మంత్రి", "అసెంబ్లీ", "ఎన్నికలు",
    "jagan", "chandrababu", "pawan kalyan", "lokesh", "ysrcp", "tdp", "janasena",
    "bjp", "congress", "mla", "mp", "minister", "assembly", "election", "politics"
]

GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.8-flash-lite",
    "gemini-2.5-flash"
]

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
    return re.sub(r"\s+", " ", text).strip()

def contains_politics(text):
    lowered = text.lower()
    return any(word in lowered for word in POLITICS_BLOCKLIST)

def is_recent_entry(entry, max_age_hours=18):
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
                resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                feed = feedparser.parse(resp.content)
                for entry in feed.entries[:6]:
                    if not is_recent_entry(entry, max_age_hours=18):
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
                        "rss_image": extract_rss_media(entry)
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
       - Keep each scene's "subtitle_te" SHORT (4 to 6 Telugu words max) so it looks clean and readable.
    4. REAL PHOTO SEARCH KEYWORDS FOR EACH SCENE:
       - For each of the 4 scenes, give a clean 2-3 word English image search query ("image_query") that reliably returns high-res real photos on the web (e.g., "Indian cricket stadium match", "Virat Kohli batting", "Indian students classroom exam", "gold jewelry necklace", "Nellore city India", "Telugu movie cinema theater").
    5. METADATA:
       - "category_badge": One of "NELLORE CITY", "JOBS ALERT", "CRICKET", "TOLLYWOOD", "TRENDING".
       - "headline_te": Short 5-word Telugu headline.
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
                        temperature=0.35
                    )
                )
                data = json.loads(response.text)
                if data.get("selected_link") and isinstance(data.get("scenes"), list) and len(data["scenes"]) >= 3:
                    return data
            except Exception as e:
                last_err = e
                time.sleep(8 * (attempt + 1))

    raise RuntimeError(f"All Gemini models failed: {last_err}")

def search_web_images(query, max_results=8):
    # Reliable multi-source real image search (Bing Image Search HTML + Wikimedia Commons)
    urls = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    }
    try:
        q_enc = urllib.parse.quote(f"{query} high resolution photo")
        r = requests.get(f"https://www.bing.com/images/search?q={q_enc}&qft=+filterui:imagesize-large+filterui:photo-photo&form=IRFLTR", headers=headers, timeout=12)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", class_="iusc"):
            m_attr = a.get("m")
            if m_attr:
                meta = json.loads(m_attr)
                murl = meta.get("murl", "")
                if murl.startswith("http") and any(ext in murl.lower() for ext in (".jpg", ".jpeg", ".png", ".webp")):
                    urls.append(murl)
                    if len(urls) >= max_results:
                        break
    except Exception as e:
        print(f"Bing image search warning ({query}): {e}")

    # Secondary source: Wikimedia Commons
    if len(urls) < 3:
        try:
            r = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query", "generator": "search",
                    "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": "6", "gsrlimit": "6",
                    "prop": "imageinfo", "iiprop": "url", "iiurlwidth": "1280", "format": "json"
                },
                headers={"User-Agent": "NelloreDailyBot/1.0"},
                timeout=12
            ).json()
            for p in r.get("query", {}).get("pages", {}).values():
                ii = p.get("imageinfo", [{}])[0]
                u = ii.get("thumburl") or ii.get("url")
                if u and u.startswith("http"):
                    urls.append(u)
        except Exception:
            pass
    return urls

def download_verified_photo(url, out_path):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and len(r.content) > 12000:
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            if img.width >= 500 and img.height >= 350:
                img.save(out_path, quality=95)
                print(f"Downloaded real photo ({img.width}x{img.height}): {out_path}")
                return out_path
    except Exception:
        pass
    return None

def gather_scene_images(scenes, rss_image_url=""):
    image_paths = []
    used_urls = set()

    # Scene 0: Try RSS lead image first if available
    if rss_image_url:
        p = download_verified_photo(rss_image_url, "scene_raw_0.jpg")
        if p:
            image_paths.append(p)
            used_urls.add(rss_image_url)

    for idx, scene in enumerate(scenes):
        if len(image_paths) > idx:
            continue
        out_file = f"scene_raw_{idx}.jpg"
        query = scene.get("image_query", "India news")
        candidates = search_web_images(query)
        if not candidates:
            candidates = search_web_images("India city modern")

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
            image_paths.append(image_paths[-1])
        else:
            img = Image.new("RGB", (1080, 1920), (24, 38, 68))
            img.save(out_file)
            image_paths.append(out_file)

    return image_paths[:len(scenes)]

def load_font(size, script="telugu"):
    if script == "english":
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
        ]
    else:
        paths = [
            "/usr/share/fonts/truetype/noto/NotoSansTelugu-Bold.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansTelugu-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf"
        ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

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

def compose_scene_frame(bg_img_path, badge, subtitle_te, scene_idx, total_scenes, out_png):
    src = Image.open(bg_img_path).convert("RGB")
    # Enhance contrast & color vibrancy cleanly in Pillow
    src = ImageEnhance.Contrast(src).enhance(1.10)
    src = ImageEnhance.Color(src).enhance(1.18)
    src = ImageEnhance.Sharpness(src).enhance(1.15)

    ratio = max(1080 / src.width, 1920 / src.height)
    resized = src.resize((int(src.width * ratio), int(src.height * ratio)), Image.LANCZOS)
    left = (resized.width - 1080) // 2
    top = (resized.height - 1920) // 2
    canvas = resized.crop((left, top, left + 1080, top + 1920)).convert("RGBA")

    # Top and bottom dark gradients so bright photos never wash out text
    grad = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grad)
    for y in range(0, 320):
        a = int(190 * (1 - y / 320))
        gdraw.line([(0, y), (1080, y)], fill=(8, 12, 22, a))
    for y in range(1260, 1920):
        a = int(235 * ((y - 1260) / 660))
        gdraw.line([(0, y), (1080, y)], fill=(8, 12, 22, a))
    canvas = Image.alpha_composite(canvas, grad)
    draw = ImageDraw.Draw(canvas)

    # Top Brand Header
    draw.rounded_rectangle([45, 70, 1035, 162], radius=20, fill=(220, 38, 38, 240))
    te_brand_font = load_font(42, "telugu")
    en_brand_font = load_font(36, "english")
    draw.text((80, 92), "నెల్లూరు డైలీ", fill=(255, 255, 255, 255), font=te_brand_font)
    draw.text((665, 96), "NELLORE DAILY", fill=(255, 255, 255, 255), font=en_brand_font)

    # Category Pill
    badge_colors = {
        "NELLORE CITY": (14, 165, 233, 245),
        "JOBS ALERT": (22, 163, 74, 245),
        "CRICKET": (37, 99, 235, 245),
        "TOLLYWOOD": (147, 51, 234, 245),
        "TRENDING": (234, 88, 12, 245)
    }
    pill_col = badge_colors.get(badge.upper(), (220, 38, 38, 245))
    draw.rounded_rectangle([45, 185, 430, 242], radius=14, fill=pill_col)
    badge_font = load_font(28, "english")
    draw.text((70, 197), f"LIVE  |  {badge.upper()}", fill=(255, 255, 255, 255), font=badge_font)

    # Scene Progress Bars
    bar_w = 52
    start_x = 1035 - (total_scenes * (bar_w + 10))
    for i in range(total_scenes):
        col = (250, 204, 21, 255) if i <= scene_idx else (255, 255, 255, 110)
        draw.rounded_rectangle([start_x + i * (bar_w + 10), 208, start_x + i * (bar_w + 10) + bar_w, 220], radius=6, fill=col)

    # Bottom Caption Card
    sub_font = load_font(52, "telugu")
    lines = wrap_text(draw, subtitle_te, sub_font, 900)
    card_h = 46 + len(lines[:2]) * 74
    card_bottom = 1660
    card_top = card_bottom - card_h

    draw.rounded_rectangle([55, card_top, 1025, card_bottom], radius=24, fill=(12, 18, 32, 220), outline=(250, 204, 21, 245), width=4)
    y = card_top + 22
    for line in lines[:2]:
        bbox = draw.textbbox((0, 0), line, font=sub_font)
        x = (1080 - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line, fill=(255, 255, 255, 255), font=sub_font)
        y += 74

    canvas.convert("RGB").save(out_png, quality=95)

async def generate_natural_voiceover(scenes, out_audio="voice.mp3"):
    full_script = " ".join(s["narration_te"].strip() for s in scenes)
    full_script = re.sub(r"#\S+", "", full_script)
    full_script = re.sub(r"[•|★☆🔥⚠️✅📍📅⏰🎬🏏💼👇]", " ", full_script)
    full_script = re.sub(r"\s+", " ", full_script).strip()

    communicate = edge_tts.Communicate(full_script, "te-IN-ShrutiNeural", rate="-2%")
    await communicate.save(out_audio)

    probe = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", out_audio
    ]).decode().strip()
    total_dur = max(float(probe), 10.0)

    counts = [max(1, len(s["narration_te"].split())) for s in scenes]
    total_words = sum(counts)
    return [max(2.6, (c / total_words) * total_dur + 0.35) for c in counts]

def build_fast_cinema_video(scenes, durations, image_paths, badge, bgm_path=None, out_video="reel.mp4"):
    # Fast, crisp motion using scale+crop (renders in ~15 seconds instead of 30 minutes!)
    scene_clips = []
    total_scenes = len(scenes)

    for idx, (scene, dur, img_path) in enumerate(zip(scenes, durations, image_paths)):
        composed_png = f"composed_{idx}.png"
        compose_scene_frame(img_path, badge, scene.get("subtitle_te", ""), idx, total_scenes, composed_png)

        clip_out = f"clip_{idx}.mp4"
        # Smooth linear push-in / pull-out via scale+crop (100x faster than zoompan)
        if idx % 2 == 0:
            vf = f"scale='1080+iw*0.004*n':'1920+ih*0.004*n':eval=frame,crop=1080:1920,format=yuv420p"
        else:
            vf = f"scale='1188-iw*0.003*n':'2112-ih*0.003*n':eval=frame,crop=1080:1920,format=yuv420p"

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-framerate", "30", "-t", f"{dur:.2f}", "-i", composed_png,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", clip_out
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
        filter_parts.append(f"{prev}[{i}:v]xfade=transition={tr}:duration={trans_dur:.2f}:offset={max(0.5, offset):.2f}{out_lbl}")
        prev = out_lbl
        offset += durations[i] - trans_dur

    subprocess.run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vmerged]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "stitched.mp4"
    ], check=True)

    if bgm_path and os.path.exists(bgm_path):
        cmd_final = [
            "ffmpeg", "-y",
            "-i", "stitched.mp4",
            "-i", "voice.mp3",
            "-stream_loop", "-1", "-i", bgm_path,
            "-filter_complex", "[2:a]volume=0.14[bgm];[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", out_video
        ]
    else:
        cmd_final = [
            "ffmpeg", "-y",
            "-i", "stitched.mp4",
            "-i", "voice.mp3",
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", out_video
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
        data={"media_type": "REELS", "upload_type": "resumable", "caption": caption, "access_token": META_ACCESS_TOKEN},
        timeout=30
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
                "Content-Type": "application/octet-stream"
            },
            data=vf.read(),
            timeout=90
        ).json()
    if not up.get("success"):
        raise RuntimeError(f"IG binary upload error: {up}")

    for _ in range(18):
        time.sleep(10)
        status = requests.get(
            f"https://graph.facebook.com/v21.0/{creation_id}",
            params={"fields": "status_code", "access_token": META_ACCESS_TOKEN},
            timeout=20
        ).json()
        if status.get("status_code") == "FINISHED":
            break
        if status.get("status_code") == "ERROR":
            raise RuntimeError(f"IG processing failed: {status}")

    r2 = requests.post(
        f"https://graph.facebook.com/v21.0/{IG_USER_ID}/media_publish",
        data={"creation_id": creation_id, "access_token": META_ACCESS_TOKEN},
        timeout=30
    ).json()
    if "id" not in r2:
        raise RuntimeError(f"IG publish error: {r2}")
    print("Instagram Reel Published:", r2)

def publish_to_facebook_page(video_path, caption):
    acct = requests.get(
        f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}",
        params={"fields": "access_token", "access_token": META_ACCESS_TOKEN},
        timeout=20
    ).json()
    page_token = acct.get("access_token", META_ACCESS_TOKEN)

    with open(video_path, "rb") as vf:
        r = requests.post(
            f"https://graph-video.facebook.com/v21.0/{FB_PAGE_ID}/videos",
            data={"description": caption, "access_token": page_token},
            files={"source": vf},
            timeout=90
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

    rss_img = ""
    for art in articles:
        if art["link"] == story["selected_link"] and art.get("rss_image"):
            rss_img = art["rss_image"]
            break

    image_paths = gather_scene_images(story["scenes"], rss_img)
    durations = asyncio.run(generate_natural_voiceover(story["scenes"]))
    bgm_file = pick_bgm(story.get("bgm_mood", "general"))

    build_fast_cinema_video(
        scenes=story["scenes"],
        durations=durations,
        image_paths=image_paths,
        badge=story.get("category_badge", "NELLORE CITY"),
        bgm_path=bgm_file
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
