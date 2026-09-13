from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from streamlit.testing.v1 import AppTest

from app import duration, money, price, timestamp, usdt_symbol
from engine import Trade

APP = Path(__file__).resolve().parents[1] / "app.py"


def field(app, label):
    return next(item for item in app.text_input if item.label == label)


def button(app, label):
    return next(item for item in app.button if item.label == label)


def fake_history(monkeypatch, prices):
    from binance import BinanceData
    monkeypatch.setattr(BinanceData, "reference_trade", lambda self, symbol, start: Trade(1, start, Decimal(100)))

    def records(self, symbol, start, end):
        for index, value in enumerate(prices):
            yield Trade(index + 2, start + (index + 1) * 60_000, Decimal(value))

    monkeypatch.setattr(BinanceData, "iter_trades", records)


def configured_app(monkeypatch, prices, targets="110\n120", stop="90", side="LONG", margin=True):
    fake_history(monkeypatch, prices)
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    field(app, "Stop loss price").set_value(stop)
    if margin:
        field(app, "Margin · USDT").set_value("100")
        field(app, "Leverage · ×").set_value("10")
    app.selectbox[0].set_value(side)
    app.text_area[0].set_value(targets)
    return app


def test_form_and_validation_are_english():
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    assert not app.exception
    assert app.title[0].value == "Binance Signal Analysis"
    assert not any(item.label == "Quote currency" for item in app.text_input)
    assert field(app, "Coin symbol").value == "BTC"
    assert [item.label for item in app.selectbox] == ["Direction"]
    button(app, "Analyze signal").click().run()
    assert app.error[0].value == "Enter a valid number without thousands separators."
    assert not app.exception


@pytest.mark.parametrize("side,targets,stop,prices,pnl,status,close", [
    ("LONG", "110\n120", "90", [110, 90, 120], "-100.00 USDT", "Closed at SL", "90"),
    ("SHORT", "90\n80", "110", [90, 80, 110], "+200.00 USDT", "Closed at TP", "80"),
    ("LONG", "120\n110", "90", [110, 120], "+200.00 USDT", "Closed at TP", "120"),
    ("SHORT", "80\n90", "110", [90, 80], "+200.00 USDT", "Closed at TP", "80"),
])
def test_automatic_final_target_summary(monkeypatch, side, targets, stop, prices, pnl, status, close):
    app = configured_app(monkeypatch, prices, targets, stop, side)
    button(app, "Analyze signal").click().run()
    assert not app.exception
    assert not app.error
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Gross P&L"] == pnl
    assert metrics["Starting balance"] == "100.00 USDT"
    assert metrics["Ending balance"] == ("0.00 USDT" if "SL" in status else "300.00 USDT")
    assert any(status in caption.value for caption in app.caption)
    assert any(item.value == f"**{close}**" for item in app.markdown)
    assert any(item.value == "**2 min 0 sec**" for item in app.markdown)
    assert len(app.dataframe) == 2
    assert list(app.dataframe[0].value["Gross P&L at TP · USDT"]) == (["+100.00", "+200.00"] if targets in ("110\n120", "90\n80") else ["+200.00", "+100.00"])


def test_entry_method_is_in_entry_section_and_enables_price():
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    assert field(app, "Entry price").disabled
    app.radio[0].set_value("At a specific price").run()
    assert not field(app, "Entry price").disabled
    assert not app.exception


def test_now_updates_both_fields_without_network(monkeypatch):
    from binance import BinanceData
    def unexpected(*args, **kwargs):
        raise AssertionError("Editing must not fetch Binance data")
    monkeypatch.setattr(BinanceData, "reference_trade", unexpected)
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    old_date = datetime.now(ZoneInfo("Europe/Istanbul")).date() - timedelta(days=3)
    app.date_input(key="end_date").set_value(old_date).run()
    before = datetime.now(ZoneInfo("Europe/Istanbul")).replace(microsecond=0)
    button(app, "Now").click().run()
    after = datetime.now(ZoneInfo("Europe/Istanbul")).replace(microsecond=0)
    selected = datetime.combine(app.date_input(key="end_date").value, app.time_input(key="end_time").value, ZoneInfo("Europe/Istanbul"))
    assert before <= selected <= after
    assert not app.exception
    assert not app.status


@pytest.mark.parametrize("targets", ["", "110\n110", "110\nabc", "NaN"])
def test_invalid_targets_show_english_error(monkeypatch, targets):
    app = configured_app(monkeypatch, [], targets=targets)
    button(app, "Analyze signal").click().run()
    assert app.error
    assert not app.exception
    assert not any(char in app.error[0].value for char in "çğıöşüÇĞİÖŞÜ")


def test_without_margin_still_shows_close_time_and_duration(monkeypatch):
    app = configured_app(monkeypatch, [110, 120], margin=False)
    button(app, "Analyze signal").click().run()
    assert not app.exception
    assert not any(metric.label == "Gross P&L" for metric in app.metric)
    assert any(item.value == "**2 min 0 sec**" for item in app.markdown)


@pytest.mark.parametrize("mode,status", [("Price at signal time", "Open at review end"),
                                         ("At a specific price", "Not entered")])
def test_unclosed_summary_does_not_show_hypothetical_profit(monkeypatch, mode, status):
    app = configured_app(monkeypatch, [101, 102])
    app.radio[0].set_value(mode).run()
    if mode == "At a specific price":
        field(app, "Entry price").set_value("99")
    button(app, "Analyze signal").click().run()
    assert not app.exception
    metrics = {metric.label: metric.value for metric in app.metric}
    for label in ("Gross P&L", "Ending balance"):
        assert metrics[label] == "—"
    assert any(status in caption.value for caption in app.caption)


@pytest.mark.parametrize("value,expected", [("sol", "SOLUSDT"), (" BTC ", "BTCUSDT"), ("ETHUSDT", "ETHUSDT"), ("1000PEPE", "1000PEPEUSDT")])
def test_fixed_usdt_symbol(value, expected):
    assert usdt_symbol(value) == expected


@pytest.mark.parametrize("value", ["", "USDT", "BTCUSDC", "../BTC", "BTC/USDT"])
def test_invalid_coin(value):
    with pytest.raises(ValueError):
        usdt_symbol(value)


def test_formatting_and_istanbul_timezone():
    assert money(Decimal("200.0000")) == "200.00 USDT"
    assert money(Decimal("208.2799")) == "208.28 USDT"
    assert price(Decimal("0.00001230")) == "0.0000123"
    assert price(Decimal("200")) == "200"
    assert timestamp(0) == "01 Jan 1970, 02:00:00"  # Historical Istanbul offset.
    assert timestamp(1704067200000) == "01 Jan 2024, 03:00:00"
    assert duration(None, 1000) == "—"
    assert duration(1000, 1000) == "0 sec"
    assert duration(1000, 1001) == "< 1 sec"
    assert duration(1000, 61000) == "1 min 0 sec"
    assert duration(1000, 7381000) == "2 hr 3 min"
