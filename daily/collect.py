"""Read publisher feeds and official district recruitment notices."""
import io
import json
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import feedparser
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

DISTRICT = "https://spsnellore.ap.gov.in/notice_category/recruitment/"
OFFICIAL_HOSTS = {"spsnellore.ap.gov.in", "cdn.s3waas.gov.in"}
FEEDS = {
    "https://www.eenadu.net/rss/andhra-pradesh.xml": "eenadu.net",
    "https://www.sakshi.com/rss/andhra-pradesh": "sakshi.com",
    "https://telugu.samayam.com/rssfeedstopstories.cms": "telugu.samayam.com",
}
DATE = r"([0-9]{1,2}[./-][0-9]{1,2}[./-][0-9]{4})"
CLOSING = re.compile(
    r"last date (?:for|of|to) [^\n:]{1,65}[:\s]*" + DATE, re.I
)
ELIGIBILITY = re.compile(r"^(?:essential qualification|eligibility|educational qualification)s?\s*:\s*(.{3,160})$", re.I | re.M)
RECRUITMENT = re.compile(r"\b(?:recruitment|vacanc(?:y|ies)|filling up|inviting applications)\b", re.I)
EXCLUDED = re.compile(r"\b(?:merit list|selection list|objections?|admission|corrigendum|walk.in|cancel\w*)\b", re.I)


def hostname(url):
    return (urlsplit(url).hostname or "").removeprefix("www.")


def body(url, hosts, limit):
    for _ in range(4):
        location = urlsplit(url)
        if location.scheme != "https" or hostname(url) not in hosts or location.port or location.username:
            raise ValueError("unexpected source host")
        with requests.get(url, timeout=(8, 15), allow_redirects=False, stream=True) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers["Location"])
                continue
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_content(32768):
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("source exceeds size limit")
            return bytes(data)
    raise ValueError("too many source redirects")


def get_news(url, host):
    feed = feedparser.parse(body(url, {host}, 2_000_000))
    if not feed.entries:
        raise ValueError("feed returned no entries")
    for entry in feed.entries[:40]:
        link = entry.get("link", "")
        if not entry.get("published_parsed") or hostname(link) != host:
            continue
        title = BeautifulSoup(entry.get("title", ""), "html.parser").get_text(" ", strip=True)
        if 15 <= len(title) <= 150:
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
            yield dict(title=title, published=published, link=link)


def parse_date(value):
    return datetime.strptime(re.sub(r"[.-]", "/", value), "%d/%m/%Y").date().isoformat()


def get_jobs(today):
    soup = BeautifulSoup(body(DISTRICT, OFFICIAL_HOSTS, 2_000_000), "html.parser")
    for row in soup.select("table tr")[:20]:
        cells = row.find_all("td", recursive=False)
        if len(cells) != 5:
            continue
        title = cells[0].get_text(" ", strip=True)
        if not RECRUITMENT.search(title) or EXCLUDED.search(title) or len(title) > 160:
            continue
        try:
            opened = parse_date(cells[2].get_text(strip=True))
            end = parse_date(cells[3].get_text(strip=True))
        except ValueError:
            continue
        if not opened <= today < end:
            continue
        links = cells[4].find_all("a", href=True)
        if len(links) != 1:
            continue
        link = urljoin(DISTRICT, links[0]["href"])
        if not urlsplit(link).path.lower().endswith(".pdf"):
            continue
        try:
            document = PdfReader(io.BytesIO(body(link, OFFICIAL_HOSTS, 5_000_000)))
            text = "\n".join(page.extract_text() for page in document.pages[:5])
            deadlines = {parse_date(match) for match in CLOSING.findall(text)}
            if deadlines != {end} or EXCLUDED.search(text):
                continue
        except Exception as error:
            print(f"Official notice skipped: {type(error).__name__}", file=sys.stderr)
            continue
        qualification = ELIGIBILITY.search(text)
        yield dict(title=title, link=link, opened=opened, deadline=end,
                   eligibility=qualification[1].strip() if qualification else "")


def main():
    news, jobs, errors = [], [], []
    for url, host in FEEDS.items():
        try:
            news.extend(get_news(url, host))
        except Exception as error:
            errors.append(f"{host}: {type(error).__name__}")
    try:
        jobs.extend(get_jobs(sys.argv[1]))
    except Exception as error:
        errors.append(f"district recruitment: {type(error).__name__}")
    print(json.dumps(dict(news=news, jobs=jobs, errors=errors)))


if __name__ == "__main__":
    main()

