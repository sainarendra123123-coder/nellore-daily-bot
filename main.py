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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import feedparser
import requests
import edge_tts
from PIL import Image, ImageDraw, ImageFont
from google import genai
from google.genai import types

# --- REQUIRED SECRETS ---
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
META_ACCESS_TOKEN = os.environ["META_ACCESS_TOKEN"]
FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "")
IG_USER_ID = os.environ["IG_USER_ID"]
PREVIEW_ONLY = os.environ.get("PREVIEW_ONLY", "false").lower() == "true"

# --- OPTIONAL MULTI-API VIDEO KEYS (AUTOMATIC WATERFALL) ---
FAL_KEY = os.environ.get("FAL_KEY", "").strip()
REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN", "").strip()
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "").strip()
GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()

IST = timezone(timedelta(hours=5, minutes=30))
HISTORY_FILE = "posted_history.json"
HOURLY_INSTAGRAM = os.environ.get("HOURLY_INSTAGRAM") == "true"
NUM_SHOTS = 8 if HOURLY_INSTAGRAM else 6

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
EXHAUSTED_PROVIDERS = set()


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
        return not HOURLY_INSTAGRAM
    try:
        return ((time.time() - time.mktime(parsed)) / 3600.0) <= max_age_hours
    except Exception:
        return not HOURLY_INSTAGRAM


def fetch_latest_news(history):
    articles = []
    seen_links = set(history)
    categories = list(CATEGORY_FEEDS.keys())
    slot = int(time.time() // 3600) % len(categories)
    ordered_cats = categories[slot:] + categories[:slot]

    for category in ordered_cats:
        for url in CATEGORY_FEEDS[category]:
            try:
                resp = requests.get(url, timeout=10, headers=BROWSER_HEADERS)
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
   - Write ONE smooth, engaging, conversational 85-to-95 word conversational Telugu script with familiar basic English when natural. Tell a 40-second story with a hook, context, what happened, why it matters, and a question for followers. Use only facts supported by the selected item.
   - Use everyday spoken Nellore Telugu (e.g., "మన నెల్లూరు వాళ్లకి...", "మన ఆటో అన్నలకు...").
5. EXACTLY {NUM_SHOTS} DIFFERENT VISUAL SHOTS ("shots" array of {NUM_SHOTS} objects):
   - Each object MUST have:
     a) "ai_video_prompt": Vivid 9:16 cinematic motion prompt in South India / Andhra Pradesh (e.g., "Cinematic handheld shot tracking yellow auto rickshaws driving down a sunny tree-lined avenue in Nellore India, clear blue sky at top, golden sunlight, 9:16 vertical, no text").
     b) "stock_query": A unique 2-to-3 word English stock video/photo query (e.g., "Indian auto rickshaw", "city traffic", "cricket stadium", "gold jewelry", "office interview", "students studying"). All {NUM_SHOTS} MUST be different!
6. METADATA:
   - "category_badge": One of "NELLORE CITY", "JOBS ALERT", "CRICKET", "TOLLYWOOD", "TRENDING".
   - "caption": Detailed, helpful Instagram & Facebook caption in Telugu + English with emojis, bullet points, source credit, and hashtags.
   - "bgm_mood": One of "emotional", "breaking", "devotional", "general".

Return strictly valid JSON with keys:
"selected_link", "category_badge", "hook_te", "narration_te", "shots" (array of {NUM_SHOTS} objects with "ai_video_prompt", "stock_query"), "caption", "bgm_mood"
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
                    data.get("selected_link") in {article["link"] for article in articles}
                    and data.get("narration_te")
                    and isinstance(data.get("shots"), list)
                    and len(data["shots"]) == NUM_SHOTS
                ):
                    return data
            except Exception as e:
                last_err = e
                print(f"Gemini ({model_name}) attempt {attempt + 1} failed: {e}")
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"All Gemini models failed: {last_err}")


def normalize_clip_to_portrait(raw_path, out_mp4, duration=3.5):
    subprocess.run(
        [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", raw_path, "-t", f"{duration:.2f}",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p",
            "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21", out_mp4,
        ],
        check=True,
    )
    return out_mp4


