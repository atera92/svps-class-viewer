#!/usr/bin/env python3
"""マリガン画面の1枚の枠に映ったカードが、デッキのどのカードかを当てる。

絵柄そのものでは照合しない。プレイヤーが別イラスト（異画）を使っていることがあり、
絵柄が公式画像と一致しないため。代わりに、どのイラストでも同じ描き方をされる部分
  ・左上のコスト   ・左下の攻撃力   ・右下の体力   ・上部のカード名
を公式画像と突き合わせ、候補（そのプレイヤーのデッキに入っているカード）の中で最も合うものを選ぶ。
絵柄の一致は、同じコスト・攻撃力・体力のカードが複数あるときの補助にだけ使う。
"""
import os
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CARDS = os.path.join(HERE, "cache", "cards")

W = 234                       # 1920x1080 の配信画面上でのカードの幅（実測）
K = W / 530                   # 公式画像（530x687）からの縮尺
S = 1.0                       # リーグ配信の画面を 1 としたときの拡大率
# 公式画像上の各部分の位置 (x0, y0, x1, y1)
REG = {
    "cost": (20, 45, 105, 140),
    "atk":  (15, 565, 100, 665),
    "life": (420, 570, 510, 665),
    "name": (125, 72, 465, 122),
    "art":  (80, 160, 450, 530),
}
# 配信画面上の枠の中心（1920x1080）。上段が GUEST、下段が OWNER。
XS = [617 + 265 * i for i in range(4)]
YS = {"top": 339, "bot": 739}

# 画面の種類ごとのカード位置（カード左上の座標と幅。1920x1080 で実測）
#   league … リーグ配信の観戦画面。上段 GUEST・下段 OWNER の手札
#   player … 選手本人の画面（個人配信）。上段 CHANGE（返すカード）・下段 KEEP（残すカード）
LAYOUTS = {
    "league": {"W": 234, "OX": [498 + 265 * i for i in range(4)], "OY": {"top": 187, "bot": 587}},
    "player": {"W": 271, "OX": [269 + 307 * i for i in range(4)], "OY": {"top": 129, "bot": 597}},
}


def set_layout(name):
    global W, K, S, XS, YS
    L = LAYOUTS[name]
    W, K, S = L["W"], L["W"] / 530, L["W"] / 234
    XS = [x + round(119 * S) for x in L["OX"]]
    YS = {r: y + round(152 * S) for r, y in L["OY"].items()}
    _T.clear()


_T = {}
STYLES = {}         # card_id -> 別イラストの枚数。register_types で登録


def _load(name):
    im = cv2.imread(os.path.join(CARDS, f"{name}.png"), cv2.IMREAD_UNCHANGED)
    if im is None:
        return None
    if im.shape[2] == 4:
        a = im[:, :, 3:4] / 255.0
        im = (im[:, :, :3] * a + np.array([40, 30, 25]) * (1 - a)).astype(np.uint8)
    h = int(round(W * im.shape[0] / im.shape[1]))
    return cv2.resize(im, (W, h), interpolation=cv2.INTER_AREA)


def templates(cid):
    """公式画像と別イラストの画像（配信の縮尺に合わせたもの）"""
    if cid not in _T:
        names = [str(cid)] + [f"{cid}_s{n}" for n in range(STYLES.get(int(cid), 0))]
        _T[cid] = [t for t in map(_load, names) if t is not None]
    return _T[cid]


def template(cid):
    return templates(cid)[0]


def load_frame(path):
    im = cv2.imread(path)
    return cv2.resize(im, (1920, 1080), interpolation=cv2.INTER_AREA)


def card_origin(row, i):
    """枠 i のカード左上の位置（配信画面の座標）"""
    return XS[i] - round(119 * S), YS[row] - round(152 * S)


def is_empty(frame, row, i):
    """交換中でカードが抜けている枠は、暗く一様になる"""
    cx, cy = XS[i], YS[row]
    dx, dy = round(80 * S), round(100 * S)
    g = cv2.cvtColor(frame[cy - dy:cy + dy, cx - dx:cx + dx], cv2.COLOR_BGR2GRAY)
    return float(g.std()) < 20


def mark_level(frame, row, i):
    """交換するカードに付く青い矢印の輪の濃さ（輪の位置にある鮮やかな青の割合）。
    付いていると 0.45〜0.65、付いていないと 0〜0.1（青い絵柄のカードでも 0.35 程度）"""
    r = round(85 * S)
    cx, cy = XS[i] - 2, YS[row]
    hsv = cv2.cvtColor(frame[cy - r:cy + r, cx - r:cx + r], cv2.COLOR_BGR2HSV)
    yy, xx = np.mgrid[-r:r, -r:r]
    d = np.hypot(xx, yy)
    ring = (d >= 52 * S) & (d <= 80 * S)
    blue = (hsv[:, :, 0] >= 95) & (hsv[:, :, 0] <= 115) & (hsv[:, :, 1] >= 110) & (hsv[:, :, 2] >= 160)
    return float(blue[ring].mean())


