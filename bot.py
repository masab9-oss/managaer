#!/usr/bin/env python3
"""Manager bot: your Telegram control panel for News Pulse PK and Islami Waqiat.

GitHub runs this every 5 minutes. It reads new Telegram messages, answers only YOU
(TELEGRAM_CHAT_ID), and controls the other two repositories through the GitHub API.

Messages it understands (English, Roman Urdu, Hindi or Urdu script):
  post karo / पोस्ट करो / پوسٹ کرو          -> post one news item now (any time of day)
  Imran Khan par post karo                  -> post the newest news about that topic
  trending / ट्रेंडिंग                        -> Pakistan's Google trends + post news on them
  video banao / वीडियो बनाओ                  -> make an Islami Waqiat video now
  status / report / kitni posts             -> today's posts, last post, last video
  band karo / बंद करो                        -> stop automatic posting
  chalu karo / चालू करो                      -> start automatic posting again
  help / madad                              -> this list
Anything else is answered by Gemini (if GEMINI_KEY is set).

Nothing secret is ever printed: this repository's logs can be public.
"""
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

TG = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GH = os.environ.get("GH_TOKEN", "").strip()
OWNER = os.environ.get("OWNER", "").strip()
GEMINI = os.environ.get("GEMINI_KEY", "").strip()
NEWS_REPO, NEWS_WF = "newspulse", "post.yml"
VIDEO_REPO, VIDEO_WF = "islamiwaqiat", "make.yml"
PKT = timezone(timedelta(hours=5))
CTX = ssl.create_default_context()


# ---------------------------------------------------------------- http helpers
def call(url, data=None, headers=None, method=None, timeout=40):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        body = r.read()
    return json.loads(body) if body else {}


def tg(method, **params):
    data = urllib.parse.urlencode(params).encode()
    return call(f"https://api.telegram.org/bot{TG}/{method}", data)


def say(text):
    try:
        tg("sendMessage", chat_id=CHAT, text=text[:4000], disable_web_page_preview="true")
    except Exception as e:  # noqa: BLE001
        print("send failed:", type(e).__name__)


