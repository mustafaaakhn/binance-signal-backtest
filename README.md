# Binance Signal Analysis

A small Python app that checks a futures trading signal using Binance trade history. The page is built with Streamlit.

Enter a coin, direction, signal time, entry price, stop loss and take profit prices. The app shows which prices were reached and how long it took. You can also enter margin and leverage to see profit or loss in USDT.

## Run

You need Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

On Windows, activate the environment with `.venv\Scripts\activate` instead. On macOS or Linux, you can also use `bash run.sh`.

## Notes

- Enter only the coin symbol, like BTC or SOL. USDT is fixed.
- All dates and times use Istanbul time (UTC+3). Click Now to update the end time.
- Enter one TP price per line. Use a decimal point or comma, without thousands separators.
- The whole trade closes at the furthest TP or SL, whichever comes first. There are no partial exits.
- Each TP row also shows what the result would be if the whole trade closed there. These results should not be added together.
- Starting balance is the margin you entered. Ending balance is margin plus profit or loss. Fees, funding, slippage and liquidation are not included.
- The app uses [Binance trade data](https://github.com/binance/binance-public-data) and the Binance futures API. It does not need an API key or place orders.
- Recent trades come from the API; older trades use daily archives. Downloaded archives stay in `.cache/`. If data is missing or Binance blocks access, the app shows an error.

## Files

- `app.py`: the page and number formatting
- `engine.py`: entry, TP, SL and balance calculations
- `binance.py`: fetching and reading Binance data
- `tests/`: checks for the calculations, data reader and form

Run the tests with:

```bash
python -m pytest -q
```
