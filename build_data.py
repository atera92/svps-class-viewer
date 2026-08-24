#!/usr/bin/env python3
"""公式サイトの試合結果 + YouTubeのチャプターを突き合わせて data.json を自動生成する。

・公式サイト schedule-results の <script id="session-modal-data"> に
  「どのバトルで誰が何クラスを使い、どちらが勝ったか」が全部入っている。
・YouTube の概要欄チャプターに「ROUND1開始 / BATTLE1 / BATTLE2 ...」の時刻が入っている。
この2つを順番で突き合わせると、全バトルの開始・終了時刻とクラスが確定する。

  python3 build_data.py
"""
import json, os, re, sys, urllib.request
from bs4 import BeautifulSoup

SRC  = "https://ps.shadowverse-wb.com/26-27/schedule-results/"
UA   = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126 Safari/537.36")
HERE = os.path.dirname(os.path.abspath(__file__))

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja"})
    return urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")

def secs(t):
    p = [int(x) for x in t.split(":")]
    return p[0]*3600 + p[1]*60 + p[2] if len(p) == 3 else p[0]*60 + p[1]

# ---------- YouTube チャプター ----------
def description(vid):
    h = fetch(f"https://www.youtube.com/watch?v={vid}")
    m = re.search(r'"shortDescription":"(.*?)","isCrawlable"', h, re.S)
    return json.loads('"' + m.group(1) + '"') if m else ""

def chapters(d):
    return [(secs(t), lab.strip())
            for t, lab in re.findall(r'^(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)$', d, re.M)]

def round_cards(d):
    """概要欄の「ROUND1：チームA VS チームB」を {1:(A,B)} で返す"""
    out = {}
    for n, a, b in re.findall(r'ROUND\s*(\d+)\s*[:：]\s*(.+?)\s+VS\s+(.+)', d):
        out[int(n)] = (a.strip(), b.strip())
    return out

def rounds_from_chapters(ch):
    """[(round番号, [(battle番号, start, end), ...]), ...] を返す"""
    out, cur = [], None
    for i, (t, lab) in enumerate(ch):
        end = ch[i+1][0] if i+1 < len(ch) else None
        r = re.match(r'ROUND\s*(\d+)', lab)
        b = re.match(r'BATTLE\s*(\d+)', lab)
        if r:
            cur = (int(r.group(1)), [])
            out.append(cur)
        elif b and cur:
            cur[1].append((int(b.group(1)), t, end))
    return out

# ---------- 公式サイト ----------
def site():
    soup = BeautifulSoup(fetch(SRC), "html.parser")
    tag = soup.find("script", id="session-modal-data")
    if not tag:
        sys.exit("session-modal-data が見つかりません。サイト構造が変わった可能性があります。")
    modal = json.loads(tag.string)

    days = []
    for d in soup.select(".results__day"):
        txt = re.sub(r"\s+", " ", d.get_text(" ", strip=True))
        rm = re.search(r"(第\d+節)(?:・(前半|後半))?", txt)
        if not rm:
            continue
        dm = re.search(r"(20\d\d)\s*(\d\d)\s*\.\s*(\d\d)", txt)
        slugs = [b["data-slug"] for b in d.select("button[data-slug]")]
        vid = None
        for a in d.select("a[href]"):
            if a.get_text(strip=True).startswith("本配信"):
                mm = re.search(r"(?:live/|watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})", a["href"])
                if mm:
                    vid = mm.group(1)
        days.append({
            "key": rm.group(1) + (rm.group(2) or ""),
            "date": f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}" if dm else "",
            "slugs": slugs, "vid": vid,
        })
    return modal, days

def main():
    arcs = json.load(open(os.path.join(HERE, "archives.json"), encoding="utf-8"))
    vid_of = {a["label"].replace("・", ""): a["videoId"] for a in arcs}
    modal, days = site()

    # 同じ節（第3節のように配信1本に4ラウンド入る場合）のスラッグを順番に連結
    merged = {}
    for d in days:
        m = merged.setdefault(d["key"], {"slugs": [], "vid": None, "date": d["date"]})
        m["slugs"] += d["slugs"]
        m["vid"] = m["vid"] or d["vid"] or vid_of.get(d["key"])

    segs, warn = [], []
    for key, m in merged.items():
        vid = m["vid"]
        if not vid or not m["slugs"]:
            continue
        desc = description(vid)
        rs = rounds_from_chapters(chapters(desc))
        cards = round_cards(desc)
        if not rs:
            warn.append(f"{key}: チャプターが無いため生成できません")
            continue
        if len(rs) != len(m["slugs"]):
            warn.append(f"{key}: 動画のROUND数({len(rs)})と試合数({len(m['slugs'])})が不一致")

        for i, (rno, battles) in enumerate(rs):
            # 概要欄のチーム名で該当試合を特定する（順番に頼らない）
            slug = None
            if rno in cards:
                want = set(cards[rno])
                for sg in m["slugs"]:
                    gg = modal.get(sg)
                    if gg and {gg["left"]["name"], gg["right"]["name"]} == want:
                        slug = sg
                        break
                if slug is None:
                    warn.append(f"{key} ROUND{rno}: 概要欄の {' VS '.join(cards[rno])} に一致する試合が見つからず、順番で対応させました")
            if slug is None:
                slug = m["slugs"][i] if i < len(m["slugs"]) else None
            g = modal.get(slug) if slug else None
            if not g:
                warn.append(f"{key} ROUND{rno}: 試合データが見つかりません")
                continue
            bl = g["battles"]
            if len(bl) != len(battles):
                warn.append(f"{key} ROUND{rno} {g['left']['name']} vs {g['right']['name']}: "
                            f"チャプター{len(battles)}件 / 実際{len(bl)}バトル → 少ない方に合わせます")
            for (bno, st, en), b in zip(battles, bl):
                L, R = b["left"], b["right"]
                segs.append({
                    "id": f"{vid}-{st}",
                    "vid": vid, "start": st, "end": en,
                    "c1": L.get("cardLabel"), "c2": R.get("cardLabel"),
                    "p1": "チームバトル" if b.get("isTeamBattle") else (L.get("name") or ""),
                    "p2": "チームバトル" if b.get("isTeamBattle") else (R.get("name") or ""),
                    "t1": g["left"]["name"], "t2": g["right"]["name"],
                    "battle": str(bno),
                    "win": {"left": "L", "right": "R"}.get(b.get("winner")),
                    "memo": f"R{rno}",
                    "deck1": L.get("cardLink"), "deck2": R.get("cardLink"),
                    "src": "auto",
                })

    segs.sort(key=lambda s: (s["vid"], s["start"]))
    out = os.path.join(HERE, "data.json")
    json.dump({"segments": segs}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"{len(segs)} バトルを {out} に書き出しました\n")
    from collections import Counter
    c = Counter()
    for s in segs:
        c[s["c1"]] += 1; c[s["c2"]] += 1
    print("クラス別の登場数:")
    for k, v in c.most_common():
        print(f"  {k:<6} {v}")
    if warn:
        print("\n[要確認]")
        for w in warn:
            print("  -", w)

if __name__ == "__main__":
    main()
