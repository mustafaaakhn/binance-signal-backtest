from dataclasses import replace
from decimal import Decimal as D

import pytest

from engine import Signal, Trade, gross_result, number, simulate


def signal(**overrides):
    base = Signal("BTCUSDT", "LONG", 1000, 10000, "signal", D(90), (D(110), D(120)))
    return replace(base, **overrides)


def trades(*prices, times=None):
    return [Trade(index + 2, times[index] if times else 2000 + index * 1000, D(p))
            for index, p in enumerate(prices)]


REF = Trade(1, 1000, D(100))


def test_signal_entry_uses_known_price_and_keeps_tp_before_stop():
    result = simulate(signal(), REF, trades(111, 89, 130))
    assert result.entry_price == 100
    assert [e.kind for e in result.events] == ["Entry", "TP 1", "SL"]
    assert result.targets[0].hit_ms == 2000
    assert result.targets[1].hit_ms is None
    assert result.targets[1].status == "Invalidated by SL"
    assert result.evaluated_until_ms == 3000


def test_stop_first_makes_all_later_targets_invalid():
    result = simulate(signal(), REF, trades(90, 120))
    assert [e.kind for e in result.events] == ["Entry", "SL"]
    assert all(row.hit_ms is None for row in result.targets)


@pytest.mark.parametrize("side,prices,stop,targets", [
    ("LONG", [110, 120, 80], 90, (110, 120)),
    ("SHORT", [90, 80, 120], 110, (90, 80)),
])
def test_all_targets_finish_position_before_later_stop(side, prices, stop, targets):
    result = simulate(signal(side=side, stop=D(stop), targets=tuple(map(D, targets))), REF, trades(*prices))
    assert result.status == "All take profits reached"
    assert result.stop_ms is None
    assert len(result.events) == 3


def test_same_millisecond_preserves_binance_id_order():
    result = simulate(signal(), REF, trades(110, 90, 120, times=[2000, 2000, 2000]))
    assert [e.kind for e in result.events] == ["Entry", "TP 1", "SL"]
    assert result.targets[1].hit_ms is None


def test_reversed_same_millisecond_changes_result():
    result = simulate(signal(), REF, trades(90, 110, times=[2000, 2000]))
    assert all(t.hit_ms is None for t in result.targets)


@pytest.mark.parametrize("side,stop,targets,jump,expected", [
    ("LONG", 90, (120, 110, 115), 121, ["TP 2", "TP 3", "TP 1"]),
    ("SHORT", 110, (80, 90, 85), 79, ["TP 2", "TP 3", "TP 1"]),
])
def test_multi_target_gap_is_ordered_by_price_not_input(side, stop, targets, jump, expected):
    result = simulate(signal(side=side, stop=D(stop), targets=tuple(map(D, targets))), REF, trades(jump))
    assert [e.kind for e in result.events[1:]] == expected
    assert [r.price for r in result.targets] == list(map(D, targets))


def test_entry_level_ignores_tp_before_entry():
    ref = Trade(1, 1000, D(125))
    result = simulate(signal(mode="level", entry=D(100)), ref, trades(130, 115, 100, 110))
    assert result.entry_ms == 4000
    assert result.targets[0].hit_ms == 5000
    assert result.targets[1].hit_ms is None


def test_entry_level_ignores_stop_before_entry():
    ref = Trade(1, 1000, D(80))
    result = simulate(signal(mode="level", entry=D(100)), ref, trades(85, 80, 100, 110))
    assert result.entry_ms == 4000
    assert result.stop_ms is None
    assert result.targets[0].hit_ms == 5000


@pytest.mark.parametrize("side,ref_price,stop,targets,jump", [
    ("LONG", 95, 90, (110, 120), 115),
    ("SHORT", 105, 110, (90, 80), 85),
])
def test_entry_crossing_and_tp_on_same_trade(side, ref_price, stop, targets, jump):
    ref = Trade(1, 1000, D(ref_price))
    result = simulate(signal(side=side, stop=D(stop), targets=tuple(map(D, targets)),
                             mode="level", entry=D(100)), ref, trades(jump))
    assert result.entry_price == 100
    assert [e.kind for e in result.events] == ["Entry", "TP 1"]
    assert result.entry_ms == result.targets[0].hit_ms == 2000


