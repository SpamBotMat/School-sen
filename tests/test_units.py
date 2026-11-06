"""Юнит-тесты чистой логики: оценки, подписи Telegram, шифрование, валидация, лимиты, предохранитель, погода."""
import time

import pytest

from app import grades as G
from app.breaker import Breaker, looks_like_block
from app.crypto import CredentialsError, Vault, generate_key
from app.phrases import render as render_phrase, validate as validate_phrase
from app.ratelimit import RateLimits
from app.schemas import ValidationError, clean_runtime, clean_theme, clean_user_settings
from app.security import AdminAuth, sign_init_data, validate_init_data
from app.weather import DayWeather, advice_for

TOKEN = "123456:ABC-test"


# ───────────── оценки ─────────────

def test_values_of_skips_non_marks():
    assert G.values_of(["5", "4", None, "", 3, 7, 0]) == [5, 4, 3]


@pytest.mark.parametrize("values,grade,status,up", [
    ([5, 5, 4], 5, "sure", None),        # 4.67 → точно 5
    ([5, 4], 4, "maybe", 5),             # 4.50 → 4, можно вытянуть на 5
    ([4, 4, 5], 4, "floor", None),       # 4.33 → 4
    ([5, 5, 5], 5, "top", None),
    ([3, 4], 3, "maybe", 4),             # 3.50
    ([2, 2, 3], 2, "floor", None),
])
def test_predict_thresholds(values, grade, status, up):
    p = G.predict(values)
    assert (p.grade, p.status, p.up) == (grade, status, up)


def test_predict_edge_062_is_sure():
    # 4.62 ровно — «точно» (сравнение по двум знакам, как на сайте)
    vals = [5] * 31 + [4] * 19       # 4.62
    assert round(sum(vals) / len(vals), 2) == 4.62
    assert G.predict(vals).status == "sure"
    vals2 = [5] * 61 + [4] * 39      # 4.61
    assert G.predict(vals2).status == "maybe"


def test_predict_custom_thresholds():
    assert G.predict([5, 4], sure=0.5, maybe=0.4).status == "sure"
    assert G.predict([5, 4, 4], sure=0.7, maybe=0.3).status == "maybe"   # 4.33


def test_needed_fives():
    assert G.needed([4, 4], 4.62) == 4            # (8+5n)/(2+n) ≥ 4.62 → n ≥ 3.26
    assert G.needed([5, 5], 4.62) == 0
    assert G.needed([2, 2], 5.0) is None          # пятёрками до 5.00 не дотянуть
    assert G.needed([], 4.62) == 1


def test_what_if_and_texts():
    base = [4, 4, 5]
    assert G.what_if(base, [5, 5]).status == "maybe"          # 4.60
    p = G.what_if(base, [5, 5, 5])                               # 4.67
    assert p.grade == 5 and p.status == "sure"
    assert G.what_if(base, [9, 0]).avg == G.predict(base).avg     # мусор игнорируется
    assert G.prediction_text(G.predict([5, 4])) == "→ 4, можно вытянуть на 5"
    assert G.prediction_text(G.predict([])) == "оценок пока нет"
    assert "пятёрки" in G.need_text(G.predict([4, 4]))


def test_mood_of():
    assert G.mood_of(None) == "none"
    assert G.mood_of(4.8) == "great"
    assert G.mood_of(2.5) == "bad"


# ───────────── подпись Telegram ─────────────

def test_init_data_roundtrip_and_tamper():
    raw = sign_init_data({"id": 77, "first_name": "Тест"}, TOKEN)
    user = validate_init_data(raw, TOKEN)
    assert user and user["id"] == 77
    assert validate_init_data(raw, "999:other") is None
    assert validate_init_data(raw.replace("77", "78"), TOKEN) is None
    assert validate_init_data("", TOKEN) is None
    assert validate_init_data("garbage", TOKEN) is None


def test_init_data_expired():
    raw = sign_init_data({"id": 1}, TOKEN, auth_date=int(time.time()) - 8 * 86400)
    assert validate_init_data(raw, TOKEN) is None
    assert validate_init_data(raw, TOKEN, max_age=0) is not None


def test_admin_otp_single_use_and_attempts():
    auth = AdminAuth()
    code = auth.issue(5)
    assert auth.verify(5, "000000" if code != "000000" else "111111") is None
    res = auth.verify(5, code)
    assert res is not None
    token, sess = res
    assert auth.session(token) is sess and sess.admin_id == 5
    assert auth.verify(5, code) is None             # одноразовый
    auth.revoke(token)
    assert auth.session(token) is None
    # 5 неверных попыток — код сгорает
    code = auth.issue(6)
    for _ in range(5):
        auth.verify(6, "999999" if code != "999999" else "888888")
    assert auth.verify(6, code) is None