# --- PROVIDER 1: GOOGLE CLOUD VERTEX AI VEO 2 / VEO 3 ---
def try_vertex_veo_clip(prompt, out_mp4):
    if not GOOGLE_CLOUD_PROJECT or "vertex_veo" in EXHAUSTED_PROVIDERS:
        return None
    try:
        client = genai.Client(vertexai=True, project=GOOGLE_CLOUD_PROJECT, location="us-central1")
        op = client.models.generate_videos(
            model="veo-2.0-generate-001",
            prompt=prompt,
            config=types.GenerateVideosConfig(aspect_ratio="9:16", duration_seconds=5),
        )
        for _ in range(18):
            if op.done:
                break
            time.sleep(8)
            op = client.operations.get(op)
        if op.response and op.response.generated_videos:
            vid_bytes = client.files.download(file=op.response.generated_videos[0].video)
            raw = out_mp4.replace(".mp4", "_vtx.mp4")
            with open(raw, "wb") as f:
                f.write(vid_bytes)
            print(f"[Provider 1: Vertex Veo 2] Generated clip: {out_mp4}")
            return normalize_clip_to_portrait(raw, out_mp4)
    except Exception as e:
        print(f"[Provider 1: Vertex Veo 2] Exhausted/Unavailable -> switching: {e}")
        EXHAUSTED_PROVIDERS.add("vertex_veo")
    return None


# --- PROVIDER 2: FAL.AI (WAN 2.1 / LTX-VIDEO) ---
def try_fal_video_clip(prompt, out_mp4):
    if not FAL_KEY or "fal" in EXHAUSTED_PROVIDERS:
        return None
    headers = {"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"}
    for endpoint in ("fal-ai/wan-t2v", "fal-ai/ltx-video"):
        try:
            submit = requests.post(
                f"https://queue.fal.run/{endpoint}",
                headers=headers,
                json={"prompt": prompt, "aspect_ratio": "9:16"},
                timeout=15,
            )
            if submit.status_code in (401, 402, 403, 429):
                print(f"[Provider 2: fal.ai] Free credits exhausted ({submit.status_code}) -> switching.")
                EXHAUSTED_PROVIDERS.add("fal")
                return None
            data = submit.json()
            status_url = data.get("status_url")
            response_url = data.get("response_url")
            if not status_url or not response_url:
                continue
            for _ in range(18):
                time.sleep(4)
                st = requests.get(status_url, headers=headers, timeout=12).json()
                if st.get("status") == "COMPLETED":
                    res = requests.get(response_url, headers=headers, timeout=15).json()
                    vid_url = (res.get("video") or {}).get("url")
                    if vid_url:
                        raw = out_mp4.replace(".mp4", "_fal.mp4")
                        with open(raw, "wb") as f:
                            f.write(requests.get(vid_url, timeout=20).content)
                        print(f"[Provider 2: fal.ai ({endpoint})] Generated clip: {out_mp4}")
                        return normalize_clip_to_portrait(raw, out_mp4)
                elif st.get("status") == "FAILED":
                    break
        except Exception as e:
            print(f"[Provider 2: fal.ai] Notice: {e}")
    return None


# --- PROVIDER 3: REPLICATE (WAN 2.1 T2V) ---
def try_replicate_video_clip(prompt, out_mp4):
    if not REPLICATE_API_TOKEN or "replicate" in EXHAUSTED_PROVIDERS:
        return None
    headers = {"Authorization": f"Bearer {REPLICATE_API_TOKEN}", "Content-Type": "application/json"}
    try:
        r = requests.post(
            "https://api.replicate.com/v1/models/wan-video/wan-2.1-t2v-480p/predictions",
            headers=headers,
            json={"input": {"prompt": prompt, "aspect_ratio": "9:16"}},
            timeout=15,
        )
        if r.status_code in (401, 402, 403, 429):
            print(f"[Provider 3: Replicate] Free credits exhausted ({r.status_code}) -> switching.")
            EXHAUSTED_PROVIDERS.add("replicate")
            return None
        get_url = (r.json().get("urls") or {}).get("get")
        if not get_url:
            return None
        for _ in range(18):
            time.sleep(5)
            st = requests.get(get_url, headers=headers, timeout=12).json()
            if st.get("status") == "succeeded":
                out_url = st.get("output")
                if isinstance(out_url, list):
                    out_url = out_url[0]
                if out_url:
                    raw = out_mp4.replace(".mp4", "_rep.mp4")
                    with open(raw, "wb") as f:
                        f.write(requests.get(out_url, timeout=20).content)
                    print(f"[Provider 3: Replicate] Generated clip: {out_mp4}")
                    return normalize_clip_to_portrait(raw, out_mp4)
            elif st.get("status") in ("failed", "canceled"):
                break
    except Exception as e:
        print(f"[Provider 3: Replicate] Notice: {e}")
    return None


