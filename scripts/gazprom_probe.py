#!/usr/bin/env python3
import re
import urllib.request
from urllib.parse import urljoin

URL = "https://gpnbonus.ru/fuel/refuel-map"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.geturl(), r.read().decode("utf-8", "replace")

status, final, html = get(URL)
print("PAGE", status, final, len(html))
print("TITLE", re.findall(r"<title[^>]*>(.*?)</title>", html, re.I|re.S)[:1])

for kw in ("refuel", "ajax", "api", "station", "fuel", "map"):
    pos = html.lower().find(kw)
    if pos >= 0:
        print("HTML-KW", kw, html[max(0,pos-250):pos+500].replace("\n"," ")[:900])

scripts = re.findall(r"<script[^>]+src=[\"']([^\"']+)", html, re.I)
print("SCRIPTS", len(scripts))
for src in scripts:
    full = urljoin(final, src)
    print("SCRIPT", full)
    try:
        st, fu, js = get(full)
        print("SCRIPT-OK", st, len(js))
        low = js.lower()
        if any(k in low for k in ("refuel", "station", "fuel", "ajax", "/api/")):
            # Print compact string literals/URL-like fragments around useful keywords.
            shown = 0
            for kw in ("refuel", "station", "/api/", "fuel", "ajax"):
                start = 0
                while shown < 30:
                    p = low.find(kw, start)
                    if p < 0:
                        break
                    frag = js[max(0,p-220):p+420].replace("\n"," ")
                    print("MATCH", kw, frag[:700])
                    shown += 1
                    start = p + len(kw)
            urls = sorted(set(re.findall(r"https?://[^\"'\\s)]+", js)))
            for u in urls[:50]:
                if any(k in u.lower() for k in ("gpn", "api", "fuel", "map")):
                    print("URL", u[:500])
    except Exception as e:
        print("SCRIPT-ERR", type(e).__name__, str(e)[:300])
