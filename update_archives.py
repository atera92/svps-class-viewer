#!/usr/bin/env python3
"""公式サイトから Shadowverse Premier Series のアーカイブ一覧を取得して archives.json を作る。
新しい節が追加されたら再実行するだけでよい:  python3 update_archives.py
"""
import json, re, sys, urllib.request, os

SRC = "https://ps.shadowverse-wb.com/26-27/schedule-results/"
UA  = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
HERE = os.path.dirname(os.path.abspath(__file__))

# 公式サイトに動画リンクが載っていない回の手動補完（videoIdのみ）
MANUAL = {
    ("第4節", "前半", "本配信"): "x6PeWkSyyV4",
    ("第4節", "後半", "本配信"): "t5f1hh-W48E",
}

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")

VID_RE = re.compile(r"(?:live/|watch\?v=|youtu\.be/|embed/)([A-Za-z0-9_-]{11})")

def resolve(url):
    """t.co などの短縮URLを展開して videoId を得る。
    t.co はリダイレクトを返さずHTMLを返すことがあるので本文からも拾う。"""
    m = VID_RE.search(url)
    if m:
        return m.group(1)
    if "t.co/" in url:
        try:
            body = fetch(url)
        except Exception:
            return None
        m = VID_RE.search(body.replace("&#x2F;", "/").replace("\\/", "/"))
        return m.group(1) if m else None
    return None

def main():
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(fetch(SRC), "html.parser")
    archives = []
    for day in soup.select(".results__day"):
        txt = re.sub(r"\s+", " ", day.get_text(" ", strip=True))
        m = re.search(r"(20\d\d)\s*(\d\d)\s*\.\s*(\d\d)", txt)
        date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""
        rm = re.search(r"(第\d+節)(?:・(前半|後半))?", txt)
        if not rm:
            continue
        rnd, half = rm.group(1), rm.group(2) or ""

        # 対戦カード（ROUND1 / ROUND2）
        cards = []
        for g in day.select(".results__game"):
            names = [t.get_text(strip=True) for t in g.select(".results__team-name")]
            sc = [t.get_text(strip=True) for t in g.select(".results__score-num")]
            rl = g.select_one(".results__score-match")
            if len(names) >= 2:
                cards.append({
                    "teamA": names[0], "teamB": names[1],
                    "scoreA": sc[0] if len(sc) > 0 else "-",
                    "scoreB": sc[1] if len(sc) > 1 else "-",
                    "round": rl.get_text(strip=True) if rl else "",
                })

        links = {}
        for a in day.select("a[href]"):
            label = a.get_text(strip=True)
            if label.startswith(("本配信", "副音声")):
                vid = resolve(a["href"])
                if vid:
                    links[label] = vid
        for (r, h, lab), vid in MANUAL.items():
            if r == rnd and h == half and lab not in links:
                links[lab] = vid

        for label, vid in links.items():
            if not label.startswith("本配信"):
                continue  # 本配信のみ登録（副音声は解説違いで同じ試合）
            archives.append({
                "videoId": vid,
                "round": rnd, "half": half, "date": date,
                "label": f"{rnd}{('・' + half) if half else ''}",
                "cards": cards,
            })

    archives.sort(key=lambda a: (int(re.sub(r"\D", "", a["round"])), a["half"]))
    out = os.path.join(HERE, "archives.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(archives, f, ensure_ascii=False, indent=1)
    print(f"{len(archives)} 件を {out} に書き出しました")
    for a in archives:
        print(" ", a["label"], a["date"], a["videoId"],
              " / ".join(f'{c["teamA"]} {c["scoreA"]}-{c["scoreB"]} {c["teamB"]}' for c in a["cards"]))

if __name__ == "__main__":
    main()
