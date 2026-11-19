import os
import json
import time
import asyncio
import random
import subprocess
import feedparser
import requests
import edge_tts
from PIL import Image, ImageDraw
from google import genai
from google.genai import types

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
META_ACCESS_TOKEN = os.environ["META_ACCESS_TOKEN"]
FB_PAGE_ID = os.environ["FB_PAGE_ID"]
IG_USER_ID = os.environ["IG_USER_ID"]
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "")

HISTORY_FILE = "posted_history.json"
RSS_FEEDS = [
    "https://www.eenadu.net/rss/andhra-pradesh.xml",
    "https://www.sakshi.com/rss/andhra-pradesh",
    "https://telugu.samayam.com/rssfeedsdefault.cms",
    "https://news.google.com/rss/search?q=Nellore+OR+%E0%B0%A8%E0%B1%86%E0%B0%B2%E0%B1%8D%E0%B0%B2%E0%B1%82%E0%B0%B0%E0%B1%81&hl=te&gl=IN&ceid=IN:te"
]

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-200:], f, ensure_ascii=False, indent=2)

def fetch_latest_news(history):
    articles = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                link = entry.get("link", "")
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                if link and link not in history:
                    articles.append({
                        "title": title,
                        "summary": summary[:400],
                        "link": link
                    })
        except Exception as e:
            print(f"Feed error {url}: {e}")
    return articles[:15]

def generate_script_with_gemini(articles):
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
    You are the chief Telugu news editor for 'Nellore Daily' (నెల్లూరు డైలీ).
    Review these latest news items:
    {json.dumps(articles, ensure_ascii=False)}

    Tasks:
    1. Pick the single most important, verified news story relevant to Nellore district or Andhra Pradesh. Prioritize stories corroborated across multiple items or from official/reliable sources. Reject rumors.
    2. Write a punchy, natural 35-to-50 word Telugu news voiceover script (spoken style, clear pronunciation).
    3. Write a short Telugu headline (max 8 words) for the video banner.
    4. Write an engaging Instagram/Facebook caption in Telugu + English with relevant hashtags (#Nellore #NelloreDaily #AndhraPradesh #TeluguNews).
    5. Choose a BGM mood from: "breaking", "emotional", "devotional", "general".

    Return strictly valid JSON with keys:
    "selected_link", "headline_te", "voiceover_te", "caption", "bgm_mood"
    """
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.3
        )
    )
    return json.loads(response.text)

async def create_voiceover(text, output_path="voice.mp3"):
    voice = random.choice(["te-IN-MohanNeural", "te-IN-ShrutiNeural"])
    communicate = edge_tts.Communicate(text, voice, rate="+5%")
    await communicate.save(output_path)

def build_background_image(headline_te, output_img="bg.png"):
    img = Image.new("RGB", (1080, 1920), color=(15, 23, 42))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 140, 1080, 260], fill=(220, 38, 38))
    draw.rectangle([60, 420, 1020, 1480], fill=(30, 41, 59), outline=(239, 68, 68), width=6)
    draw.rectangle([0, 1660, 1080, 1780], fill=(185, 28, 28))
    img.save(output_img)

def pick_bgm(mood):
    if not os.path.isdir("bgm"):
        return None
    files = [f for f in os.listdir("bgm") if f.endswith(".mp3")]
    if not files:
        return None
    for f in files:
        if mood in f.lower():
            return os.path.join("bgm", f)
    return os.path.join("bgm", random.choice(files))

def render_video(voice_path="voice.mp3", bg_img="bg.png", bgm_path=None, out_video="reel.mp4"):
    if bgm_path and os.path.exists(bgm_path):
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", bg_img,
            "-i", voice_path,
            "-stream_loop", "-1", "-i", bgm_path,
            "-filter_complex",
            "[2:a]volume=0.15[bgm];[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", out_video
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", bg_img,
            "-i", voice_path,
            "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", out_video
        ]
    subprocess.run(cmd, check=True)

def host_video_temporarily(video_path):
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    tag = f"reel-{int(time.time())}"
    rel_resp = requests.post(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases",
        headers=headers,
        json={"tag_name": tag, "name": tag, "prerelease": True}
    )
    rel_data = rel_resp.json()
    upload_url = rel_data["upload_url"].split("{")[0]
    release_id = rel_data["id"]

    with open(video_path, "rb") as f:
        up_resp = requests.post(
            f"{upload_url}?name=reel.mp4",
            headers={**headers, "Content-Type": "video/mp4"},
            data=f.read()
        )
    download_url = up_resp.json()["browser_download_url"]
    return download_url, release_id, tag

def cleanup_temp_release(release_id, tag):
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
    requests.delete(f"https://api.github.com/repos/{GITHUB_REPO}/releases/{release_id}", headers=headers)
    requests.delete(f"https://api.github.com/repos/{GITHUB_REPO}/git/refs/tags/{tag}", headers=headers)

def publish_to_instagram(video_url, caption):
    r1 = requests.post(
        f"https://graph.facebook.com/v21.0/{IG_USER_ID}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": META_ACCESS_TOKEN
        }
    ).json()
    creation_id = r1.get("id")
    if not creation_id:
        raise RuntimeError(f"IG container error: {r1}")

    for _ in range(15):
        time.sleep(10)
        status = requests.get(
            f"https://graph.facebook.com/v21.0/{creation_id}",
            params={"fields": "status_code", "access_token": META_ACCESS_TOKEN}
        ).json()
        if status.get("status_code") == "FINISHED":
            break

    r2 = requests.post(
        f"https://graph.facebook.com/v21.0/{IG_USER_ID}/media_publish",
        data={"creation_id": creation_id, "access_token": META_ACCESS_TOKEN}
    ).json()
    print("Instagram Reel Published:", r2)

def publish_to_facebook_page(video_path, caption):
    acct = requests.get(
        f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}",
        params={"fields": "access_token", "access_token": META_ACCESS_TOKEN}
    ).json()
    page_token = acct.get("access_token", META_ACCESS_TOKEN)

    with open(video_path, "rb") as vf:
        r = requests.post(
            f"https://graph-video.facebook.com/v21.0/{FB_PAGE_ID}/videos",
            data={"description": caption, "access_token": page_token},
            files={"source": vf}
        ).json()
    print("Facebook Page Video Published:", r)

def main():
    history = load_history()
    articles = fetch_latest_news(history)
    if not articles:
        print("No new articles found this hour.")
        return

    news_data = generate_script_with_gemini(articles)
    print("Selected News:", news_data["headline_te"])

    asyncio.run(create_voiceover(news_data["voiceover_te"]))
    build_background_image(news_data["headline_te"])
    bgm_file = pick_bgm(news_data.get("bgm_mood", "general"))
    render_video(bgm_path=bgm_file)

    video_url, release_id, tag = host_video_temporarily("reel.mp4")
    try:
        publish_to_instagram(video_url, news_data["caption"])
        publish_to_facebook_page("reel.mp4", news_data["caption"])
    finally:
        cleanup_temp_release(release_id, tag)

    history.append(news_data["selected_link"])
    save_history(history)
    print("Hourly run completed successfully!")

if __name__ == "__main__":
    main()