# --- PROVIDER 4: HUGGING FACE INFERENCE API (WAN 2.1 / LTX-VIDEO) ---
def try_huggingface_video_clip(prompt, out_mp4):
    if not HF_TOKEN or "huggingface" in EXHAUSTED_PROVIDERS:
        return None
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    for model_id in ("Wan-AI/Wan2.1-T2V-1.3B", "Lightricks/LTX-Video"):
        try:
            r = requests.post(
                f"https://api-inference.huggingface.co/models/{model_id}",
                headers=headers,
                json={"inputs": prompt},
                timeout=45,
            )
            if r.status_code in (401, 402, 403, 429):
                print(f"[Provider 4: Hugging Face] Free quota exhausted ({r.status_code}) -> switching.")
                EXHAUSTED_PROVIDERS.add("huggingface")
                return None
            if r.status_code == 200 and len(r.content) > 150_000:
                raw = out_mp4.replace(".mp4", "_hf.mp4")
                with open(raw, "wb") as f:
                    f.write(r.content)
                print(f"[Provider 4: Hugging Face ({model_id})] Generated clip: {out_mp4}")
                return normalize_clip_to_portrait(raw, out_mp4)
        except Exception as e:
            print(f"[Provider 4: Hugging Face] Notice: {e}")
    return None


# --- PROVIDER 5: PEXELS FREE PORTRAIT HD VIDEO API ---
def try_pexels_video_clip(query, out_mp4, used_urls):
    if not PEXELS_API_KEY or "pexels" in EXHAUSTED_PROVIDERS:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "orientation": "portrait", "size": "medium", "per_page": 8},
            timeout=10,
        )
        if r.status_code in (401, 403, 429):
            print(f"[Provider 5: Pexels] Rate/quota limit ({r.status_code}) -> switching.")
            EXHAUSTED_PROVIDERS.add("pexels")
            return None
        for v in r.json().get("videos", []):
            files = sorted(v.get("video_files", []), key=lambda x: x.get("width", 0), reverse=True)
            for vf in files:
                link = vf.get("link", "")
                if link and link not in used_urls and vf.get("file_type") == "video/mp4" and vf.get("width", 0) <= 1920:
                    raw = out_mp4.replace(".mp4", "_px.mp4")
                    vr = requests.get(link, timeout=15)
                    if vr.status_code == 200 and len(vr.content) > 200_000:
                        with open(raw, "wb") as f:
                            f.write(vr.content)
                        used_urls.add(link)
                        print(f"[Provider 5: Pexels Portrait HD] Downloaded ({query}): {out_mp4}")
                        return normalize_clip_to_portrait(raw, out_mp4)
    except Exception as e:
        print(f"[Provider 5: Pexels] Notice: {e}")
    return None


# --- PROVIDER 6: PARALLEL ZERO-KEY MIXKIT HD VIDEOS + WIKIMEDIA/BING MOTION FALLBACK ---
def _download_and_norm_mixkit(args):
    idx, link = args
    raw = f"fb_mix_{idx}.mp4"
    out_mp4 = f"fb_clip_{idx}.mp4"
    try:
        vr = requests.get(link, headers=BROWSER_HEADERS, timeout=12)
        if vr.status_code == 200 and len(vr.content) > 220_000:
            with open(raw, "wb") as f:
                f.write(vr.content)
            return normalize_clip_to_portrait(raw, out_mp4)
    except Exception:
        pass
    return None


def _download_and_norm_photo(args):
    idx, u, motion_filter = args
    jpg_p = f"fb_img_{idx}.jpg"
    mp4_p = f"fb_mot_{idx}.mp4"
    try:
        vr = requests.get(u, headers=BROWSER_HEADERS, timeout=8)
        if vr.status_code == 200 and len(vr.content) > 22_000:
            img = Image.open(io.BytesIO(vr.content)).convert("RGB")
            if img.width >= 500 and img.height >= 350:
                img.save(jpg_p, quality=92)
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-t", "3.5",
                        "-i", jpg_p, "-vf", motion_filter,
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21", mp4_p,
                    ],
                    check=True,
                )
                return mp4_p
    except Exception:
        pass
    return None