def test_admin_resend_cooldown():
    auth = AdminAuth()
    assert auth.can_send(1) == 0
    auth.issue(1)
    assert auth.can_send(1) > 0


# ───────────── шифрование ─────────────

def test_vault_roundtrip_and_wrong_key():
    v = Vault(generate_key())
    token = v.seal("ABCDEF0123", 9)
    assert "abcdef0123" not in token
    assert v.open(token) == ("abcdef0123", 9)
    with pytest.raises(CredentialsError):
        Vault(generate_key()).open(token)
    with pytest.raises(CredentialsError):
        Vault("not-a-key")


# ───────────── валидация ─────────────

def test_user_settings_validation():
    assert clean_user_settings({"morning_time": "07:45", "n_marks": 0, "unknown": 1}) == {
        "morning_time": "07:45", "n_marks": False}
    with pytest.raises(ValidationError):
        clean_user_settings({"morning_time": "03:00"})       # слишком рано
    with pytest.raises(ValidationError):
        clean_user_settings({"evening_time": "25:00"})


def test_theme_validation():
    out = clean_theme({"palette": "nope", "mode": "dark", "accent": "#12ab34", "radius": 99, "font": 10, "lite": 1})
    assert out == {"mode": "dark", "accent": "#12ab34", "radius": 28, "font": 85, "lite": True}
    assert "accent" not in clean_theme({"accent": "red; background:url(x)"})


def test_runtime_validation():
    assert clean_runtime({"sync_interval": "45", "calc_sure": 0.7})["sync_interval"] == 45
    with pytest.raises(ValidationError):
        clean_runtime({"sync_interval": 1})                   # слишком часто — бережём сайт школы
    with pytest.raises(ValidationError):
        clean_runtime({"sync_concurrency": 10})
    with pytest.raises(ValidationError):
        clean_runtime({"calc_sure": 1.5})
    bells = clean_runtime({"bells": {"1": ["09:00", "09:45"]}})["bells"]
    assert bells == {"1": ["09:00", "09:45"]}


def test_phrase_validation_blocks_attribute_access():
    assert validate_phrase("Привет, {name}!") is None
    assert validate_phrase("{name.__class__}") is not None
    assert validate_phrase("{0}") is not None
    assert validate_phrase("кривая { скобка") is not None
    assert validate_phrase("") is not None
    assert render_phrase("Привет, {name}! {missing}", {"name": "Алекс"}) == "Привет, Алекс! "


# ───────────── лимиты и предохранитель ─────────────

def test_rate_limits():
    rl = RateLimits()
    assert all(rl.allow("k", 3, 60) for _ in range(3))
    assert not rl.allow("k", 3, 60)
    assert rl.retry_after("k", 3, 60) > 0
    rl.reset("k")
    assert rl.allow("k", 3, 60)


def test_breaker_opens_after_streak_and_probes():
    alerts = []
    b = Breaker(streak=3, cooldown_min=10)
    b._notify = alerts.append          # без бота
    for _ in range(2):
        b.failure("timeout")
    assert b.state == "closed" and b.allow()
    b.failure("timeout")
    assert b.state == "open" and not b.allow()
    assert alerts and alerts[0].startswith("⛔")
    # кулдаун прошёл → ровно один пробный запрос
    b.opened_at = b.opened_at.replace(year=b.opened_at.year - 1)
    assert b.allow() is True and b.state == "half"
    assert b.allow() is False
    b.failure("timeout")                # проба не удалась → снова пауза, дольше
    assert b.state == "open" and b.cooldown == 20
    b.opened_at = b.opened_at.replace(year=b.opened_at.year - 1)
    assert b.allow()
    b.success()
    assert b.state == "closed" and b.cooldown == 10


def test_breaker_block_markers_open_immediately():
    b = Breaker(streak=50, cooldown_min=10)
    b._notify = lambda *_: None
    assert looks_like_block("HTTP 403 Forbidden")
    b.failure("HTTP 403 Forbidden")
    assert b.state == "open"


# ───────────── погода ─────────────

def test_weather_advice():
    assert advice_for(DayWeather(date="2026-11-06", tmin=10, tmax=20, code=61, precip_prob=80))[0] == "weather.rain"
    assert "weather.cold" in advice_for(DayWeather(date="d", tmin=-2, tmax=4, code=0))
    assert advice_for(DayWeather(date="d", tmin=12, tmax=22, code=1, precip_prob=5)) == ["weather.nice"]
    hot = advice_for(DayWeather(date="d", tmin=25, tmax=38, code=0, uv=9))
    assert "weather.hot" in hot and "weather.uv" not in hot
