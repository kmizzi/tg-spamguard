from types import SimpleNamespace

import spamguard


def user(**kw):
    base = dict(scam=False, fake=False, photo=object(), username="someone")
    return SimpleNamespace(**{**base, **kw})


def test_plain_greeting_from_normal_profile_is_not_spam():
    s, _ = spamguard.score(user(), "hey, long time no see")
    assert s < spamguard.THRESHOLD


def test_crypto_pitch_with_link_is_spam():
    s, why = spamguard.score(user(), "Earn 300% with this crypto signal https://x.top")
    assert s >= spamguard.THRESHOLD
    assert "link/handle" in why


def test_keyword_from_faceless_account_is_spam():
    s, _ = spamguard.score(user(photo=None, username=None), "Hello dear, are you into investment?")
    assert s >= spamguard.THRESHOLD


def test_telegram_scam_flag_always_wins():
    s, _ = spamguard.score(user(scam=True), "hi")
    assert s >= spamguard.THRESHOLD


def test_keyword_match_is_reported_lowercase():
    _, why = spamguard.score(user(), "Join our VIP GROUP today")
    assert "keyword:vip group" in why


def test_romance_opener_from_faceless_account_is_spam():
    s, _ = spamguard.score(user(username=None), "Looking for chemistry?")
    assert s >= spamguard.THRESHOLD