def gather_zero_key_fallback_clips(queries, needed, used_urls):
    clips = []
    candidate_vid_links = []
    for q in queries[:4]:
        try:
            slug = urllib.parse.quote_plus(q.strip())
            r = requests.get(f"https://mixkit.co/free-stock-video/{slug}/", headers=BROWSER_HEADERS, timeout=8)
            for link in re.findall(r'https://assets\.mixkit\.co/videos/[^"\'\s]+\.mp4', r.text):
                if link not in used_urls and link not in candidate_vid_links:
                    candidate_vid_links.append(link)
        except Exception:
            continue

    if candidate_vid_links:
        with ThreadPoolExecutor(max_workers=4) as ex:
            tasks = [(i, link) for i, link in enumerate(candidate_vid_links[: needed + 2])]
            for res in as_completed([ex.submit(_download_and_norm_mixkit, t) for t in tasks]):
                mp4 = res.result()
                if mp4 and len(clips) < needed:
                    clips.append(mp4)
                    print(f"[Provider 6A: Mixkit HD] Ready: {mp4}")

    if len(clips) < needed:
        rem = needed - len(clips)
        candidate_imgs = []
        for q in queries:
            if len(candidate_imgs) >= rem * 3:
                break
            try:
                r = requests.get(
                    "https://commons.wikimedia.org/w/api.php",
                    params={
                        "action": "query",
                        "generator": "search",
                        "gsrsearch": f"filetype:bitmap {q}",
                        "gsrnamespace": "6",
                        "gsrlimit": "6",
                        "prop": "imageinfo",
                        "iiprop": "url",
                        "iiurlwidth": "1280",
                        "format": "json",
                    },
                    headers={"User-Agent": "NelloreDailyBot/15.0 (https://instagram.com/nelloredaily)"},
                    timeout=8,
                ).json()
                for p in r.get("query", {}).get("pages", {}).values():
                    if p.get("imageinfo"):
                        u = p["imageinfo"][0].get("thumburl") or p["imageinfo"][0].get("url")
                        if u and u not in used_urls and u not in candidate_imgs and any(u.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png")):
                            candidate_imgs.append(u)
            except Exception:
                pass
            try:
                q_enc = urllib.parse.quote_plus(f"{q} photography")
                resp = requests.get(
                    f"https://www.bing.com/images/search?q={q_enc}&qft=+filterui:imagesize-large+filterui:photo-photo",
                    headers=BROWSER_HEADERS,
                    timeout=8,
                )
                for u in re.findall(r'murl&quot;:&quot;(https?://[^&]+?\.(?:jpg|jpeg|png))', resp.text, re.I):
                    if u not in used_urls and u not in candidate_imgs:
                        candidate_imgs.append(u)
            except Exception:
                pass

        motion_filters = [
            "scale=1220:2168,crop=1080:1920:'min(140, n*1.8)':'min(248, n*3.0)',format=yuv420p",
            "scale=1220:2168,crop=1080:1920:'max(0, 140-n*1.8)':'max(0, 248-n*3.0)',format=yuv420p",
            "scale=1240:2204,crop=1080:1920:'min(160, n*2.0)':142,format=yuv420p",
            "scale=1240:2204,crop=1080:1920:'max(0, 160-n*2.0)':142,format=yuv420p",
        ]
        with ThreadPoolExecutor(max_workers=4) as ex:
            tasks = [
                (i, u, motion_filters[i % len(motion_filters)])
                for i, u in enumerate(candidate_imgs[: rem + 4])
            ]
            for res in as_completed([ex.submit(_download_and_norm_photo, t) for t in tasks]):
                mp4 = res.result()
                if mp4 and len(clips) < needed:
                    clips.append(mp4)
                    print(f"[Provider 6B: Motion Photo] Ready: {mp4}")

    return clips


def gather_six_waterfall_clips(shots, category="NELLORE CITY"):
    """Chains all available video APIs in waterfall order until 6 distinct clips are collected."""
    clips = []
    used_urls = set()
    cat_defaults = {
        "CRICKET": ["cricket match", "stadium lights", "sports crowd", "cricket ball"],
        "TOLLYWOOD": ["cinema projector", "movie theater", "city night lights", "festival crowd"],
        "JOBS ALERT": ["job interview", "office laptop", "students studying", "corporate building"],
        "TRENDING": ["gold jewelry", "gold coins", "indian market", "city traffic"],
        "NELLORE CITY": ["indian street", "city traffic", "temple bells", "river sunset"],
    }
    extra_queries = cat_defaults.get(category.upper(), cat_defaults["NELLORE CITY"])

    for idx in range(NUM_SHOTS):
        shot = shots[idx] if idx < len(shots) else {}
        ai_prompt = shot.get("ai_video_prompt", "Cinematic sunlit street in South India with natural motion, 9:16 vertical")
        stock_q = shot.get("stock_query", extra_queries[idx % len(extra_queries)])
        out_mp4 = f"final_shot_{idx}.mp4"

        picked = (
            try_vertex_veo_clip(ai_prompt, out_mp4)
            or try_fal_video_clip(ai_prompt, out_mp4)
            or try_replicate_video_clip(ai_prompt, out_mp4)
            or try_huggingface_video_clip(ai_prompt, out_mp4)
            or try_pexels_video_clip(stock_q, out_mp4, used_urls)
        )
        if picked:
            clips.append(picked)

    if len(clips) < NUM_SHOTS:
        all_queries = [s.get("stock_query", "") for s in shots if s.get("stock_query")] + extra_queries
        clips.extend(gather_zero_key_fallback_clips(all_queries, NUM_SHOTS - len(clips), used_urls))

    if not clips:
        raise RuntimeError("All video providers and fallbacks failed to produce clips.")

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


async def generate_voice_with_word_timings(narration_te, out_audio="voice.mp3"):
    full_script = re.sub(r"#\S+", "", narration_te or "")
    full_script = re.sub(r"[•|★☆🔥⚠️✅📍📅⏰🎬🏏💼👇]", " ", full_script)
    full_script = re.sub(r"\s+", " ", full_script).strip()

    communicate = edge_tts.Communicate(full_script, "te-IN-ShrutiNeural", rate="-2%", boundary="WordBoundary")
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


def build_fast_reel(total_dur, word_events, clip_paths, hook_te, bgm_path=None, out_video="reel.mp4"):
    """Instant single-pass concat of 6 distinct clips + live karaoke captions (~15s total render)."""
    num_clips = len(clip_paths)
    shot_dur = total_dur / num_clips

    if HOURLY_INSTAGRAM:
        adjusted_clips = []
        for index, clip in enumerate(clip_paths):
            adjusted = f"scene_{index}.mp4"
            subprocess.run([
                "ffmpeg", "-loglevel", "error", "-y", "-i", clip,
                "-vf", f"tpad=stop_mode=clone:stop_duration={shot_dur:.3f}",
                "-t", f"{shot_dur:.3f}", "-an", "-c:v", "libx264",
                "-preset", "ultrafast", "-pix_fmt", "yuv420p", adjusted,
            ], check=True)
            adjusted_clips.append(adjusted)
        clip_paths = adjusted_clips

    with open("concat.txt", "w") as cf:
        for cpath in clip_paths:
            cf.write(f"file '{os.path.abspath(cpath)}'\n")
            cf.write(f"duration {shot_dur:.2f}\n")

    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt",
            "-t", f"{total_dur:.2f}", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21",
            "-pix_fmt", "yuv420p", "base_video.mp4",
        ],
        check=True,
    )

    ov_fps = 8
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
            "-preset", "ultrafast", "-crf", "21", "-pix_fmt", "yuv420p",
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
            "-preset", "ultrafast", "-crf", "21", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
            "-movflags", "+faststart", "-shortest", out_video,
        ]
    if HOURLY_INSTAGRAM:
        cmd_final[-2:] = ["-af", "apad", "-t", f"{total_dur:.2f}", out_video]
        cmd_final[1:1] = ["-filter_complex_threads", "1"]
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