def test_entry_crossing_and_stop_on_same_trade():
    result = simulate(signal(mode="level", entry=D(100)), Trade(1, 1000, D(105)), trades(85, 125))
    assert [e.kind for e in result.events] == ["Entry", "SL"]


def test_entry_equal_to_signal_price_is_immediate():
    result = simulate(signal(mode="level", entry=D(100)), REF, trades(110))
    assert result.entry_ms == 1000


def test_level_never_reached_has_no_profit_calculation():
    result = simulate(signal(mode="level", entry=D(100), margin=D(100), leverage=D(10)),
                      Trade(1, 1000, D(105)), trades(106, 108))
    assert result.status == "Not entered"
    assert result.events == ()
    assert all(row.gross is None for row in result.targets)


def test_reference_may_be_older_but_entry_time_is_signal_time():
    result = simulate(signal(), Trade(1, 900, D(100)), [])
    assert result.entry_ms == 1000
    assert result.status == "Open at review end"


def test_future_reference_rejected():
    with pytest.raises(match="future"):
        simulate(signal(), Trade(1, 1001, D(100)), [])


@pytest.mark.parametrize("records", [
    [Trade(3, 2000, D(110))],
    [Trade(1, 2000, D(110))],
    [Trade(2, 900, D(110))],
    [Trade(2, 2000, D(105)), Trade(3, 1999, D(110))],
    [Trade(2, 1000, D(105))],
])
def test_bad_sequence_is_not_reported_as_success(records):
    with pytest.raises(ValueError):
        simulate(signal(), REF, records)


def test_end_time_is_inclusive():
    result = simulate(signal(end_ms=2000), REF, trades(110, 120, times=[2000, 2001]))
    assert result.targets[0].hit_ms == 2000
    assert result.targets[1].hit_ms is None


def test_arbitrary_number_of_targets():
    targets = tuple(D(100) + D(i) / 10 for i in range(1, 101))
    result = simulate(signal(targets=targets), REF, trades(111))
    assert len(result.targets) == 100
    assert all(row.hit_ms == 2000 for row in result.targets)


def test_hypothetical_gross_for_each_whole_position_is_not_divided():
    result = simulate(signal(margin=D(100), leverage=D(10)), REF, trades(110, 90, 120))
    assert [row.gross for row in result.targets] == [D(100), D(200)]
    assert [row.roi for row in result.targets] == [D(100), D(200)]
    assert result.targets[1].status == "Invalidated by SL"


def test_short_gross_and_loss():
    assert gross_result(D(100), D(80), "SHORT", D(200), D(5)) == D(200)
    assert gross_result(D(100), D(110), "SHORT", D(200), D(5)) == D(-100)


def test_decimal_prices_do_not_miss_equality():
    ref = Trade(1, 1000, D("0.1"))
    result = simulate(signal(stop=D("0.05"), targets=(D("0.3"),), margin=D(10), leverage=D(2)),
                      ref, trades("0.3"))
    assert result.targets[0].gross == D(40)


@pytest.mark.parametrize("changes", [
    {"targets": ()}, {"targets": (D(110), D(110))}, {"targets": (D(99),)},
    {"stop": D(100)}, {"stop": D(-1)}, {"end_ms": 1000},
    {"margin": D(100)}, {"leverage": D(10)}, {"margin": D(0), "leverage": D(2)},
    {"mode": "level", "entry": None}, {"side": "INVALID"}, {"mode": "invalid"},
    {"targets": (D("NaN"),)}, {"stop": D("Infinity")},
])
def test_invalid_inputs(changes):
    with pytest.raises(ValueError):
        simulate(signal(**changes), REF, [])


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "", "abc", "1,000.10"])
def test_invalid_numeric_text(value):
    with pytest.raises(ValueError):
        number(value)


def test_turkish_decimal():
    assert number("1,25") == D("1.25")