def gh(path, body=None, method=None, raw=False):
    headers = {"Authorization": f"Bearer {GH}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "manager-bot"}
    if raw:
        headers["Accept"] = "application/vnd.github.raw"
    data = json.dumps(body).encode() if body is not None else None
    url = f"https://api.github.com/repos/{OWNER}/{path}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
        out = r.read()
    if raw:
        return out.decode("utf-8", "replace")
    return json.loads(out) if out else {}


def pk_time(iso):
    """GitHub/ISO time -> '24 Nov, 07:15 PM' in Pakistan time."""
    if not iso:
        return "?"
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if d.tzinfo is None:          # state.json times are written on GitHub's UTC clock
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(PKT).strftime("%d %b, %I:%M %p")


# ---------------------------------------------------------------- actions
def post_now(topic=""):
    gh(f"{NEWS_REPO}/actions/workflows/{NEWS_WF}/dispatches", {
        "ref": "main", "inputs": {"dry_run": "false", "force": "true", "topic": topic}}, "POST")
    if topic:
        say(f"👍 Theek hai, '{topic}' ki taaza khabar dhoond kar post kar raha hoon. "
            "2-5 minute mein tasveer ke saath confirmation aa jaye gi.")
    else:
        say("👍 Theek hai, abhi ek post kar raha hoon. 2-5 minute mein tasveer ke saath confirmation aa jaye gi.")


def make_video():
    gh(f"{VIDEO_REPO}/actions/workflows/{VIDEO_WF}/dispatches", {"ref": "main"}, "POST")
    say("🎬 Video banna shuru ho gai. 3-6 minute mein video yahin aa jaye gi.")


def trending():
    xml = urllib.request.urlopen(urllib.request.Request(
        "https://trends.google.com/trending/rss?geo=PK", headers={"User-Agent": "Mozilla/5.0"}),
        timeout=30, context=CTX).read()
    terms = [i.findtext("title", "").strip() for i in ET.fromstring(xml).iter("item")]
    terms = [t for t in terms if t][:10]
    if not terms:
        say("Google Trends se abhi list nahi mili. Thori der baad try karein.")
        return
    say("📈 Pakistan mein abhi trending:\n" + "\n".join(f"{n}. {t}" for n, t in enumerate(terms, 1))
        + "\n\nIn mein se jis par taaza khabar mili, us ki post kar raha hoon.")
    gh(f"{NEWS_REPO}/actions/workflows/{NEWS_WF}/dispatches", {
        "ref": "main", "inputs": {"dry_run": "false", "force": "true", "topic": "|".join(terms[:8])}}, "POST")


def status():
    lines = ["📊 Report"]
    try:
        st = json.loads(gh(f"{NEWS_REPO}/contents/state.json", raw=True))
        today = datetime.now(PKT).date().isoformat()
        n = st.get("posted_today", 0) if st.get("last_posted") == today else 0
        lines.append(f"\n📰 News Pulse PK\nAaj ki posts: {n}\nAakhri post: {pk_time(st.get('last_time'))}")
        titles = st.get("recent_titles") or []
        if titles:
            lines.append(f"Aakhri khabar: {titles[-1][:120]}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"\n📰 News Pulse: state nahi parh saka ({type(e).__name__})")
    try:
        wf = gh(f"{NEWS_REPO}/actions/workflows/{NEWS_WF}")
        lines.append("Auto-posting: " + ("✅ chalu" if wf.get("state") == "active" else "⏸ band"))
        runs = gh(f"{NEWS_REPO}/actions/workflows/{NEWS_WF}/runs?per_page=1").get("workflow_runs", [])
        if runs:
            r = runs[0]
            res = {"success": "✅", "failure": "❌", "cancelled": "⚪"}.get(r.get("conclusion"), "⏳")
            lines.append(f"Aakhri run: {res} {pk_time(r.get('created_at'))}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"(GitHub runs nahi dekh saka: {type(e).__name__})")
    try:
        runs = gh(f"{VIDEO_REPO}/actions/workflows/{VIDEO_WF}/runs?per_page=10").get("workflow_runs", [])
        ok = [r for r in runs if r.get("conclusion") == "success"]
        lines.append("\n🎬 Islami Waqiat\nAakhri video: " + (pk_time(ok[0]["created_at"]) if ok else "abhi koi nahi"))
        if runs and runs[0].get("conclusion") == "failure":
            lines.append("⚠️ Sab se aakhri video run fail hua tha.")
    except Exception as e:  # noqa: BLE001
        lines.append(f"\n🎬 Islami Waqiat: nahi dekh saka ({type(e).__name__})")
    say("\n".join(lines))


def set_auto(on):
    gh(f"{NEWS_REPO}/actions/workflows/{NEWS_WF}/{'enable' if on else 'disable'}", {}, "PUT")
    say("✅ Auto-posting dobara chalu ho gai." if on else
        "⏸ Auto-posting band kar di. Dobara chalu karne ke liye likhein: chalu karo")


HELP = ("🤖 Main ye kaam kar sakta hoon:\n"
        "• post karo – abhi ek khabar post\n"
        "• Imran Khan par post karo – us topic ki khabar\n"
        "• trending – Pakistan ke trends + un par post\n"
        "• video banao – Islami Waqiat ki nai video\n"
        "• status – aaj kitni posts, aakhri post, aakhri video\n"
        "• band karo / chalu karo – auto-posting rokna / chalana\n"
        "Hindi, Urdu ya Roman Urdu, kisi mein bhi likh sakte hain.")


def ask_gemini(text):
    if not GEMINI:
        return None
    prompt = ("You are 'Manager', the Telegram assistant of a Pakistani user who runs a Facebook news page "
              "'News Pulse PK' (auto-posts news every hour, 8am-11pm PKT) and a YouTube channel "
              "'Islami Waqiat' (Urdu Islamic story videos). Reply briefly (max 5 lines) in simple Roman Urdu. "
              "You cannot change code or settings yourself; for actions tell him these commands: "
              "'post karo', '<topic> par post karo', 'trending', 'video banao', 'status', 'band karo', 'chalu karo'. "
              "For bigger changes tell him to ask Claude in the Claude app.\n\nUser: " + text)
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    for model in ("gemini-3.8-flash", "gemini-flash-latest", "gemini-flash-lite-latest"):
        try:
            r = call(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                     body, {"Content-Type": "application/json", "x-goog-api-key": GEMINI}, timeout=40)
            return r["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception:  # noqa: BLE001
            continue
    return None


# ---------------------------------------------------------------- understanding messages
POST_W = {"post", "posts", "پوسٹ", "पोस्ट"}
FILLER = {"pm", "cm", "par", "pe", "pr", "per", "post", "karo", "kro", "kar", "kardo", "kr", "do", "on", "about", "ki", "ke", "ka",
          "khabar", "khabrain", "news", "wali", "wala", "aaj", "bhi", "abhi", "ek", "please", "plz", "now", "a", "the",
          "پر", "پوسٹ", "کرو", "کریں", "کی", "خبر", "والی", "آج", "بھی", "ابھی", "ایک", "دو",
          "पर", "पे", "पोस्ट", "करो", "करें", "की", "के", "खबर", "ख़बर", "वाली", "आज", "भी", "अभी", "एक", "दो"}


def has(t, *words):
    return any(w in t for w in words)


def handle(text):
    t = text.lower().strip()
    words = re.findall(r"[\w؀-ۿऀ-ॿ]+", t)
    if not t or has(t, "/start", "help", "madad", "مدد", "मदद"):
        say(HELP)
    elif has(t, "trend", "ट्रेंड", "ٹرینڈ"):
        trending()
    elif has(t, "video", "ویڈیو", "वीडियो", "विडियो"):
        make_video()
    elif has(t, "status", "report", "kitni", "aakhri", "akhri", "last", "स्टेटस", "रिपोर्ट", "कितनी", "आखिरी",
             "آخری", "کتنی", "رپورٹ"):
        status()
    elif has(t, "chalu", "shuru", "resume", "चालू", "शुरू", "چالو", "شروع"):
        set_auto(True)
    elif has(t, "band", "stop", "roko", "बंद", "रोको", "بند", "روکو"):
        set_auto(False)
    elif any(w in POST_W for w in words):
        topic_words = [w for w in re.findall(r"\S+", text.strip()) if w.lower().strip("?.!,") not in FILLER]
        post_now(" ".join(topic_words).strip(" ?.!,"))
    else:
        say(ask_gemini(text) or ("Main ye samajh nahi saka. 🙂\n\n" + HELP))


# ---------------------------------------------------------------- main
def main():
    if not TG:
        print("TELEGRAM_TOKEN secret missing")
        return 1
    updates = tg("getUpdates", timeout=0, allowed_updates='["message"]').get("result", [])
    if not updates:
        print("no new messages")
        return 0
    tg("getUpdates", offset=updates[-1]["update_id"] + 1, timeout=0)  # mark as read first: no double actions
    for u in updates:
        msg = u.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        text = msg.get("text") or ""
        if not CHAT:
            # first-time setup: tell the sender their chat id (it goes into the TELEGRAM_CHAT_ID secret)
            if msg.get("chat", {}).get("type") == "private":
                tg("sendMessage", chat_id=chat, text=f"Setup: aap ka chat ID ye hai:\n{chat}\n\n"
                   "Isay GitHub mein TELEGRAM_CHAT_ID naam ke secret mein daal dein (teeno repositories mein).")
            continue
        if chat != CHAT:
            continue  # only the owner can control the bot
        if not GH or not OWNER:
            say("GH_TOKEN secret missing hai, is liye main GitHub nahi chala sakta.")
            continue
        try:
            handle(text)
        except urllib.error.HTTPError as e:
            say(f"⚠️ GitHub ne mana kar diya (error {e.code}). Shayad GH_TOKEN ki ijazat kam hai.")
        except Exception as e:  # noqa: BLE001
            say(f"⚠️ Kuch garbar hui: {type(e).__name__}")
    print(f"handled {len(updates)} message(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
