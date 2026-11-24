"""Render branded Telugu artwork and a narrated, captioned vertical reel."""
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import edge_tts
from PIL import Image, ImageDraw, ImageFont, features

FONT = os.environ.get("TELUGU_FONT", "/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf")


def run(*args):
    subprocess.run(args, check=True)


def font(size):
    return ImageFont.truetype(FONT, size, layout_engine=ImageFont.Layout.RAQM)


def text_box(draw, value, bounds, max_size, min_size, color):
    left, top, right, bottom = bounds
    for size in range(max_size, min_size - 1, -2):
        face = font(size)
        lines = [""]
        for word in value.split():
            trial = (lines[-1] + " " + word).strip()
            if lines[-1] and draw.textlength(trial, font=face) > right - left:
                lines.append(word)
            else:
                lines[-1] = trial
        block = "\n".join(lines)
        x0, y0, x1, y1 = draw.multiline_textbbox((0, 0), block, font=face, spacing=14)
        if x1 - x0 <= right - left and y1 - y0 <= bottom - top:
            draw.multiline_text((left - x0, top - y0), block, font=face, fill=color, spacing=14)
            return
    raise ValueError("Text does not fit the artwork")


def artwork(data, destination, height, subtitle=None):
    image = Image.new("RGB", (1080, height), "#102035")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1080, 24), fill="#F2C46C")
    draw.rounded_rectangle((70, 113, 1010, height - 132), 46, fill="#172E49")
    text_box(draw, "NELLORE DAILY", (114, 167, 940, 238), 54, 38, "#FFFFFF")
    text_box(draw, data["label"], (114, 302, 940, 381), 56, 36, "#F2C46C")
    draw.line((114, 426, 945, 426), width=3, fill="#405672")
    text_box(draw, data["title"], (114, 486, 930, 805), 76, 38, "#FFFFFF")
    for index, detail in enumerate(data["details"]):
        top = 824 + index * 135
        text_box(draw, detail, (114, top, 940, top + 116), 41, 26, "#F2C46C")
    if subtitle:
        draw.rounded_rectangle((105, 1230, 975, 1570), 34, fill="#080F1C")
        text_box(draw, subtitle, (145, 1285, 925, 1490), 59, 33, "#FFFFFF")
    text_box(draw, "నెల్లూరు జిల్లా", (114, height - 245, 940, height - 162), 39, 29, "#A9BDD6")
    image.save(destination, quality=94)


def timestamp(seconds):
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


async def render(folder):
    if not features.check("raqm"):
        raise RuntimeError("Pillow requires RAQM for Telugu shaping")
    os.chdir(folder)
    data = json.loads(Path("editorial.json").read_text(encoding="utf-8"))
    artwork(data, "poster.jpg", 1350)
    duration, subtitles, pieces = 0.0, [], []
    for index, segment in enumerate(data["segments"], 1):
        frame, audio, clip = f"frame{index}.jpg", f"audio{index}.wav", f"clip{index}.mp4"
        if os.environ.get("TELUGU_VOICE") == "edge":
            await edge_tts.Communicate(segment, "te-IN-ShrutiNeural").save(f"voice{index}.mp3")
            run("ffmpeg", "-v", "error", "-y", "-i", f"voice{index}.mp3", audio)
        else:
            run("espeak-ng", "-v", "te", "-s", "140", "-w", audio, segment)
        length = float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", audio], text=True))
        if length <= 0:
            raise ValueError("Empty narration")
        artwork(data, frame, 1920, segment)
        frames = max(1, round(length * 30))
        length = frames / 30
        run("ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", frame,
            "-i", audio, "-af", "apad", "-t", str(length), "-c:v", "libx264", "-preset", "veryfast",
            "-g", "60", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
            "-ac", "2", "-b:a", "160k", clip)
        pieces.append(f"file '{clip}'")
        subtitles.append(f"{index}\n{timestamp(duration)} --> {timestamp(duration + length)}\n{segment}\n")
        duration += length
    if not 3 <= duration <= 90:
        raise ValueError("Reel duration is outside the publishing range")
    Path("subtitles.srt").write_text("\n".join(subtitles), encoding="utf-8")
    Path("concat.txt").write_text("\n".join(pieces), encoding="ascii")
    run("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "1", "-i", "concat.txt",
        "-c", "copy", "-movflags", "+faststart", "reel.mp4")


if __name__ == "__main__":
    asyncio.run(render(sys.argv[1]))

