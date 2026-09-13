from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import streamlit as st

from binance import BinanceData, BinanceError, validate_symbol
from engine import Signal, number, simulate

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo("Europe/Istanbul")


def timestamp(value):
    if value is None:
        return "—"
    return datetime.fromtimestamp(value / 1000, TZ).strftime("%d %b %Y, %H:%M:%S")


def duration(start, end):
    if start is None or end is None:
        return "—"
    seconds = max(0, (end - start) // 1000)
    if end > start and seconds == 0:
        return "< 1 sec"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours} hr {minutes} min"
    if minutes:
        return f"{minutes} min {seconds} sec"
    return f"{seconds} sec"


def price(value):
    if value is None:
        return "—"
    formatted = format(value, "f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def money(value, signed=False):
    if value is None:
        return "—"
    return f"{value:+,.2f} USDT" if signed else f"{value:,.2f} USDT"


def usdt_symbol(value):
    base = value.strip().upper()
    if base.endswith("USDT"):
        base = base[:-4]
    if not re.fullmatch(r"[A-Z0-9]{1,26}", base):
        raise ValueError("Enter a coin symbol such as BTC, ETH or SOL.")
    if base.endswith(("USDC", "BUSD")):
        raise ValueError("Enter only the coin symbol. The quote currency is fixed to USDT.")
    return validate_symbol(base + "USDT")


def set_end_to_now():
    now = datetime.now(TZ).replace(microsecond=0)
    st.session_state["end_date"] = now.date()
    st.session_state["end_time"] = now.time()


def style():
    st.html("""<style>
    .stApp { background: #0d1118; color: #e8edf5; }
    .stMainBlockContainer { max-width: 1160px; padding-top: 5rem; padding-bottom: 4rem; }
    h1 { letter-spacing: -0.045em; font-weight: 750 !important; }
    h3 { letter-spacing: -0.025em; }
    [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color: #a9b6c8; opacity: 1; }
    [data-testid="stVerticalBlockBorderWrapper"] > div { border-radius: 16px; }
    [data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea {
        font-variant-numeric: tabular-nums; }
    .st-key-coin_symbol [data-testid="stTextInputRootElement"]::after {
        content: "USDT"; flex: 0 0 auto; align-self: center;
        border-left: 1px solid #526075; margin-right: 14px; padding-left: 14px;
        color: #c3cedd; font-size: 0.875rem; line-height: 1.5;
        pointer-events: none;
    }
    .st-key-coin_symbol input { min-width: 0; }
    .st-key-review_now button { color: #80e0c0; min-height: 0; padding: 0 4px; }
    [data-testid="stMetric"] { background: #151d29; border: 1px solid #283345;
        border-radius: 12px; padding: 16px 18px; height: 100%; min-height: 126px; }
    [data-testid="stMetricValue"] { font-size: clamp(1.25rem, 2.2vw, 1.8rem); font-variant-numeric: tabular-nums; }
    [data-testid="stMetricLabel"] { color: #aab7c9; }
    [data-testid="stButton"] button[kind="primary"] { background: #80e0c0; color: #0d211c;
        border: 0; font-weight: 700; min-height: 46px; border-radius: 10px; }
    [data-testid="stButton"] button[kind="primary"]:hover { background: #a1efd5; }
    [data-testid="stTabs"] button { font-size: 1rem; }
    @media (max-width: 640px) { .stMainBlockContainer { padding-top: 5rem; }
        [data-testid="stMetricValue"] { font-size: 1.45rem; } }
    </style>""")


def show_summary(result, signal):
    summary = result.summary
    st.subheader("Trade overview")
    st.caption(f"{signal.symbol} · {signal.side} · {summary.status}")
    if signal.margin is not None:
        a, b, c = st.columns(3)
        a.metric("Starting balance", money(signal.margin))
        b.metric("Gross P&L", money(summary.pnl, signed=True),
                 delta=None if summary.roi is None else f"{summary.roi:+.2f}% on margin")
        c.metric("Ending balance", money(summary.ending_balance))
    with st.container(border=True):
        a, b, c = st.columns([1, 1.4, 1])
        a.caption("CLOSE PRICE")
        a.write(f"**{price(summary.close_price)}**")
        b.caption("CLOSED AT · ISTANBUL")
        b.write(f"**{timestamp(summary.close_ms)}**")
        c.caption("TIME TO CLOSE")
        c.write(f"**{duration(result.entry_ms, summary.close_ms)}**")
        if summary.close_ms is None and result.entry_ms is not None:
            st.caption(f"Open for {duration(result.entry_ms, result.evaluated_until_ms)} at review end.")
        if signal.margin is not None:
            st.caption(f"Margin {money(signal.margin)}  ·  Leverage {price(signal.leverage)}×  ·  "
                       f"Position size {money(summary.position_size)}")
    st.caption("The whole trade closes at the furthest TP or SL, whichever comes first. "
               "Fees, funding, slippage and liquidation are not included. Ending balance = margin + gross P&L.")


def show_result(result, signal, sources):
    show_summary(result, signal)
    if result.status == "All take profits reached":
        st.success(result.status)
    elif result.stop_ms is not None:
        st.warning(result.status)
    else:
        st.info(result.status)
    left, middle, right = st.columns(3)
    left.metric("Entry price", price(result.entry_price))
    middle.metric("Take profits reached", f"{sum(row.hit_ms is not None for row in result.targets)} / {len(result.targets)}")
    right.metric("Stop loss", "Reached" if result.stop_ms is not None else "Not reached")
    st.caption(f"Entered {timestamp(result.entry_ms)} · Reviewed through {timestamp(result.evaluated_until_ms)} · Istanbul (UTC+3)")

    targets_tab, events_tab = st.tabs(["Take profits", "Event timeline"])
    with targets_tab:
        rows = []
        for target in result.targets:
            row = {"Target": f"TP {target.index}", "Price": price(target.price),
                   "Status": target.status, "First reached · Istanbul": timestamp(target.hit_ms),
                   "Time from entry": duration(result.entry_ms, target.hit_ms)}
            if signal.margin is not None:
                row["Gross P&L at TP · USDT"] = "—" if target.gross is None else f"{target.gross:+,.2f}"
                row["Return on margin"] = "—" if target.roi is None else f"{target.roi:+.2f}%"
            rows.append(row)
        st.dataframe(rows, hide_index=True, width="stretch")
        if signal.margin is not None:
            st.caption("Each row shows the result if you closed the whole trade at that TP. Do not add the results together. "
                       "If a TP was not reached, its result is only an estimate.")
    with events_tab:
        if result.events:
            st.dataframe([{"Event": event.kind, "Time · Istanbul": timestamp(event.time_ms),
                           "Time from entry": duration(result.entry_ms, event.time_ms),
                           "Modeled price": price(event.price), "Binance trade price": price(event.observed_price)}
                          for event in result.events], hide_index=True, width="stretch")
        else:
            st.write("The entry price was not reached during the review period.")

    with st.expander("Data sources & calculation details"):
        st.write(f"Price known at signal time: {price(result.reference.price)} · "
                 f"Reference trade: {timestamp(result.reference.time_ms)} (Istanbul).")
        st.write("The starting price comes from the last Binance trade at or before your signal time. "
                 "If you enter a price, the trade starts when the market first touches or crosses it. "
                 "TP and SL movements before entry do not count. Trades at the same time are checked in Binance ID order.")
        st.write("The calculation uses your entry, TP and SL prices. If the market crosses several targets at once, "
                 "they are checked in price order. Real orders may fill at different prices. "
                 "The review ends at SL, when all TPs are reached, or at your chosen end time.")
        st.write("Gross P&L = margin × leverage × directional price change / entry price. "
                 "All amounts are in USDT. Starting balance is the margin you entered for this trade. "
                 "The app does not read your account balance or place orders. Data comes from Binance trade records (aggTrades).")
        for source in sorted(sources):
            st.markdown(f"- [Official Binance data source]({source})")


def main():
    st.set_page_config(page_title="Binance Signal Analysis", page_icon="📈", layout="wide")
    style()
    st.caption("BINANCE USD M FUTURES  /  SIGNAL REVIEW")
    st.title("Binance Signal Analysis")
    st.write("Check when your trade entered and which TP or SL prices it reached.")
    st.caption("All times are in Istanbul time (UTC+3). This app does not place orders.")
    now = st.session_state.setdefault("form_default_end", datetime.now(TZ).replace(microsecond=0))
    initial = now - timedelta(days=1)
    st.session_state.setdefault("end_date", now.date())
    st.session_state.setdefault("end_time", now.time())
    with st.container(border=True):
        st.subheader("Market & timing")
        left, right = st.columns(2)
        symbol = left.text_input("Coin symbol", value="BTC", placeholder="BTC, ETH, SOL", key="coin_symbol")
        side = right.selectbox("Direction", ["LONG", "SHORT"])
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Signal time**")
            date_col, time_col = st.columns(2)
            start_date = date_col.date_input("Signal date", value=initial.date(), min_value=datetime(2019, 9, 8).date(), format="DD/MM/YYYY")
            start_time = time_col.time_input("Signal time", value=initial.time(), step=timedelta(seconds=1))
        with c2:
            heading, action = st.columns([4, 1], vertical_alignment="center", wrap=False)
            heading.markdown("**Review until**")
            action.button("Now", key="review_now", type="tertiary", on_click=set_end_to_now)
            date_col, time_col = st.columns(2)
            end_date = date_col.date_input("End date", key="end_date", min_value=datetime(2019, 9, 8).date(), format="DD/MM/YYYY")
            end_time = time_col.time_input("End time", key="end_time", step=timedelta(seconds=1))
    with st.container(border=True):
        st.subheader("Entry & targets")
        left, right = st.columns(2, gap="large")
        with left:
            mode_label = st.radio("Entry method", ["Price at signal time", "At a specific price"], horizontal=True)
            mode = "signal" if mode_label == "Price at signal time" else "level"
            entry_text = st.text_input("Entry price", disabled=mode == "signal", placeholder="From Binance at signal time" if mode == "signal" else "e.g. 70000")
            stop_text = st.text_input("Stop loss price", placeholder="e.g. 68000")
        with right:
            targets_text = st.text_area("Take profit prices", height=185, placeholder="72000\n74000\n76000")
            st.caption("Enter one price per line. Use a decimal point or comma, with no thousands separators.")
    with st.container(border=True):
        st.markdown("**Position sizing** · Optional")
        c1, c2 = st.columns(2)
        margin_text = c1.text_input("Margin · USDT", placeholder="e.g. 200")
        leverage_text = c2.text_input("Leverage · ×", placeholder="e.g. 20")
        st.caption("Enter both fields to include gross P&L and balances.")
    submitted = st.button("Analyze signal", type="primary", width="stretch")
    if not submitted:
        return
    try:
        start_ms = int(datetime.combine(start_date, start_time, TZ).timestamp() * 1000)
        end_ms = int(datetime.combine(end_date, end_time, TZ).timestamp() * 1000)
        targets = tuple(number(line) for line in targets_text.splitlines() if line.strip())
        signal = Signal(
            symbol=usdt_symbol(symbol), side=side, start_ms=start_ms, end_ms=end_ms, mode=mode,
            stop=number(stop_text), targets=targets,
            entry=number(entry_text) if mode == "level" else None,
            margin=number(margin_text) if margin_text.strip() else None,
            leverage=number(leverage_text) if leverage_text.strip() else None,
        )
        signal.validate()
        if end_ms > int(datetime.now(TZ).timestamp() * 1000):
            raise ValueError("The review end cannot be in the future.")
        with st.status("Loading Binance trades…", expanded=True) as status:
            message = st.empty()
            with closing(BinanceData(ROOT / ".cache" / "binance", progress=message.write)) as data:
                reference = data.reference_trade(signal.symbol, start_ms)
                with closing(data.iter_trades(signal.symbol, start_ms + 1, end_ms)) as trades:
                    result = simulate(signal, reference, trades)
                sources = set(data.sources)
            status.update(label="Analysis complete", state="complete", expanded=False)
        show_result(result, signal, sources)
    except (ValueError, BinanceError, OSError) as exc:
        st.error(str(exc))
        st.caption("Could not finish the analysis. Check your inputs and connection, then try again.")


if __name__ == "__main__":
    main()
