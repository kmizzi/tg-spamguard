#!/usr/bin/env python3
"""tg-spamguard — auto-block, report and delete unsolicited spam DMs on your
personal Telegram account (MTProto user API via Telethon).

Commands:
  login send <phone>                 send a login code to the Telegram app
  login code <code> [password]       finish login (password only if 2FA is on)
  run                                listen forever (systemd)
  scan [--act]                       sweep existing chats; report-only unless --act

TG_MODE=log (default) only logs what it WOULD do; TG_MODE=act blocks+reports+deletes.
Every decision lands in actions.jsonl so false positives can be reviewed.
"""
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

from telethon import TelegramClient, events, functions
from telethon.errors import PhoneCodeExpiredError, PhoneCodeInvalidError, SessionPasswordNeededError
from telethon.tl.types import User

HERE = Path(os.environ.get("TG_DATA_DIR") or Path(__file__).resolve().parent)


def load_env():
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()
API_ID = int(os.environ.get("TG_API_ID", "0"))
API_HASH = os.environ.get("TG_API_HASH", "")
MODE = os.environ.get("TG_MODE", "log")
THRESHOLD = int(os.environ.get("TG_THRESHOLD", "3"))
LONG_CHAT = 20  # a stranger chat longer than this is a real conversation, never spam
COLD_CHAT = 5   # a one-way chat this short is a cold opener
LOG = HERE / "actions.jsonl"
WHITELIST = HERE / "whitelist.txt"  # one user id per line, '#' comments ok

LINK = re.compile(r"(https?://|t\.me/|telegram\.me/|wa\.me/|www\.|@\w{4,}|\b\w+\.(com|io|net|org|xyz|top|vip|cc)\b)", re.I)
KEYWORDS = re.compile(
    r"\b(crypto|bitcoin|btc|usdt|eth|airdrop|forex|invest(ment|ing|or)?|trading|profit|"
    r"earn|income|passive|sign ?up|bonus|giveaway|casino|bet(ting)?|loan|"
    r"dating|lonely|sexy|onlyfans|promo(tion)?|followers|subscribers|seo|"
    r"recruit(er|ing)?|remote job|part[- ]time|hiring|telegram premium|"
    r"wallet|binance|mining|signal(s)?|pump|vip group|dm me|wrong number|"
    r"chemistry|dear|darling|handsome|beautiful|gorgeous|single|lonely|"
    r"looking for (a )?(friend|partner|love|relationship)|nice to meet you|"
    r"represents you|are you (free|busy|available)|can we (talk|chat)|hookup|nudes?)\b",
    re.I,
)
EXTRA = [k.strip() for k in os.environ.get("TG_EXTRA_KEYWORDS", "").split(",") if k.strip()]
if EXTRA:
    KEYWORDS = re.compile(KEYWORDS.pattern[:-3] + "|" + "|".join(map(re.escape, EXTRA)) + r")\b", re.I)

client: TelegramClient = None  # created in main(), so the module imports without credentials


def whitelist():
    if not WHITELIST.exists():
        return set()
    return {int(l.split("#")[0]) for l in WHITELIST.read_text().splitlines() if l.split("#")[0].strip()}


def log(**rec):
    rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False), flush=True)


async def trusted(u: User) -> str | None:
    """Return why this sender is trusted, or None if they're a stranger."""
    if u.is_self:
        return "self"
    if u.contact or u.mutual_contact:
        return "contact"
    if u.bot:
        return "bot"
    if u.verified or u.support:
        return "verified"
    if u.id in whitelist():
        return "whitelist"
    # len(), not .total: .total is the whole chat's count, not the filtered one
    if len(await client.get_messages(u, limit=1, from_user="me")):
        return "you replied before"
    if (await client.get_messages(u, limit=0)).total > LONG_CHAT:
        return "long conversation"
    common = await client(functions.messages.GetCommonChatsRequest(user_id=u, max_id=0, limit=1))
    if common.chats:
        return "shared group"
    return None


def score(u: User, text: str, cold: bool = False) -> tuple[int, list[str]]:
    if u.scam or u.fake:
        return 99, ["flagged scam/fake by Telegram"]
    s, why = 0, []
    if cold:
        s, why = s + 2, why + ["cold opener"]
    if LINK.search(text):
        s, why = s + 2, why + ["link/handle"]
    kw = KEYWORDS.search(text)
    if kw:
        s, why = s + 2, why + [f"keyword:{kw.group(0).lower()}"]
    if not u.photo:
        s, why = s + 1, why + ["no photo"]
    if not u.username:
        s, why = s + 1, why + ["no username"]
    return s, why


async def nuke(u: User):
    await client(functions.messages.ReportSpamRequest(peer=u))
    await client(functions.contacts.BlockRequest(id=u))
    await client.delete_dialog(u, revoke=True)


async def judge(u: User, text: str, act: bool, source: str):
    reason = await trusted(u)
    who = {"id": u.id, "name": " ".join(filter(None, [u.first_name, u.last_name])), "username": u.username}
    if reason:
        return
    cold = (await client.get_messages(u, limit=0)).total <= COLD_CHAT
    s, why = score(u, text, cold)
    verdict = "spam" if s >= THRESHOLD else "stranger"
    acted = False
    if verdict == "spam" and act:
        await nuke(u)
        acted = True
    log(source=source, verdict=verdict, score=s, why=why, acted=acted, mode=MODE, **who, text=text[:500])


async def on_dm(e):
    u = await e.get_sender()
    if isinstance(u, User):
        try:
            await judge(u, e.raw_text or "", MODE == "act", "live")
        except Exception as ex:  # never let one message kill the listener
            log(source="live", error=repr(ex), id=getattr(u, "id", None))


async def scan(act: bool):
    async for d in client.iter_dialogs():
        if not d.is_user or not isinstance(d.entity, User):
            continue
        msgs = await client.get_messages(d.entity, limit=5)
        text = "\n".join(m.raw_text or "" for m in reversed(msgs) if not m.out)
        await judge(d.entity, text, act, "scan")


async def login(argv):
    await client.connect()
    state = HERE / ".login.json"
    if argv[0] == "send":
        sent = await client.send_code_request(argv[1])
        state.write_text(json.dumps({"phone": argv[1], "hash": sent.phone_code_hash}))
        print(f"code sent via {type(sent.type).__name__}")
    else:
        st = json.loads(state.read_text())
        try:
            await client.sign_in(st["phone"], argv[1], phone_code_hash=st["hash"])
        except PhoneCodeInvalidError:
            sys.exit("That code is wrong. Use the NEWEST code in your Telegram app and run login code again.")
        except PhoneCodeExpiredError:
            sys.exit("That code expired. Run: login send <phone>, then login code <new code>.")
        except SessionPasswordNeededError:
            if len(argv) < 3:
                sys.exit("2FA password required: login code <code> <password>")
            await client.sign_in(password=argv[2])
        state.unlink()
        me = await client.get_me()
        print(f"logged in as {me.first_name} (@{me.username}, id {me.id})")


async def main():
    global client
    if not API_ID or not API_HASH:
        sys.exit("Set TG_API_ID and TG_API_HASH (see .env.example)")
    client = TelegramClient(str(HERE / "spamguard"), API_ID, API_HASH)
    client.add_event_handler(on_dm, events.NewMessage(incoming=True, func=lambda e: e.is_private))
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "login":
        return await login(sys.argv[2:])
    await client.start()
    if cmd == "scan":
        return await scan("--act" in sys.argv)
    print(f"spamguard listening, mode={MODE} threshold={THRESHOLD}", flush=True)
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
