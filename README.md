# Binance Signal Backtest

A small project I made to check old crypto signals with Binance Futures data.

## Why I made this

One day I was scrolling through Instagram and saw a crypto signal.

Later, the person who shared it said the trade made money. I was curious, so I wanted to check if it was really true.

At first, I copied the signal and gave it to ChatGPT or Claude and asked them to check what happened.

It worked, but doing this again and again took some time.

So I thought it would be easier to make a small app for it.

You just enter the signal, and the app checks what happened after it was posted.

## What it does

You can enter:

* Coin
* Long or Short
* Date and time
* Entry price
* Stop Loss
* Take Profit levels
* Margin
* Leverage

The app checks old Binance Futures price data and shows what happened after the signal.

For example, it can tell you:

* If TP1 was reached
* If TP2 or TP3 was reached
* If the Stop Loss was hit
* What happened first
* How long it took
* About how much profit or loss the trade would make

For example:

```text
BTCUSDT
LONG
Entry: 60000
TP1: 61000
TP2: 62000
SL: 59000
```

The app checks the price after that time and shows the result.

## Why I use it

Before this, I had to open charts or ask ChatGPT or Claude every time I wanted to check a signal.

Now I can just enter it here and see the result much faster.

I mainly made this for myself, but it was also a nice project to practice coding and working with real data.

## Notes

* This project only checks old signals.
* It does not predict prices.
* It does not give trading signals.
* It does not open real trades.
* Profit and loss numbers are only estimates.
* Fees, funding and slippage are not included.
