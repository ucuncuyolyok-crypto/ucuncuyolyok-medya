"""Read and tally viewer comments on @ucuncuyolyok Reels.

Usage:
  python3 comments.py latest            # tally the most recent Reel
  python3 comments.py <media_id>        # tally a specific Reel
  python3 comments.py list              # list recent media (id, time, caption start)
  python3 comments.py raw <media_id>    # every comment with its id (for drafting replies)
  python3 comments.py pending [n]       # unreplied viewer comments on the n latest Reels
  python3 comments.py reply <comment_id> "<text>"   # ONLY after the user approved the reply text

Prints a JSON report: A/B counts (one vote per user, latest comment wins),
free-form suggestions (comments that are not just "A"/"B"), and top liked comments.
Reads META_TOKEN from env. Only touches media owned by IG 17841463812077146.
"""
import json, os, re, sys, urllib.parse, urllib.request

API = "https://graph.facebook.com/v21.0"
IG_ID = "17841463812077146"
TOKEN = os.environ["META_TOKEN"]


def get(path, **params):
    params["access_token"] = TOKEN
    url = path if path.startswith("http") else f"{API}/{path}"
    if "?" not in url:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read())


def paged(path, **params):
    d = get(path, **params)
    while True:
        yield from d.get("data", [])
        nxt = d.get("paging", {}).get("next")
        if not nxt:
            break
        with urllib.request.urlopen(nxt, timeout=60) as r:
            d = json.loads(r.read())


def recent_media(limit=10):
    return list(paged(f"{IG_ID}/media", fields="id,timestamp,media_product_type,caption,permalink,comments_count,like_count", limit=limit))[:limit]


# A vote: the comment is (mostly) a lone A or B, e.g. "A", "a!!", "B 😱", "kesin B", "A şıkkı"
VOTE_RE = re.compile(r"(?<![a-zçğıöşü])([ab])(?![a-zçğıöşü])", re.I)
WORD_HINTS = {  # episode-specific: Bölüm 4 (A Örtüyü kaldır / B Defteri imzala)
    "A": ["örtüyü", "ortuyu", "örtü", "aynaya bak", "aynayı aç", "kaldır"],
    "B": ["defteri", "imzala", "imza", "imzalar"],
}


def classify(text):
    t = text.lower().strip()
    t = re.sub(r"(?<![a-zçğıöşü])([ab])\1+(?![a-zçğıöşü])", r"\1", t)  # "BBBB" -> "b"
    letters = {m.group(1).upper() for m in VOTE_RE.finditer(t)}
    hint = {k for k, ws in WORD_HINTS.items() if any(w in t for w in ws)}
    votes = letters | hint
    if len(votes) == 1:
        vote = votes.pop()
    else:
        vote = None  # none or ambiguous ("A mı B mi bilemedim")
    stripped = VOTE_RE.sub("", t)
    stripped = re.sub(r"[\W_]+", "", stripped)
    freeform = len(stripped) >= 6  # has real words beyond the letter
    return vote, freeform


def owner_ok(media_id):
    m = get(media_id, fields="owner{id},username")
    return m.get("owner", {}).get("id") == IG_ID or m.get("username") == "ucuncuyolyok"


def tally(media_id):
    if not owner_ok(media_id):
        raise SystemExit("Media does not belong to @ucuncuyolyok — refusing.")
    comments = list(paged(f"{media_id}/comments", fields="id,text,username,timestamp,like_count", limit=50))
    comments.sort(key=lambda c: c.get("timestamp", ""))
    per_user, free, own = {}, [], 0
    for c in comments:
        if c.get("username") == "ucuncuyolyok":
            own += 1
            continue
        vote, freeform = classify(c.get("text", ""))
        if vote:
            per_user[c["username"]] = vote
        if freeform:
            free.append({"user": c["username"], "text": c["text"], "likes": c.get("like_count", 0), "vote": vote})
    counts = {"A": 0, "B": 0}
    for v in per_user.values():
        counts[v] += 1
    total = counts["A"] + counts["B"]
    return {
        "media_id": media_id,
        "comments_total": len(comments) - own,
        "voters": total,
        "A": counts["A"], "B": counts["B"],
        "A_pct": round(100 * counts["A"] / total, 1) if total else None,
        "freeform": sorted(free, key=lambda x: -x["likes"]),
        "top_liked": sorted(
            [{"user": c["username"], "text": c["text"], "likes": c.get("like_count", 0)} for c in comments if c.get("username") != "ucuncuyolyok"],
            key=lambda x: -x["likes"])[:10],
    }


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "latest"
    if arg == "list":
        print(json.dumps(recent_media(), ensure_ascii=False, indent=1))
    elif arg == "raw":
        if not owner_ok(sys.argv[2]): raise SystemExit("not our media")
        print(json.dumps(list(paged(f"{sys.argv[2]}/comments", fields="id,text,username,timestamp,like_count", limit=50)), ensure_ascii=False, indent=1))
    elif arg == "pending":
        # unreplied viewer comments on the N most recent Reels (default 2): what still needs an answer
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 2
        reels = [m for m in recent_media(20) if m.get("media_product_type") == "REELS"][:n]
        out = []
        for m in reels:
            for c in paged(f"{m['id']}/comments", fields="id,text,username,timestamp,replies{username}", limit=50):
                if c.get("username") == "ucuncuyolyok": continue
                if any(r.get("username") == "ucuncuyolyok" for r in (c.get("replies") or {}).get("data", [])): continue
                out.append({"media": m["id"], "episode": ((m.get("caption") or "").splitlines() or [""])[0][:60],
                            "comment_id": c["id"], "user": c["username"], "text": c["text"]})
        print(json.dumps(out, ensure_ascii=False, indent=1))
    elif arg == "reply":
        cid, text = sys.argv[2], sys.argv[3]
        c = get(cid, fields="id,media{id,owner}")
        if c.get("media", {}).get("owner", {}).get("id") not in (IG_ID, None) and not owner_ok(c.get("media", {}).get("id", "")):
            raise SystemExit("comment not on @ucuncuyolyok media")
        req = urllib.request.Request(f"{API}/{cid}/replies", data=urllib.parse.urlencode({"message": text, "access_token": TOKEN}).encode(), method="POST")
        with urllib.request.urlopen(req, timeout=60) as r: print(r.read().decode())
    else:
        if arg == "latest":
            reels = [m for m in recent_media(20) if m.get("media_product_type") == "REELS"]
            if not reels:
                raise SystemExit("No Reels yet.")
            arg = reels[0]["id"]
        print(json.dumps(tally(arg), ensure_ascii=False, indent=1))
