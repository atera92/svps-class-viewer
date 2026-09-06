#!/usr/bin/env python3
"""公式サイトの内部API（Kuroco）から日程・試合結果・デッキリストを取る共通部分。

2026年9月に公式サイトがクライアント側描画に変わり、HTMLから試合結果を読めなくなった。
代わりにページが叩いている JSON API を直接使う。こちらのほうが構造が明確で壊れにくい。

  schedule[] … 節ごとの日付・配信URL・その日の各ラウンド(round1〜4)
  result[]   … 1ラウンド分の対戦（左右チーム、battle1〜5の選手・クラス・勝敗）
  decks[]    … チーム×クラスごとのデッキリストURL

  対応付け: schedule.round.roundN.module_id -> result.topics_id
            result.reference.module_id      -> decks.topics_id
"""
import json, re, urllib.request

API = "https://ps.shadowverse-wb.com/rcms-api/1/schedule-results?apiId=1"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")
VID_RE = re.compile(r"(?:live/|watch\?v=|youtu\.be/|embed/)([A-Za-z0-9_-]{11})")


def fetch(url, accept=None):
    hdr = {"User-Agent": UA, "Accept-Language": "ja"}
    if accept:
        hdr["Accept"] = accept
    req = urllib.request.Request(url, headers=hdr)
    return urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")


def video_id(url):
    """YouTubeのURLから動画IDを取る。
    t.co の短縮URLはリダイレクトを返さずHTMLを返すことがあるので、本文からも拾う。"""
    url = url or ""
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


def load():
    """APIを取得し、扱いやすい形に整えて返す。

    戻り値: [{label, date, videoId, rounds:[{no, teamA, teamB, scoreA, scoreB, battles:[...]}]}]
    battles の要素: {no, p1, c1, deck1, p2, c2, deck2, win, team_battle}
    """
    d = json.loads(fetch(API, accept="application/json"))
    results = {r["topics_id"]: r for r in d.get("result", [])}
    decks = {x["topics_id"]: x for x in d.get("decks", [])}

    days = []
    for s in d.get("schedule", []):
        links = s.get("streaming_links") or []
        vid = next((video_id(l.get("link")) for l in links
                    if (l.get("title") or "").startswith("本配信")), None)

        rounds = []
        for key in sorted((s.get("round") or {}).keys()):          # round1..round4
            mid = (s["round"][key] or {}).get("module_id")
            r = results.get(mid)
            if not r or not r.get("is_result"):
                continue
            rounds.append(_round(r, decks))

        days.append({
            "label": s.get("subject", ""),
            "date": (s.get("schedule") or {}).get("date", ""),
            "videoId": vid,
            "rounds": rounds,
        })
    return days


def _round(r, decks):
    left = (r["team"]["left_team"] or {}).get("label", "")
    right = (r["team"]["right_team"] or {}).get("label", "")

    # そのラウンドのデッキリスト（チーム名 -> クラスkey -> URL）
    dk = decks.get((r.get("reference") or {}).get("module_id")) or {}
    deck_of = {t["team"]["label"]: (t.get("deck") or {}) for t in dk.get("team", [])}

    def link(team, cls_key):
        return ((deck_of.get(team) or {}).get(cls_key) or {}).get("link")

    battles, a, b = [], 0, 0
    for i in range(1, 6):
        x = r.get(f"battle{i}") or {}
        lc, rc = x.get("left_deck") or {}, x.get("right_deck") or {}
        if not lc.get("label") or not rc.get("label"):
            continue                                   # 行われなかったバトル
        w = (x.get("winner") or {}).get("key")          # "left" / "right"
        lp = (x.get("left_player") or {}).get("label")
        rp = (x.get("right_player") or {}).get("label")
        if w == "left":
            a += 1
        elif w == "right":
            b += 1
        battles.append({
            "no": i,
            "p1": lp or "チームバトル", "c1": lc["label"], "deck1": link(left, lc.get("key")),
            "p2": rp or "チームバトル", "c2": rc["label"], "deck2": link(right, rc.get("key")),
            "win": {"left": "L", "right": "R"}.get(w),
            "team_battle": not (lp or rp),
        })

    return {"no": len(battles) and 0, "teamA": left, "teamB": right,
            "scoreA": a, "scoreB": b, "battles": battles}


# ---------- YouTube ----------
def description(vid):
    h = fetch(f"https://www.youtube.com/watch?v={vid}")
    m = re.search(r'"shortDescription":"(.*?)","isCrawlable"', h, re.S)
    return json.loads('"' + m.group(1) + '"') if m else ""


def _secs(t):
    p = [int(x) for x in t.split(":")]
    return p[0] * 3600 + p[1] * 60 + p[2] if len(p) == 3 else p[0] * 60 + p[1]


def chapters(desc):
    return [(_secs(t), lab.strip())
            for t, lab in re.findall(r"^(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)$", desc, re.M)]


def rounds_from_chapters(ch):
    """[(ラウンド番号, [(バトル番号, 開始, 終了), ...]), ...]"""
    out, cur = [], None
    for i, (t, lab) in enumerate(ch):
        end = ch[i + 1][0] if i + 1 < len(ch) else None
        rm = re.match(r"ROUND\s*(\d+)", lab)
        bm = re.match(r"BATTLE\s*(\d+)", lab)
        if rm:
            cur = (int(rm.group(1)), [])
            out.append(cur)
        elif bm and cur:
            cur[1].append((int(bm.group(1)), t, end))
    return out


def round_cards(desc):
    """概要欄の「ROUND1：チームA VS チームB」"""
    return {int(n): (a.strip(), b.strip())
            for n, a, b in re.findall(r"ROUND\s*(\d+)\s*[:：]\s*(.+?)\s+VS\s+(.+)", desc)}
