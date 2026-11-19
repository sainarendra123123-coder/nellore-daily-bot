import os
import re
import io
import json
import time
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

# Curated category-specific fresh feeds (No politics)
CATEGORY_FEEDS = {
    "NELLORE CITY": [
        "https://news.google.com/rss/search?q=Nellore+city+OR+%E0%B0%A8%E0%B1%86%E0%B0%B2%E0%B1%8D%E0%B0%B2%E0%B1%82%E0%B0%B0%E0%B1%81+when:1d&hl=te&gl=IN&ceid=IN:te",
        "https://news.google.com/rss/search?q=Nellore+offers+OR+events+OR+weather+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
    ],
    "JOBS ALERT": [
        "https://news.google.com/rss/search?q=APPSC+OR+Andhra+Pradesh+jobs+notification+when:1d&hl=te&gl=IN&ceid=IN:te",
        "https://news.google.com/rss/search?q=Nellore+jobs+OR+AP+govt+jobs+notification+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
    ],
    "CRICKET": [
        "https://news.google.com/rss/search?q=India+cricket+OR+IPL+Telugu+when:1d&hl=te&gl=IN&ceid=IN:te",
        "https://www.espncricinfo.com/rss/content/story/feeds/0.xml"
    ],
    "TOLLYWOOD": [
        "https://news.google.com/rss/search?q=Tollywood+movie+release+OR+trailer+when:1d&hl=te&gl=IN&ceid=IN:te",
        "https://telugu.samayam.com/entertainment/rssfeeds/48332856.cms"
    ],
    "TRENDING": [
        "https://news.google.com/rss/search?q=Gold+rate+today+Andhra+Pradesh+when:1d&hl=te&gl=IN&ceid=IN:te",
        "https://www.eenadu.net/rss/andhra-pradesh.xml",
        "https://www.sakshi.com/rss/andhra-pradesh"
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

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            print(f"History read warning: {e}")
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
        pub_ts = time.mktime(parsed)
        age_hours = (time.time() - pub_ts) / 3600.0
        return age_hours <= max_age_hours
    except Exception:
        return True

def extract_entry_image(entry):
    for key in ("media_content", "media_thumbnail"):
        media = entry.get(key)
        if isinstance(media, list) and media and media[0].get("url"):
            return media[0]["url"]
    for link in entry.get("links", []):
        if "image" in link.get("type", "") and link.get("href"):
            return link["href"]
    article_url = entry.get("link", "")
    if article_url and "news.google.com" not in article_url:
        try:
            html = requests.get(article_url, timeout=8, headers={"User-Agent": "Mozilla/5.0"}).text
            m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
            if m:
                return m.group(1)
        except Exception:
            pass
    return ""

def fetch_latest_news(history):
    articles = []
    seen_links = set(history)
    # Rotate category order every 30-min run so your page gets variety (Nellore -> Jobs -> Cricket -> Tollywood -> Trending)
    categories = list(CATEGORY_FEEDS.keys())
    slot = int(time.time() // 1800) % len(categories)
    ordered_cats = categories[slot:] + categories[:slot]

    for category in ordered_cats:
        for url in CATEGORY_FEEDS[category]:
            try:
                resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                feed = feedparser.parse(resp.content)
                for entry in feed.entries[:8]:
                    if not is_recent_entry(entry, max_age_hours=18):
                        continue
                    link = entry.get("link", "").strip()
                    title = clean_html(entry.get("title", ""))
                    summary = clean_html(entry.get("summary", ""))
                    combined = f"{title} {summary}"
                    if not link or not title or link in seen_links:
                        continue
                    if contains_politics(combined):
                        continue
                    seen_links.add(link)
                    articles.append({
                        "category": category,
                        "title": title,
                        "summary": summary[:400],
                        "published": entry.get("published", ""),
                        "link": link,
                        "image_url": extract_entry_image(entry)
                    })
            except Exception as e:
                print(f"Feed warning ({category}): {e}")
    return articles[:22]

def generate_script_with_gemini(articles):
    client = genai.Client(api_key=GEMINI_API_KEY)
    now_ist = datetime.now(IST).strftime("%B %d, %Y at %I:%M %p IST")
    preferred_cat = articles[0]["category"] if articles else "NELLORE CITY"

    prompt = f"""
    You are the content creator for 'Nellore Daily' (నెల్లూరు డైలీ), a friendly, modern local Telugu channel.
    Current exact date & time: {now_ist}.

    Review these fresh news items from today:
    {json.dumps(articles, ensure_ascii=False)}

    STRICT EDITORIAL RULES:
    1. ZERO POLITICS: Never pick political news, party fights, MLA/MP statements, or election stories.
    2. PRIORITY TOPICS: Prefer '{preferred_cat}' if a strong, fresh story is available; otherwise pick the best fresh story from:
       - Nellore city local updates, civic alerts, weather, or shopping/food offers
       - New job notifications (APPSC, Govt, Private jobs for Nellore/AP youth)
       - Latest Cricket news (Team India, IPL, Telugu cricketers)
       - Tollywood updates (new movie releases, trailers, OTT releases)
       - Useful daily trends (Gold/Silver rates, interesting viral facts)
    3. NATURAL SPOKEN TELUGU (VERY IMPORTANT):
       - Write the voiceover in everyday, natural spoken Telugu—the way real people in Nellore talk!
       - DO NOT use heavy literary, bookish, or artificial "dubbing" Telugu words.
       - Keep common English words naturally in Telugu script or simple phrasing where people actually say them (e.g., జాబ్ నోటిఫికేషన్, ఆఫర్స్, క్రికెట్, ట్రైలర్, రిలీజ్, నెల్లూరు సిటీ).
       - Use short, clear sentences with commas and full stops so the voiceover breathes naturally and pronounces every word clearly.
       - Length: 40 to 55 words (about 18–24 seconds spoken).
    4. ON-SCREEN VISUAL TEXT:
       - "category_badge": Short uppercase English badge (one of: "NELLORE CITY", "JOBS ALERT", "CRICKET", "TOLLYWOOD", "TRENDING").
       - "headline_te": Punchy, crystal-clear Telugu headline (max 7 words).
       - "card_summary_te": 2 short, easy-to-read Telugu sentences summarizing the key facts/dates/numbers (max 24 words).
    5. CAPTION:
       - Engaging Instagram & Facebook caption in Telugu + English with source credit and hashtags (#Nellore #NelloreDaily #NelloreCity #TeluguNews #AndhraPradesh).
    6. BGM MOOD: Choose one from "breaking", "emotional", "devotional", "general".

    Return strictly valid JSON with keys:
    "selected_link", "selected_image_url", "category_badge", "headline_te", "card_summary_te", "voiceover_te", "caption", "bgm_mood"
    """

    last_err = None
    for model_name in GEMINI_MODELS:
        for attempt in range(3):
            try:
                print(f"Trying {model_name} (attempt {attempt + 1}/3)...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.35
                    )
                )
                data = json.loads(response.text)
                required = ["selected_link", "headline_te", "voiceover_te", "caption"]
                if all(k in data and data[k] for k in required):
                    return data
            except Exception as e:
                last_err = e
                wait_sec = 10 * (attempt + 1)
                print(f"{model_name} error: {e}. Retrying in {wait_sec}s...")
                time.sleep(wait_sec)

    raise RuntimeError(f"All Gemini models failed after retries: {last_err}")

def clean_for_tts(text):
    # Remove hashtags, emojis/symbols, and extra spaces so Telugu TTS pronounces cleanly
    text = re.sub(r"#\S+", "", text)
    text = re.sub(r"[•|★☆🔥⚠️✅📍📅⏰🎬🏏💼👇]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

async def create_voiceover(text, output_path="voice.mp3"):
    spoken_text = clean_for_tts(text)
    voices = ["te-IN-MohanNeural", "te-IN-ShrutiNeural"]
    random.shuffle(voices)
    for voice in voices:
        for attempt in range(2):
            try:
                communicate = edge_tts.Communicate(spoken_text, voice, rate="+2%", pitch="+0Hz")
                await communicate.save(output_path)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                    print(f"Voiceover generated cleanly using {voice}")
                    return
            except Exception as e:
                print(f"TTS warning ({voice}): {e}")
                await asyncio.sleep(3)
    raise RuntimeError("Failed to generate Telugu TTS audio.")

def load_telugu_font(size):
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansTelugu-Bold.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansTelugu-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines

def fetch_news_photo(image_url):
    if not image_url:
        return None
    try:
        r = requests.get(image_url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and len(r.content) > 6000:
            return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:
        print(f"Image fetch warning: {e}")
    return None

def build_background_image(headline_te, summary_te="", badge="NELLORE CITY", image_url="", output_img="bg.png"):
    photo = fetch_news_photo(image_url)

    # Create blurred full-bleed backdrop if photo exists, else sleek deep navy gradient
    if photo:
        ratio = max(1080 / photo.width, 1920 / photo.height)
        bg = photo.resize((int(photo.width * ratio), int(photo.height * ratio)), Image.LANCZOS)
        left = (bg.width - 1080) // 2
        top = (bg.height - 1920) // 2
        bg = bg.crop((left, top, left + 1080, top + 1920))
        bg = bg.filter(ImageFilter.GaussianBlur(radius=24))
        img = ImageEnhance.Brightness(bg).enhance(0.28)
    else:
        img = Image.new("RGB", (1080, 1920), color=(11, 18, 32))

    draw = ImageDraw.Draw(img)

    # Top Brand Header Bar
    draw.rounded_rectangle([50, 95, 1030, 215], radius=24, fill=(220, 38, 38))
    brand_font = load_telugu_font(48)
    draw.text((85, 128), "నెల్లూరు డైలీ  •  NELLORE DAILY", fill=(255, 255, 255), font=brand_font)

    # Category Pill Badge
    badge_colors = {
        "NELLORE CITY": (14, 165, 233),
        "JOBS ALERT": (22, 163, 74),
        "CRICKET": (37, 99, 235),
        "TOLLYWOOD": (147, 51, 234),
        "TRENDING": (234, 88, 12)
    }
    pill_color = badge_colors.get(badge.upper(), (220, 38, 38))
    draw.rounded_rectangle([50, 240, 440, 305], radius=18, fill=pill_color)
    badge_font = load_telugu_font(34)
    draw.text((80, 253), f"●  {badge.upper()}", fill=(255, 255, 255), font=badge_font)

    # Crisp Main Photo Card (if photo exists)
    if photo:
        target_w, target_h = 980, 560
        ratio = max(target_w / photo.width, target_h / photo.height)
        resized = photo.resize((int(photo.width * ratio), int(photo.height * ratio)), Image.LANCZOS)
        left = (resized.width - target_w) // 2
        top = (resized.height - target_h) // 2
        cropped = resized.crop((left, top, left + target_w, top + target_h))
        img.paste(cropped, (50, 330))
        draw.rectangle([50, 330, 1030, 890], outline=(255, 255, 255), width=4)
        card_top = 920
    else:
        card_top = 340

    # Clean Glass-Style News Card
    draw.rounded_rectangle([50, card_top, 1030, 1620], radius=28, fill=(17, 24, 39), outline=(239, 68, 68), width=4)

    # Bold Yellow Telugu Headline
    head_font = load_telugu_font(56)
    head_lines = wrap_text(draw, headline_te, head_font, 880)
    y = card_top + 40
    for line in head_lines[:4]:
        draw.text((95, y), line, fill=(250, 204, 21), font=head_font)
        y += 80

    # Accent Divider
    y += 16
    draw.line([(95, y), (985, y)], fill=(239, 68, 68), width=4)
    y += 32

    # Clear White Telugu Summary
    if summary_te:
        body_font = load_telugu_font(42)
        body_lines = wrap_text(draw, summary_te, body_font, 880)
        for line in body_lines[:6]:
            draw.text((95, y), line, fill=(248, 250, 252), font=body_font)
            y += 64

    # Bottom Live Timestamp Bar
    draw.rounded_rectangle([50, 1655, 1030, 1765], radius=22, fill=(185, 28, 28))
    footer_font = load_telugu_font(38)
    now_label = datetime.now(IST).strftime("%d %b %Y • %I:%M %p")
    draw.text((85, 1688), f"LIVE UPDATE • {now_label} • నెల్లూరు", fill=(255, 255, 255), font=footer_font)

    img.save(output_img, quality=95)

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

def render_video(voice_path="voice.mp3", bg_img="bg.png", bgm_path=None, out_video="reel.mp4"):
    # Apply subtle smooth zoom-in motion so the Reel looks dynamic and crisp
    vf_motion = "scale=1188:2112,zoompan=z='min(zoom+0.0008,1.08)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30,format=yuv420p"

    if bgm_path and os.path.exists(bgm_path):
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", bg_img,
            "-i", voice_path,
            "-stream_loop", "-1", "-i", bgm_path,
            "-filter_complex",
            f"[0:v]{vf_motion}[vout];[2:a]volume=0.13[bgm];[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-shortest", out_video
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", bg_img,
            "-i", voice_path,
            "-vf", vf_motion,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-shortest", out_video
        ]
    subprocess.run(cmd, check=True)

def publish_to_instagram(video_path, caption):
    r1 = requests.post(
        f"https://graph.facebook.com/v21.0/{IG_USER_ID}/media",
        data={
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption,
            "access_token": META_ACCESS_TOKEN
        },
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
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
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
        print("No fresh non-political articles found in this 30-min window. Exiting cleanly.")
        return

    news_data = generate_script_with_gemini(articles)
    print("Selected News:", news_data["headline_te"])

    selected_img = news_data.get("selected_image_url", "")
    if not selected_img:
        for art in articles:
            if art["link"] == news_data["selected_link"] and art.get("image_url"):
                selected_img = art["image_url"]
                break

    asyncio.run(create_voiceover(news_data["voiceover_te"]))
    build_background_image(
        headline_te=news_data["headline_te"],
        summary_te=news_data.get("card_summary_te", ""),
        badge=news_data.get("category_badge", "NELLORE CITY"),
        image_url=selected_img
    )
    bgm_file = pick_bgm(news_data.get("bgm_mood", "general"))
    render_video(bgm_path=bgm_file)

    if PREVIEW_ONLY:
        print("PREVIEW_ONLY=true — skipping IG/FB publish. Download reel.mp4 from Artifacts!")
        return

    ig_ok, fb_ok = False, False
    try:
        publish_to_instagram("reel.mp4", news_data["caption"])
        ig_ok = True
    except Exception as e:
        print(f"Instagram publish warning: {e}")

    try:
        publish_to_facebook_page("reel.mp4", news_data["caption"])
        fb_ok = True
    except Exception as e:
        print(f"Facebook publish warning: {e}")

    if not ig_ok and not fb_ok:
        raise RuntimeError("Both Instagram and Facebook publishing failed.")

    history.append(news_data["selected_link"])
    save_history(history)
    print(f"30-min run completed! (IG={ig_ok}, FB={fb_ok})")

if __name__ == "__main__":
    main()
