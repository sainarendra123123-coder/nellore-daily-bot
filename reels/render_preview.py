import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


PROVIDERS = (
    ("Lightricks LTX", "https://lightricks-ltx-video-distilled.hf.space", False),
    ("DeepRat LTX", "https://deeprat-ltx-video-zerogpu-optimized.hf.space", True),
    ("OpenKing Wan", "https://openking-wan2-video-generation.hf.space", None),
)
SHOT_SECONDS = 8


class SameHost(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if (urlparse(newurl).scheme, urlparse(newurl).netloc) != (
            urlparse(req.full_url).scheme, urlparse(req.full_url).netloc
        ):
            raise ValueError("Cross-origin redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


opener = urllib.request.build_opener(SameHost())


def download_shot(provider, prompt, destination):
    _, base, extra_argument = provider
    args = [prompt, "still image, stutter, text, distorted anatomy", "", "",
            832, 480, "text-to-video", SHOT_SECONDS, 9, 0, True, 1, True]
    if extra_argument:
        args.append(False)
    method = "text_to_video"
    if extra_argument is None:
        method = "generate_video"
        args = [prompt, None, 480, 832, 193, 20, 5, -1]
    endpoint = base + "/gradio_api/call/" + method
    request = urllib.request.Request(endpoint, json.dumps({"data": args}).encode(),
                                     {"Content-Type": "application/json"})
    with opener.open(request, timeout=20) as response:
        event = json.load(response)["event_id"]
    if not re.fullmatch("[a-zA-Z0-9_-]{1,100}", event):
        raise ValueError("Invalid generation event")
    output = None
    with opener.open(endpoint + "/" + event, timeout=25) as response:
        from time import monotonic
        deadline = monotonic() + 100
        for raw in response:
            if monotonic() >= deadline:
                raise TimeoutError("Generation deadline exceeded")
            line = raw.decode().strip()
            if line == "event: error":
                raise ValueError("Provider rejected generation")
            if line == "event: complete":
                output = json.loads(response.readline().decode().removeprefix("data: "))
                break
    clip = output[0] if isinstance(output, list) and output else None
    if not isinstance(clip, dict):
        raise ValueError("Provider returned no clip")
    media = clip.get("video") or clip
    if not isinstance(media, dict) or not isinstance(media.get("url"), str):
        raise ValueError("Provider returned an invalid clip reference")
    url = media["url"]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != urlparse(base).netloc:
        raise ValueError("Unexpected clip origin")
    with opener.open(url, timeout=25) as response:
        data = response.read(32 * 1024 * 1024 + 1)
    if not data or len(data) > 32 * 1024 * 1024:
        raise ValueError("Clip download size outside limit")
    destination.write_bytes(data)


def command(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=120)


def validate_motion(path):
    probe = command("ffprobe", "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=width,height,duration",
                    "-of", "json", str(path))
    track = json.loads(probe.stdout)["streams"][0]
    if (float(track["duration"]) < SHOT_SECONDS or track["width"] < 400
            or track["height"] <= track["width"]):
        raise ValueError("Need at least eight seconds of portrait motion at usable resolution")
    result = command("ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-xerror",
                     "-i", str(path), "-t", str(SHOT_SECONDS),
                     "-vf", "freezedetect=n=-48dB:d=0.75", "-an", "-f", "null", "-")
    if "freeze_start" in result.stderr:
        raise ValueError("Detected a held frame")


def choose_shot(prompt, destination, providers=PROVIDERS, generate=download_shot,
                validate=validate_motion):
    for provider in providers:
        try:
            generate(provider, prompt, destination)
            validate(destination)
            return provider[0]
        except (OSError, ValueError, KeyError, TypeError, IndexError,
                subprocess.SubprocessError) as error:
            destination.unlink(missing_ok=True)
            print(f"{provider[0]} failed the shot ({type(error).__name__})", file=sys.stderr)
    raise RuntimeError("All enabled providers failed to return usable motion")


def render(plan_path, output):
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    scenes = plan["scenes"]
    if len(scenes) != 5 or any(not scene.get("prompt", "").strip() for scene in scenes):
        raise ValueError("A 40-second reel needs five scene prompts")
    base = plan_path.parent
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as directory:
        work = Path(directory)
        sources = []
        for index, scene in enumerate(scenes):
            raw = work / f"raw-{index}.mp4"
            if scene.get("clip"):
                shutil.copyfile(base / scene["clip"], raw)
                validate_motion(raw)
            else:
                print(f"Scene {index + 1}: {choose_shot(scene['prompt'], raw)}")
            digest = hashlib.sha256(raw.read_bytes()).hexdigest()
            if digest in sources:
                raise ValueError("Duplicate clip in this reel")
            sources.append(digest)
            command("ffmpeg", "-v", "error", "-y", "-i", str(raw),
                    "-t", str(SHOT_SECONDS), "-an", "-vf",
                    "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,fps=30",
                    "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                    str(work / f"scene-{index}.mp4"))
        clips = work / "clips.txt"
        clips.write_text("".join(f"file 'scene-{i}.mp4'\n" for i in range(5)))
        captions = base / plan["captions"]
        text = captions.read_text(encoding="utf-8")
        if not re.search("[\u0c05-\u0c39]", text) or not re.search("[a-zA-Z]{3}", text):
            raise ValueError("Subtitles must contain Telugu and everyday English")
        shutil.copyfile(captions, work / "captions.srt")
        narration = base / plan["narration"]
        duration = float(command("ffprobe", "-v", "error", "-select_streams", "a:0",
                                 "-show_entries", "stream=duration", "-of",
                                 "csv=p=0", str(narration)).stdout.strip())
        if not 25 <= duration <= 40:
            raise ValueError("Narration must span 25 to 40 seconds")
        staged = work / "preview.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "1",
                        "-i", "clips.txt", "-i", str(narration.resolve()), "-map", "0:v:0",
                        "-map", "1:a:0", "-t", "40", "-vf",
                        "subtitles=captions.srt:force_style='FontName=Noto Sans Telugu,FontSize=14,Alignment=2,MarginV=52',"
                        "drawtext=text='AI visuals | Nellore Daily':x=30:y=90:fontsize=30:fontcolor=white:box=1:boxcolor=black@0.7",
                        "-af", "loudnorm=I=-16:TP=-2:LRA=8,apad", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
                        str(staged)], cwd=work, check=True, timeout=120)
        command("ffmpeg", "-v", "error", "-xerror", "-i", str(staged), "-f", "null", "-")
        final_duration = float(command("ffprobe", "-v", "error", "-show_entries",
                                       "format=duration", "-of", "csv=p=0", str(staged)).stdout)
        if not 39.95 <= final_duration <= 40.05:
            raise ValueError("Output must be 40 seconds")
        os.replace(staged, output)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Usage: python3 reels/render_preview.py PLAN.json PREVIEW.mp4")
    render(Path(sys.argv[1]), Path(sys.argv[2]))