def region_scores(frame, x0, y0, cid, pad=7, t=None):
    t = template(cid) if t is None else t
    out = {}
    for k, (a, b, c, d) in REG.items():
        a, b, c, d = int(a * K), int(b * K), int(c * K), int(d * K)
        tp = t[b:d, a:c]
        sr = frame[max(0, y0 + b - pad):y0 + d + pad, max(0, x0 + a - pad):x0 + c + pad]
        if sr.shape[0] < tp.shape[0] or sr.shape[1] < tp.shape[1]:
            out[k] = 0.0
            continue
        r = cv2.matchTemplate(sr, tp, cv2.TM_CCOEFF_NORMED)
        out[k] = float(r.max())
    return out


TYPES = {}          # card_id -> 種類（1 フォロワー / 2・3 アミュレット / 4 スペル）。register_types で登録


def register_types(cards):
    """{card_id: {"type": ..., "styles": [...]}} を登録しておくと、
    枠に映ったカードの種類で候補を絞れ、別イラストの絵柄でも照合できる"""
    for cid, c in cards.items():
        TYPES[int(cid)] = c["type"]
        if len(c.get("styles") or []) != STYLES.get(int(cid), 0):
            STYLES[int(cid)] = len(c.get("styles") or [])
            _T.pop(cid, None)
            _T.pop(int(cid), None)


def slot_type(frame, row, i):
    """枠に映ったカードの種類を、絵柄に左右されない部分で見分ける。
      ・フォロワーだけ、左下（攻撃力・青）と右下（体力・赤）に宝石がある
      ・名前の帯の色：スペルは紫、アミュレットは緑、フォロワーは赤
    戻り値 'follower' / 'spell' / 'amulet' / None（判断できない）"""
    x0, y0 = card_origin(row, i)
    a = cv2.cvtColor(frame[y0 + int(575 * K):y0 + int(665 * K), x0 + int(15 * K):x0 + int(100 * K)], cv2.COLOR_BGR2HSV)
    l = cv2.cvtColor(frame[y0 + int(575 * K):y0 + int(665 * K), x0 + int(420 * K):x0 + int(510 * K)], cv2.COLOR_BGR2HSV)
    blue = ((a[:, :, 0] >= 100) & (a[:, :, 0] <= 125) & (a[:, :, 1] >= 120) & (a[:, :, 2] >= 90)).mean()
    red = (((l[:, :, 0] <= 8) | (l[:, :, 0] >= 165)) & (l[:, :, 1] >= 120) & (l[:, :, 2] >= 90)).mean()
    if blue > 0.25 and red > 0.25:
        return "follower"
    b = cv2.cvtColor(frame[y0 + int(80 * K):y0 + int(115 * K), x0 + int(140 * K):x0 + int(450 * K)], cv2.COLOR_BGR2HSV)
    m = b[:, :, 1] > 60
    if m.mean() < 0.5:
        return None
    h = float(np.median(b[:, :, 0][m]))
    if 118 <= h <= 155:
        return "spell"
    if 55 <= h <= 105:
        return "amulet"
    return None


_KIND = {1: "follower", 2: "amulet", 3: "amulet", 4: "spell"}


def score(rs, kind="follower"):
    if kind == "follower":
        return rs["cost"] + rs["atk"] + rs["life"] + 1.5 * rs["name"] + 0.3 * rs["art"]
    # スペル・アミュレットは下の角に宝石がなく、枠の飾りも絵柄の版で変わるので、コストと名前で決める
    return rs["cost"] + 2 * rs["name"] + 0.3 * rs["art"]


def identify(frame, row, i, cands):
    """cands: {card_id: 表示名}。戻り値: [(総合点, card_id, 各部の点), ...] 点の高い順
    カードの種類が分かる場合は、同じ種類の候補だけで比べる"""
    x0, y0 = card_origin(row, i)
    kind = slot_type(frame, row, i)
    pool = [c for c in cands if _KIND.get(TYPES.get(int(c))) == kind] if kind else []
    if not pool:
        pool = list(cands)
    res = []
    for cid in pool:
        kind_c = _KIND.get(TYPES.get(int(cid)), "follower")
        # 公式の絵柄と別イラストのうち、いちばん合うものの点を採る
        best = max(((score(rs, kind_c), rs) for rs in
                    (region_scores(frame, x0, y0, cid, t=t) for t in templates(cid))), key=lambda x: x[0])
        res.append((best[0], cid, best[1]))
    res.sort(key=lambda r: -r[0])
    if len(res) == 1:
        res.append((res[0][0] - 1, None, {}))
    return res