@pytest.mark.parametrize("side,stop,targets,prices,exit_price,pnl,close_ms", [
    ("LONG", 90, (110, 120), (110, 90, 120), 90, -100, 3000),
    ("LONG", 90, (110, 120), (110, 120, 90), 120, 200, 3000),
    ("LONG", 90, (110, 120), (90, 110, 120), 90, -100, 2000),
    ("SHORT", 110, (90, 80), (90, 110, 80), 110, -100, 3000),
    ("SHORT", 110, (90, 80), (90, 80, 110), 80, 200, 3000),
    ("SHORT", 110, (90, 80), (110, 90, 80), 110, -100, 2000),
])
def test_close_and_balance(side, stop, targets, prices, exit_price, pnl, close_ms):
    config = signal(side=side, stop=D(stop), targets=tuple(map(D, targets)),
                    margin=D(100), leverage=D(10))
    result = simulate(config, REF, trades(*prices))
    assert result.summary.close_price == D(exit_price)
    assert result.summary.close_ms == close_ms
    assert result.summary.position_size == D(1000)
    assert result.summary.pnl == D(pnl)
    assert result.summary.roi == D(pnl)
    assert result.summary.ending_balance == D(100 + pnl)


@pytest.mark.parametrize("prices,status", [
    ((110, 90, 120), "Closed at SL"),
    ((110, 120, 90), "Closed at TP"),
])
def test_close_at_same_millisecond(prices, status):
    result = simulate(signal(), REF, trades(*prices, times=[2000, 2000, 2000]))
    assert result.summary.status == status
    assert result.summary.close_ms == 2000


@pytest.mark.parametrize("side,targets,stop,jump,expected", [
    ("LONG", (120, 110, 115), 90, 130, 120),
    ("SHORT", (80, 90, 85), 110, 70, 80),
])
def test_close_uses_furthest_tp(side, targets, stop, jump, expected):
    config = signal(side=side, targets=tuple(map(D, targets)), stop=D(stop),
                    margin=D(200), leverage=D(5))
    result = simulate(config, REF, trades(jump))
    assert result.summary.close_price == expected
    assert result.summary.pnl == D(200)
    assert result.summary.ending_balance == D(400)


@pytest.mark.parametrize("reference,jump,expected", [(95, 125, 120), (105, 85, 90)])
def test_entry_and_close_on_same_trade(reference, jump, expected):
    result = simulate(signal(mode="level", entry=D(100)),
                      Trade(1, 1000, D(reference)), trades(jump))
    assert result.summary.close_price == expected
    assert result.entry_ms == result.summary.close_ms == 2000


@pytest.mark.parametrize("mode,reference,status,size", [
    ("signal", 100, "Open at review end", D(1000)),
    ("level", 105, "Not entered", None),
])
def test_unclosed_balance(mode, reference, status, size):
    config = signal(mode=mode, entry=D(100), margin=D(100), leverage=D(10))
    result = simulate(config, Trade(1, 1000, D(reference)), trades(110, 115))
    assert result.summary.status == status
    assert result.summary.position_size == size
    assert result.summary.close_price is result.summary.close_ms is None
    assert result.summary.pnl is result.summary.roi is result.summary.ending_balance is None


def test_close_without_margin():
    result = simulate(signal(), REF, trades(110, 90))
    assert result.summary.close_price == D(90)
    assert result.summary.close_ms == 3000
    assert result.summary.pnl is None


def test_decimal_balance():
    config = signal(stop=D("0.09"), targets=(D("0.11"), D("0.12")),
                    margin=D("12.5"), leverage=D(3))
    result = simulate(config, Trade(1, 1000, D("0.10")), trades("0.11", "0.12"))
    assert result.summary.position_size == D("37.5")
    assert result.summary.pnl == D("7.5")
    assert result.summary.roi == D(60)
    assert result.summary.ending_balance == D(20)


def test_close_after_end_is_ignored():
    result = simulate(signal(end_ms=2000), REF, trades(110, 120, times=[2000, 2001]))
    assert result.summary.close_ms is None
