from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


def number(value):
    try:
        result = Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("Enter a valid number without thousands separators.") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("Prices, margin and leverage must be greater than zero.")
    return result


@dataclass
class Trade:
    id: int
    time_ms: int
    price: Decimal


@dataclass
class Signal:
    symbol: str
    side: str
    start_ms: int
    end_ms: int
    mode: str
    stop: Decimal
    targets: tuple[Decimal, ...]
    entry: Decimal | None = None
    margin: Decimal | None = None
    leverage: Decimal | None = None

    def validate(self):
        if self.side not in ("LONG", "SHORT") or self.mode not in ("signal", "level"):
            raise ValueError("Invalid direction or entry method.")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("The end time must be after the signal time.")
        if not self.targets:
            raise ValueError("Enter at least one take profit price.")
        if len(set(self.targets)) != len(self.targets):
            raise ValueError("Take profit prices must be unique.")
        for value in (self.stop, *self.targets):
            number(value)
        if self.mode == "level":
            if self.entry is None:
                raise ValueError("Enter an entry price.")
            self.validate_levels(number(self.entry))
        if (self.margin is None) != (self.leverage is None):
            raise ValueError("Enter both margin and leverage to calculate gross results.")
        if self.margin is not None:
            number(self.margin)
            number(self.leverage)

    def validate_levels(self, entry):
        sign = 1 if self.side == "LONG" else -1
        if sign * (self.stop - entry) >= 0:
            raise ValueError("The stop loss must be below entry for LONG and above entry for SHORT.")
        if any(sign * (tp - entry) <= 0 for tp in self.targets):
            raise ValueError("All take profit prices must be above entry for LONG and below entry for SHORT.")


@dataclass
class Event:
    kind: str
    time_ms: int
    price: Decimal
    observed_price: Decimal


@dataclass
class TargetResult:
    index: int
    price: Decimal
    hit_ms: int | None
    status: str
    gross: Decimal | None
    roi: Decimal | None


@dataclass
class Summary:
    status: str
    close_price: Decimal | None = None
    close_ms: int | None = None
    position_size: Decimal | None = None
    pnl: Decimal | None = None
    roi: Decimal | None = None
    ending_balance: Decimal | None = None


@dataclass
class Result:
    status: str
    entry_price: Decimal | None
    entry_ms: int | None
    reference: Trade
    events: tuple[Event, ...]
    targets: tuple[TargetResult, ...]
    stop_ms: int | None
    evaluated_until_ms: int
    summary: Summary


def gross_result(entry, exit_price, side, margin, leverage):
    sign = 1 if side == "LONG" else -1
    return margin * leverage * sign * (exit_price - entry) / entry


def summarize(signal, entry_price, close):
    result = Summary("Not entered" if entry_price is None else "Open at review end")
    if close is not None:
        reason = "SL" if close.kind == "SL" else "TP"
        result.status = f"Closed at {reason}"
        result.close_price = close.price
        result.close_ms = close.time_ms
    if entry_price is not None and signal.margin is not None:
        result.position_size = signal.margin * signal.leverage
        if close is not None:
            result.pnl = gross_result(entry_price, close.price, signal.side, signal.margin, signal.leverage)
            result.roi = result.pnl / signal.margin * 100
            result.ending_balance = signal.margin + result.pnl
    return result


def simulate(signal, reference, trades):
    signal.validate()
    number(reference.price)
    if reference.time_ms > signal.start_ms:
        raise ValueError("The signal price cannot come from a future trade.")
    entry_price = None
    entry_ms = None
    stop_ms = None
    close = None
    events = []
    hits = {}
    # Target order follows price, not the order in the form.
    ordered = sorted(enumerate(signal.targets, 1), key=lambda item: item[1],
                     reverse=signal.side == "SHORT")
    sign = 1 if signal.side == "LONG" else -1

    def enter(price, timestamp, trade):
        nonlocal entry_price, entry_ms
        signal.validate_levels(price)
        entry_price, entry_ms = price, timestamp
        events.append(Event("Entry", timestamp, price, trade.price))

    if signal.mode == "signal":
        enter(reference.price, signal.start_ms, reference)
    elif reference.price == signal.entry:
        enter(signal.entry, signal.start_ms, reference)

    previous = reference
    evaluated_until = signal.end_ms
    for trade in trades:
        number(trade.price)
        if trade.time_ms < previous.time_ms or trade.id <= previous.id:
            raise ValueError("Binance trades are out of order or duplicated.")
        if trade.id != previous.id + 1:
            raise ValueError("Binance trade records contain a gap; no result was calculated.")
        if trade.time_ms <= signal.start_ms:
            raise ValueError("The trade stream must start after the signal time.")
        if trade.time_ms > signal.end_ms:
            break

        if entry_price is None:
            level = signal.entry
            if min(previous.price, trade.price) <= level <= max(previous.price, trade.price):
                enter(level, trade.time_ms, trade)
        if entry_price is not None:
            if sign * (trade.price - signal.stop) <= 0:
                stop_ms = trade.time_ms
                events.append(Event("SL", trade.time_ms, signal.stop, trade.price))
                close = events[-1]
                evaluated_until = trade.time_ms
                break
            for index, target in ordered:
                if index not in hits and sign * (trade.price - target) >= 0:
                    hits[index] = trade.time_ms
                    events.append(Event(f"TP {index}", trade.time_ms, target, trade.price))
            if len(hits) == len(signal.targets):
                close = events[-1]
                evaluated_until = trade.time_ms
                break
        previous = trade

    rows = []
    for index, target in enumerate(signal.targets, 1):
        gross = roi = None
        if entry_price is not None and signal.margin is not None:
            gross = gross_result(entry_price, target, signal.side, signal.margin, signal.leverage)
            roi = gross / signal.margin * 100
        if index in hits:
            status = "Reached"
        elif entry_price is None:
            status = "Not entered"
        elif stop_ms is not None:
            status = "Invalidated by SL"
        else:
            status = "Not reached"
        rows.append(TargetResult(index, target, hits.get(index), status, gross, roi))
    if entry_price is None:
        status = "Not entered"
    elif stop_ms is not None:
        status = "Stop loss reached"
    elif len(hits) == len(signal.targets):
        status = "All take profits reached"
    else:
        status = "Open at review end"
    return Result(status, entry_price, entry_ms, reference, tuple(events), tuple(rows),
                  stop_ms, evaluated_until, summarize(signal, entry_price, close))
