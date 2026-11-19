import os
import re
import io
import json
import time
import base64
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
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
PREVIEW_ONLY = os.environ.get("PREVIEW_ONLY", "false").lower() == "true"

IST = timezone(timedelta(hours=5, minutes=30))
HISTORY_FILE = "posted_history.json"

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

XFADE_TRANSITIONS = ["smoothleft", "fadeblack", "wipeleft", "dissolve", "slideright"]

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
        return ((time.time() - pub_ts) / 3600.0) <= max_age_hours
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
    categories = list(CATEGORY_FEEDS.keys())
    slot = int(time.time() // 3600) % len(categories)
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
                    if not link or not title or link in seen_links:
                        continue
                    if contains_politics(f"{title} {summary}"):
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

def generate_storyboard_with_gemini(articles):
    client = genai.Client(api_key=GEMINI_API_KEY)
    now_ist = datetime.now(IST).strftime("%B %d, %Y at %I:%M %p IST")
    preferred_cat = articles[0]["category"] if articles else "NELLORE CITY"

    prompt = f"""
    You are a senior cinema-grade vertical video director & Telugu storyteller for 'Nellore Daily' (నెల్లూరు డైలీ).
    Current exact date & time: {now_ist}.

    Review these fresh news items from today:
    {json.dumps(articles, ensure_ascii=False)}

    STRICT EDITORIAL & VISUAL RULES:
    1. ZERO POLITICS: Never pick political parties, speeches, MLA/MP controversies, or election stories.
    2. TOPIC PRIORITY: Prefer '{preferred_cat}' if a strong fresh story is available; otherwise pick the best fresh update from Nellore City updates/offers, Job notifications (APPSC/Govt/Private), Cricket, Tollywood, or Gold/Silver & trending facts.
    3. CINEMATIC 4-SCENE STORYBOARD:
       Break the story into 4 grand, fast-paced visual scenes (each ~4-5 seconds) that visually explain every step of the story:
       For each scene provide:
       - "narration_te": 1–2 short, natural spoken Telugu sentences (everyday Nellore spoken Telugu, NO stiff literary/dubbing words, use common terms like జాబ్ నోటిఫికేషన్, ఆఫర్స్, క్రికెట్, ట్రైలర్, రిలీజ్).
       - "on_screen_te": Short punchy Telugu kinetic headline/key-point for that scene (max 5 words).
       - "visual_query": 2-3 specific English keywords to search for an HD vertical B-roll video matching that scene (e.g., "indian university campus students", "cricket stadium floodlights", "gold jewelry showroom", "indian city monsoon rain", "cinema premiere crowd").
       - "ai_visual_prompt": A photorealistic, grand cinematic vertical scene description (no text in image) for realistic Indian/Andhra context in case stock video is unavailable.
    4. METADATA:
       - "category_badge": One of "NELLORE CITY", "JOBS ALERT", "CRICKET", "TOLLYWOOD", "TRENDING".
       - "top_banner_te": Short 4-word Telugu topic title.
       - "caption": Engaging Instagram & Facebook caption in Telugu + English with source credit and hashtags (#Nellore #NelloreDaily #NelloreCity #TeluguReels #AndhraPradesh).
       - "bgm_mood": One of "breaking", "emotional", "devotional", "general".

    Return strictly valid JSON with keys:
    "selected_link", "selected_image_url", "category_badge", "top_banner_te", "scenes" (array of 4 objects with "narration_te", "on_screen_te", "visual_query", "ai_visual_prompt"), "caption", "bgm_mood"
    """

    last_err = None
    for model_name in GEMINI_MODELS:
        for attempt in range(3):
            try:
                print(f"Generating cinematic storyboard with {model_name} (attempt {attempt + 1}/3)...")
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
                wait_sec = 10 * (attempt + 1)
                print(f"{model_name} error: {e}. Retrying in {wait_sec}s...")
                time.sleep(wait_sec)

    raise RuntimeError(f"All Gemini models failed: {last_err}")

def clean_for_tts(text):
    text = re.sub(r"#\S+", "", text)
    text = re.sub(r"[•|★☆🔥⚠️✅📍📅⏰🎬🏏💼👇]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

async def generate_scene_audio_and_subs(scenes, out_audio="voice.mp3", out_srt="subs.srt"):
    voice = random.choice(["te-IN-MohanNeural", "te-IN-ShrutiNeural"])
    print(f"Generating scene-by-scene Telugu voiceover using {voice}...")

    scene_durations = []
    part_files = []
    srt_entries = []
    cursor = 0.0
    transition_overlap = 0.45

    def fmt_srt_time(sec):
        sec = max(0.0, sec)
        ms = int(round((sec % 1) * 1000))
        s = int(sec) % 60
        m = (int(sec) // 60) % 60
        h = int(sec) // 3600
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    for idx, scene in enumerate(scenes):
        part_mp3 = f"part_{idx}.mp3"
        spoken = clean_for_tts(scene["narration_te"])
        communicate = edge_tts.Communicate(spoken, voice, rate="+3%")
        await communicate.save(part_mp3)

        probe = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", part_mp3
        ]).decode().strip()
        # Add 0.45s overlap padding for all scenes except the last so xfade transitions match audio duration
        pad = transition_overlap if idx < len(scenes) - 1 else 0.2
        dur = max(float(probe) + pad, 2.8)
        scene_durations.append(dur)
        part_files.append(part_mp3)

        effective_span = dur - (transition_overlap if idx < len(scenes) - 1 else 0.0)
        words = spoken.split()
        mid = max(1, len(words) // 2)
        chunks = [c for c in [" ".join(words[:mid]), " ".join(words[mid:])] if c.strip()]
        chunk_dur = effective_span / max(len(chunks), 1)
        for c in chunks:
            srt_entries.append((cursor, cursor + chunk_dur, c))
            cursor += chunk_dur

    with open("concat_audio.txt", "w", encoding="utf-8") as f:
        for p in part_files:
            f.write(f"file '{p}'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "concat_audio.txt", "-c", "copy", out_audio], check=True)

    with open(out_srt, "w", encoding="utf-8") as f:
        for i, (st, en, txt) in enumerate(srt_entries, 1):
            f.write(f"{i}\n{fmt_srt_time(st)} --> {fmt_srt_time(en)}\n{txt}\n\n")

    return scene_durations

def generate_ai_scene_image(prompt_text, idx):
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        full_prompt = f"Photorealistic, grand cinematic 9:16 vertical photography, dramatic lighting, rich color depth, ultra detailed, realistic South Indian setting, no text or watermarks: {prompt_text}"
        res = client.models.generate_images(
            model="imagen-3.0-generate-002",
            prompt=full_prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="9:16",
                output_mime_type="image/jpeg"
            )
        )
        if res.generated_images:
            img_bytes = res.generated_images[0].image.image_bytes
            out_path = f"ai_scene_{idx}.jpg"
            with open(out_path, "wb") as f:
                f.write(img_bytes)
            print(f"Generated photorealistic AI scene {idx}")
            return out_path
    except Exception as e:
        print(f"Imagen fallback warning ({idx}): {e}")
    return None

def fetch_scene_visual(scene, idx, article_photo_path=None):
    # Scene 0 prioritizes the real news article photo if high-res, to ground the story in real news
    if idx == 0 and article_photo_path and os.path.exists(article_photo_path):
        return "image", article_photo_path

    query = scene.get("visual_query", "nellore city")
    if PEXELS_API_KEY:
        headers = {"Authorization": PEXELS_API_KEY}
        try:
            r = requests.get(
                "https://api.pexels.com/videos/search",
                headers=headers,
                params={"query": query, "orientation": "portrait", "size": "medium", "per_page": 5},
                timeout=12
            ).json()
            videos = r.get("videos", [])
            if videos:
                vid = random.choice(videos[:3])
                files = [vf for vf in vid.get("video_files", []) if vf.get("file_type") == "video/mp4" and vf.get("width", 0) >= 720]
                if files:
                    files.sort(key=lambda x: x.get("width", 0))
                    raw_path = f"raw_clip_{idx}.mp4"
                    with open(raw_path, "wb") as f:
                        f.write(requests.get(files[0]["link"], timeout=25).content)
                    return "video", raw_path
        except Exception as e:
            print(f"Pexels video warning ({query}): {e}")

        try:
            r = requests.get(
                "https://api.pexels.com/v1/search",
                headers=headers,
                params={"query": query, "orientation": "portrait", "per_page": 5},
                timeout=12
            ).json()
            photos = r.get("photos", [])
            if photos:
                img_url = random.choice(photos[:3])["src"]["large2x"]
                img_path = f"raw_img_{idx}.jpg"
                with open(img_path, "wb") as f:
                    f.write(requests.get(img_url, timeout=15).content)
                return "image", img_path
        except Exception as e:
            print(f"Pexels photo warning ({query}): {e}")

    # Fallback to photorealistic Imagen 3 visual tailored to the scene
    ai_img = generate_ai_scene_image(scene.get("ai_visual_prompt", query), idx)
    if ai_img:
        return "image", ai_img

    if article_photo_path and os.path.exists(article_photo_path):
        return "image", article_photo_path

    solid_path = f"solid_{idx}.jpg"
    Image.new("RGB", (1080, 1920), (15, 23, 42)).save(solid_path)
    return "image", solid_path

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

def create_scene_overlay_png(badge, scene_headline_te, scene_num, total_scenes, out_png):
    overlay = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Cinema top & bottom vignette gradients
    for y in range(0, 360):
        alpha = int(205 * (1 - y / 360))
        draw.line([(0, y), (1080, y)], fill=(8, 12, 22, alpha))

    for y in range(1240, 1920):
        alpha = int(240 * ((y - 1240) / 680))
        draw.line([(0, y), (1080, y)], fill=(8, 12, 22, alpha))

    # Top Brand Header
    draw.rounded_rectangle([45, 72, 1035, 168], radius=22, fill=(220, 38, 38, 240))
    brand_font = load_telugu_font(42)
    draw.text((80, 98), "నెల్లూరు డైలీ  •  NELLORE DAILY", fill=(255, 255, 255, 255), font=brand_font)

    # Category Pill + Scene Progress Indicator
    badge_colors = {
        "NELLORE CITY": (14, 165, 233, 245),
        "JOBS ALERT": (22, 163, 74, 245),
        "CRICKET": (37, 99, 235, 245),
        "TOLLYWOOD": (147, 51, 234, 245),
        "TRENDING": (234, 88, 12, 245)
    }
    pill_col = badge_colors.get(badge.upper(), (220, 38, 38, 245))
    draw.rounded_rectangle([45, 192, 430, 252], radius=16, fill=pill_col)
    badge_font = load_telugu_font(30)
    draw.text((72, 204), f"● LIVE  |  {badge.upper()}", fill=(255, 255, 255, 255), font=badge_font)

    # Scene progress bars at top-right
    bar_w = 52
    start_x = 1035 - (total_scenes * (bar_w + 10))
    for i in range(total_scenes):
        col = (250, 204, 21, 255) if i <= scene_num else (255, 255, 255, 90)
        draw.rounded_rectangle([start_x + i * (bar_w + 10), 216, start_x + i * (bar_w + 10) + bar_w, 228], radius=6, fill=col)

    # Kinetic Scene Callout Card (above burned-in subtitles)
    head_font = load_telugu_font(52)
    lines = wrap_text(draw, scene_headline_te, head_font, 910)
    box_h = 36 + len(lines[:2]) * 72
    box_top = 1350 - box_h
    draw.rounded_rectangle([55, box_top, 1025, 1350], radius=22, fill=(15, 23, 42, 215), outline=(250, 204, 21, 245), width=4)
    y = box_top + 18
    for line in lines[:2]:
        draw.text((90, y), line, fill=(250, 204, 21, 255), font=head_font)
        y += 72

    overlay.save(out_png)

def build_cinema_reel(scenes, durations, badge, fallback_news_img_url="", bgm_path=None, out_video="reel.mp4"):
    article_photo_path = "news_lead.jpg"
    has_article_photo = False
    if fallback_news_img_url:
        try:
            r = requests.get(fallback_news_img_url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and len(r.content) > 6000:
                Image.open(io.BytesIO(r.content)).convert("RGB").save(article_photo_path)
                has_article_photo = True
        except Exception:
            pass

    # Cinema color grading filter chain: rich contrast, vibrant warmth, crisp sharpening, subtle vignette
    cinema_grade = "eq=contrast=1.09:brightness=0.02:saturation=1.18,unsharp=5:5:0.75:5:5:0.0,vignette=PI/5"

    scene_clips = []
    total_scenes = len(scenes)
    for idx, (scene, dur) in enumerate(zip(scenes, durations)):
        overlay_png = f"overlay_{idx}.png"
        create_scene_overlay_png(badge, scene.get("on_screen_te", ""), idx, total_scenes, overlay_png)

        media_type, media_path = fetch_scene_visual(scene, idx, article_photo_path if has_article_photo else None)
        clip_out = f"scene_clip_{idx}.mp4"

        if media_type == "video":
            cmd = [
                "ffmpeg", "-y",
                "-stream_loop", "-1", "-i", media_path,
                "-i", overlay_png,
                "-t", f"{dur:.2f}",
                "-filter_complex",
                f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,{cinema_grade}[graded];[graded][1:v]overlay=0:0,format=yuv420p[v]",
                "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-r", "30", clip_out
            ]
        else:
            # Alternating dynamic Ken Burns camera push-in / pull-out + cinema color grading
            zoom_expr = "min(zoom+0.0014,1.14)" if idx % 2 == 0 else "max(1.14-on*0.0014,1.0)"
            frames = int(dur * 30) + 8
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-i", media_path,
                "-i", overlay_png,
                "-t", f"{dur:.2f}",
                "-filter_complex",
                f"[0:v]scale=1240:2204:force_original_aspect_ratio=increase,crop=1240:2204,zoompan=z='{zoom_expr}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30,{cinema_grade}[graded];[graded][1:v]overlay=0:0,format=yuv420p[v]",
                "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-r", "30", clip_out
            ]
        subprocess.run(cmd, check=True)
        scene_clips.append(clip_out)

    # Stitch scenes together with smooth xfade broadcast transitions
    transition_dur = 0.45
    if len(scene_clips) > 1:
        inputs = []
        for c in scene_clips:
            inputs.extend(["-i", c])

        filter_parts = []
        prev_label = "[0:v]"
        offset = durations[0] - transition_dur
        for i in range(1, len(scene_clips)):
            trans = XFADE_TRANSITIONS[(i - 1) % len(XFADE_TRANSITIONS)]
            out_label = f"[v{i}]" if i < len(scene_clips) - 1 else "[vmerged]"
            filter_parts.append(
                f"{prev_label}[{i}:v]xfade=transition={trans}:duration={transition_dur:.2f}:offset={max(0.5, offset):.2f}{out_label}"
            )
            prev_label = out_label
            offset += durations[i] - transition_dur

        subprocess.run([
            "ffmpeg", "-y", *inputs,
            "-filter_complex", ";".join(filter_parts),
            "-map", "[vmerged]", "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "raw_stitched.mp4"
        ], check=True)
    else:
        subprocess.run(["cp", scene_clips[0], "raw_stitched.mp4"], check=True)

    # Burn synced Telugu subtitles + mix voiceover and background music
    sub_style = "FontName=Noto Sans Telugu,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H90000000,BorderStyle=3,Outline=2,Shadow=0,Alignment=2,MarginV=145"
    if bgm_path and os.path.exists(bgm_path):
        cmd_final = [
            "ffmpeg", "-y",
            "-i", "raw_stitched.mp4",
            "-i", "voice.mp3",
            "-stream_loop", "-1", "-i", bgm_path,
            "-filter_complex",
            f"[0:v]subtitles=subs.srt:force_style='{sub_style}'[vout];[2:a]volume=0.14[bgm];[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-c:a", "aac", "-b:a", "192k", "-shortest", out_video
        ]
    else:
        cmd_final = [
            "ffmpeg", "-y",
            "-i", "raw_stitched.mp4",
            "-i", "voice.mp3",
            "-vf", f"subtitles=subs.srt:force_style='{sub_style}'",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-c:a", "aac", "-b:a", "192k", "-shortest", out_video
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
        print("No fresh non-political articles found this hour. Exiting cleanly.")
        return

    story = generate_storyboard_with_gemini(articles)
    print("Selected Cinema Story:", story.get("top_banner_te"))

    selected_img = story.get("selected_image_url", "")
    if not selected_img:
        for art in articles:
            if art["link"] == story["selected_link"] and art.get("image_url"):
                selected_img = art["image_url"]
                break

    durations = asyncio.run(generate_scene_audio_and_subs(story["scenes"]))
    bgm_file = pick_bgm(story.get("bgm_mood", "general"))
    build_cinema_reel(
        scenes=story["scenes"],
        durations=durations,
        badge=story.get("category_badge", "NELLORE CITY"),
        fallback_news_img_url=selected_img,
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
