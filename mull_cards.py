#!/usr/bin/env python3
"""試合で使われたデッキのカード情報と、公式のカード画像を手元に取っておく。

マリガン画面に映ったカードを見分けるための「見本」になる。
  cache/decks/<デッキhash>.json   … 公式デッキポータルのデッキ詳細（カード名・コスト・画像hash）
  cache/cards/<card_id>.png       … 公式のカード画像

一度取ったものは取り直さないので、新しい節を足したときも差分だけ取りに行く。
cache/ は公開しない（.gitignore 済み）。
"""
import json, os, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import svps_api

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
DETAIL = "https://shadowverse-wb.com/web/DeckBuilder/deckHashDetail?hash="
IMG = "https://shadowverse-wb.com/uploads/card_image/jpn/card/{}.png"


def deck_hash(url):
    q = urllib.parse.urlparse(url or "").query
    return (urllib.parse.parse_qs(q).get("hash") or [None])[0]


def deck(h):
    """デッキhash -> {card_id: {name, cost, img, num}}"""
    os.makedirs(os.path.join(CACHE, "decks"), exist_ok=True)
    p = os.path.join(CACHE, "decks", h + ".json")
    if not os.path.exists(p):
        raw = json.loads(svps_api.fetch(DETAIL + urllib.parse.quote(h, safe=".-_"),
                                        accept="application/json"))
        json.dump(raw, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        time.sleep(0.3)
    d = json.load(open(p, encoding="utf-8"))["data"]
    out = {}
    for cid, n in d["deck_card_num"].items():
        c = d["card_details"][cid]["common"]
        out[int(cid)] = {"name": c["name"], "cost": c["cost"], "atk": c.get("atk"), "life": c.get("life"),
                         "type": c["type"], "img": c["card_image_hash"], "num": n,
                         "styles": styles_of(d["card_details"][cid])}
    return out


def styles_of(detail):
    """別イラスト・コラボの見た目（style_card_list）の画像hash。
    プレイヤーが設定していると、試合ではこちらの絵柄・名前で表示される"""
    return [s["hash"] for s in (detail.get("style_card_list") or []) if s.get("hash")]


def images(cid, c):
    """公式画像と、別イラストの画像をすべて手元に置く"""
    image(cid, c["img"])
    for n, h in enumerate(c.get("styles") or []):
        image(f"{cid}_s{n}", h)


LIST = "https://shadowverse-wb.com/web/CardList/cardList?class={}&offset={}"
CLASS_ID = {"ニュートラル": 0, "エルフ": 1, "ロイヤル": 2, "ウィッチ": 3, "ドラゴン": 4,
            "ナイトメア": 5, "ビショップ": 6, "ネメシス": 7}


def class_pool(cls, rotation=True):
    """そのクラスとニュートラルの全カード（デッキリストが分からない個人配信用の候補）。
    戻り値 {card_id: {name, cost, atk, life, type, img}}。トークン（デッキに入らないカード）は除く"""
    os.makedirs(os.path.join(CACHE, "lists"), exist_ok=True)
    out = {}
    for cid_ in (CLASS_ID[cls], 0):
        p = os.path.join(CACHE, "lists", f"class{cid_}.json")
        if not os.path.exists(p):
            det, off, total = {}, 0, None
            while total is None or off < total:
                d = json.loads(svps_api.fetch(LIST.format(cid_, off), accept="application/json"))["data"]
                total = d["count"]
                det.update(d["card_details"])
                off += 30
                time.sleep(0.3)
            json.dump(det, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        for k, v in json.load(open(p, encoding="utf-8")).items():
            c = v["common"]
            if c.get("is_token") or (rotation and not c.get("is_include_rotation")) or int(k) != c["base_card_id"]:
                continue
            out[int(k)] = {"name": c["name"], "cost": c["cost"], "atk": c.get("atk"), "life": c.get("life"),
                           "type": c["type"], "img": c["card_image_hash"], "styles": styles_of(v)}
    for k, c in out.items():
        images(k, c)
    return out


def image(cid, img_hash):
    os.makedirs(os.path.join(CACHE, "cards"), exist_ok=True)
    p = os.path.join(CACHE, "cards", f"{cid}.png")
    if not os.path.exists(p):
        req = urllib.request.Request(IMG.format(img_hash), headers={"User-Agent": svps_api.UA})
        open(p, "wb").write(urllib.request.urlopen(req, timeout=45).read())
        time.sleep(0.2)
    return p


def main():
    segs = json.load(open(os.path.join(HERE, "data.json"), encoding="utf-8"))["segments"]
    hashes = sorted({deck_hash(s[k]) for s in segs for k in ("deck1", "deck2") if s.get(k)} - {None})
    cards = {}
    for h in hashes:
        cards.update(deck(h))
    for cid, c in sorted(cards.items()):
        images(cid, c)
    print(f"デッキ {len(hashes)} 件・カード {len(cards)} 種を {CACHE} に用意しました")


if __name__ == "__main__":
    main()
