# Binance Signal Backtest

A simple app for checking old crypto signals with Binance Futures data.

You can enter a trade manually with:

- Coin
- Long / Short
- Date and time
- Entry price
- Stop Loss
- Take Profit levels
- Margin and leverage

The app checks what happened after the signal and shows which TP levels were reached, whether the Stop Loss was hit, and how long each event took.

It can also calculate an approximate profit or loss based on the margin and leverage entered.

## Example

A signal:

BTCUSDT  
LONG  
Entry: 60000  
TP1: 61000  
TP2: 62000  
SL: 59000

The app checks Binance USD-M Futures historical data and shows the order in which these levels were reached.

## Notes

- This project is for backtesting and checking past trade ideas only.
- It does not place real trades.
- Profit or loss values are approximate and do not include fees, funding or slippage.