def gather_hourly_clips(shots):
    clips = []
    used_urls = set()
    colors = ("14213d", "263251", "1b363e", "373059", "233857", "402d46", "223d3b", "2d3457")
    for index, shot in enumerate(shots):
        output = f"hourly_shot_{index}.mp4"
        clip = try_pexels_video_clip(shot["stock_query"], output, used_urls)
        if not clip:
            subprocess.run([
                "ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                f"color=c=0x{colors[index]}:s=1080x1920:r=30:d=5",
                "-vf", "drawgrid=w=120:h=120:t=1:c=white@0.08,"
                "drawbox=x=80:y=280:w=920:h=7:c=0xf2b447:t=fill",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", output,
            ], check=True)
            clip = output
        clips.append(clip)
    return clips


def publish_hosted_reel(caption, history, source, slot):
    graph = "https://graph.facebook.com/v25.0/"
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}"}

    def meta(path, fields, method="GET"):
        response = requests.request(
            method, graph + path, headers=headers,
            params=fields if method == "GET" else None,
            data=fields if method == "POST" else None,
            timeout=30,
        )
        payload = response.json()
        if not response.ok or "error" in payload:
            raise RuntimeError(
                f"Instagram {path.rsplit('/', 1)[-1]} failed: "
                f"HTTP {response.status_code}, Meta code {payload.get('error', {}).get('code')}"
            )
        return payload

    if not IG_USER_ID.isdigit() or meta(IG_USER_ID, {"fields": "username"}).get("username", "").lower() != "nelloredaily":
        raise RuntimeError("Configured Instagram account must be @nelloredaily.")
    tag = f"hourly-reel-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    subprocess.run([
        "gh", "release", "create", tag, "reel.mp4", "--prerelease",
        "--target", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "--title", "Nellore Daily reel upload",
    ], check=True)
    try:
        url = f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/releases/download/{tag}/reel.mp4"
        container = meta(f"{IG_USER_ID}/media", {
            "media_type": "REELS", "video_url": url, "caption": caption,
        }, "POST")["id"]
        for _ in range(35):
            status = meta(container, {"fields": "status_code"}).get("status_code")
            if status == "FINISHED":
                break
            if status in {"ERROR", "EXPIRED"}:
                raise RuntimeError(f"Instagram video processing ended with {status}.")
            time.sleep(5)
        else:
            raise RuntimeError("Instagram video processing timed out.")

        # Record the slot and source before publishing so an ambiguous response cannot duplicate a post.
        save_history([*history, source, slot])
        subprocess.run(["git", "add", HISTORY_FILE], check=True)
        subprocess.run([
            "git", "-c", "user.name=Nellore Daily",
            "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "-m", "Record hourly Instagram publication attempt [skip ci]",
        ], check=True)
        subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)

        media_id = meta(f"{IG_USER_ID}/media_publish", {"creation_id": container}, "POST")["id"]
        print(f"Published Instagram reel: {media_id}", flush=True)
        print(meta(media_id, {"fields": "permalink"}).get("permalink", ""), flush=True)
    finally:
        subprocess.run(["gh", "release", "delete", tag, "--yes", "--cleanup-tag"], check=False)


