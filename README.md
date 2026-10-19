# tg-spamguard

Automatically **report, block and delete** unsolicited spam DMs on your personal Telegram account.

Telegram's own fix for this, "Archive and Mute New Chats", is only offered to Premium users
or to accounts Telegram decides are spammed heavily, and even then it only hides the messages.
tg-spamguard runs as your own account through the official user API (MTProto, via
[Telethon](https://github.com/LonamiWebs/Telethon)) and actually removes them:

1. **Reports** the sender as spam to Telegram, which helps get the account banned.
2. **Blocks** them.
3. **Deletes** the chat for both sides.

A bot can't do this. The Bot API has no access to your personal DMs.

## How it decides

A sender is **never touched** if any of these are true:

| Trusted when… | |
|---|---|
| They're in your contacts (or you're in theirs) | |
| They're a bot, a verified account or Telegram support | |
| You have ever sent them a real message | you started or replied (system markers like "history cleared" don't count) |
| You share a group or channel with them | probably a real person |
| The chat has more than 20 messages | an established conversation |
| Their user id is in `whitelist.txt` | manual override |

Messages from everyone else are scored:

| Signal | Points |
|---|---|
| Telegram itself flags the account `SCAM` / `FAKE` | instant spam |
| Deleted account with a short one-way chat (a banned spammer's leftover) | chat cleared |
| Short one-way chat (≤5 messages, only they wrote) — a cold opener | +2 |
| Contains a link, domain or `@handle` | +2 |
| Contains a spam keyword (crypto, invest, airdrop, forex, dating, loan, …) | +2 |
| No profile photo | +1 |
| No username | +1 |

A score of **3 or more** (`TG_THRESHOLD`) counts as spam. A cold "Hello" from an account with no
username scores 3 and is removed; a cold "Hi, we met at the conference" from a complete profile
(photo and username) scores 2, so it's logged as `stranger` and left alone.

Every decision is appended to `actions.jsonl` with the sender, score, reasons and message text,
so you can review false positives before trusting it.

## Setup

**1. Get API credentials.** Log in at <https://my.telegram.org> → *API development tools* → create an
app (any name and platform). Note the `api_id` and `api_hash`.

**2. Install.**

```bash
git clone https://github.com/kmizzi/tg-spamguard && cd tg-spamguard
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in TG_API_ID and TG_API_HASH
```

**3. Log in as yourself.** The login is split into two steps so it works over SSH or from a script:

```bash
.venv/bin/python spamguard.py login send +15551234567   # code arrives in your Telegram app
.venv/bin/python spamguard.py login code 12345          # add your 2FA password as a 3rd arg if you use one
```

This creates `spamguard.session`. **Treat that file like a password**: it gives full access to your account.
Revoke it any time under Telegram → Settings → Devices.

**4. Dry-run first.** With `TG_MODE=log` (the default) nothing is deleted:

```bash
.venv/bin/python spamguard.py scan    # score every existing private chat, report only
.venv/bin/python spamguard.py run     # listen for new DMs, log only
```

Read `actions.jsonl`. Put any wrongly flagged user ids in `whitelist.txt`, or tune
`TG_THRESHOLD` / `TG_EXTRA_KEYWORDS`.

**5. Turn it on.** Set `TG_MODE=act` in `.env`. To clean up existing spam once:

```bash
.venv/bin/python spamguard.py scan --act
```

## Run it permanently (systemd user service)

```bash
cp deploy/tg-spamguard.service ~/.config/systemd/user/   # edit the paths if you cloned elsewhere
systemctl --user daemon-reload && systemctl --user enable --now tg-spamguard
loginctl enable-linger "$USER"     # keep it running when you're logged out
journalctl --user -u tg-spamguard -f
```

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `TG_API_ID`, `TG_API_HASH` | required | from my.telegram.org |
| `TG_MODE` | `log` | `log` = record only, `act` = report + block + delete |
| `TG_THRESHOLD` | `3` | score needed to count as spam |
| `TG_EXTRA_KEYWORDS` | — | comma-separated extra spam words or phrases |
| `TG_DATA_DIR` | script dir | where the session, log and whitelist live |

## Caveats

- **Deletion can't be undone.** That's why `log` is the default and why everything is logged.
- **Using the user API is allowed by Telegram,** but accounts that act like spam bots get limited.
  This tool only reacts to messages you receive, so its volume is tiny. Don't adapt it into
  something that sends messages in bulk.
- Spammers adapt. The keyword list is a starting point; `TG_EXTRA_KEYWORDS` and PRs are welcome.

## Development

```bash
.venv/bin/pip install pytest && PYTHONPATH=. .venv/bin/pytest -q
```

## License

MIT