def main():
    history = load_history()
    slot = datetime.now(IST).strftime("instagram-hour:%Y-%m-%dT%H")
    if HOURLY_INSTAGRAM and not PREVIEW_ONLY and slot in history:
        print(f"Instagram publication already attempted for {slot}.")
        return
    articles = fetch_latest_news(history)
    if not articles:
        if HOURLY_INSTAGRAM:
            raise RuntimeError("No fresh, unused non-political articles found.")
        print("No fresh non-political articles found this hour. Exiting cleanly.")
        return

    story = generate_storyboard_with_gemini(articles)
    print("Selected Hook:", story.get("hook_te"))

    cat_badge = story.get("category_badge", "NELLORE CITY")
    clip_paths = (
        gather_hourly_clips(story["shots"]) if HOURLY_INSTAGRAM
        else gather_six_waterfall_clips(story["shots"], category=cat_badge)
    )
    total_dur, word_events = asyncio.run(generate_voice_with_word_timings(story["narration_te"]))
    bgm_file = None if HOURLY_INSTAGRAM else pick_bgm(story.get("bgm_mood", "emotional"))
    if HOURLY_INSTAGRAM:
        scale = 38 / total_dur
        if scale < 1:
            subprocess.run([
                "ffmpeg", "-loglevel", "error", "-y", "-i", "voice.mp3",
                "-af", f"atempo={scale ** -1:.5f}", "voice-adjusted.mp3",
            ], check=True)
            os.replace("voice-adjusted.mp3", "voice.mp3")
            for event in word_events:
                event["start"] *= scale
                event["end"] *= scale
        total_dur = 40

    build_fast_reel(
        total_dur=total_dur,
        word_events=word_events,
        clip_paths=clip_paths,
        hook_te=story.get("hook_te", "తాజా వార్త!"),
        bgm_path=bgm_file,
    )

    if PREVIEW_ONLY:
        print("PREVIEW_ONLY=true — download reel.mp4 from Artifacts to inspect!")
        return

    if HOURLY_INSTAGRAM:
        caption = story["caption"][:1750] + (
            "\nదృశ్యాలు విషయాన్ని వివరించడానికి మాత్రమే. @nelloredaily ని ఫాలో అవ్వండి.\n"
            + story["selected_link"]
        )
        publish_hosted_reel(caption[:2200], history, story["selected_link"], slot)
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
    print(f"Hourly Fast Multi-API Reel completed! (IG={ig_ok}, FB={fb_ok})")


if __name__ == "__main__":
    main()
