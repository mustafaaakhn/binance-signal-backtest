import csv
import hashlib
import io
import re
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from engine import Trade, number

API = "https://fapi.binance.com/fapi/v1/aggTrades"
ARCHIVE = "https://data.binance.vision/data/futures/um/daily/aggTrades"
DAY_MS = 86_400_000
HOUR_MS = 3_600_000


class BinanceError(RuntimeError):
    pass


def validate_symbol(symbol):
    symbol = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,30}(?:_[0-9]{6})?", symbol):
        raise ValueError("Enter a valid Binance USD M symbol (e.g. BTCUSDT).")
    return symbol


def parse_trade(row):
    try:
        trade = Trade(int(row["a"]), int(row["T"]), number(row["p"]))
        if trade.id < 0 or not 0 < trade.time_ms < 100_000_000_000_000:
            raise ValueError("Invalid trade ID or timestamp")
        return trade
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Could not read a Binance trade record; calculation stopped.") from exc


def read_archive(path):
    try:
        with zipfile.ZipFile(path) as archive:
            files = [name for name in archive.namelist() if name.endswith(".csv")]
            if len(files) != 1:
                raise ValueError("Expected one CSV trade file in the archive.")
            with archive.open(files[0]) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig") as stream:
                rows = csv.reader(stream)
                columns = None
                for index, row in enumerate(rows):
                    if not row:
                        continue
                    if index == 0 and row[0].strip() in ("agg_trade_id", "aggregate_trade_id"):
                        columns = {name.strip(): i for i, name in enumerate(row)}
                        continue
                    if columns is not None:
                        id_key = "agg_trade_id" if "agg_trade_id" in columns else "aggregate_trade_id"
                        yield parse_trade({"a": row[columns[id_key]], "p": row[columns["price"]],
                                           "T": row[columns["transact_time"]]})
                    else:
                        yield parse_trade({"a": row[0], "p": row[1], "T": row[5]})
    except (zipfile.BadZipFile, OSError, UnicodeError, csv.Error, IndexError, KeyError) as exc:
        raise ValueError("The Binance archive is corrupt or in an unsupported format.") from exc


class BinanceData:
    def __init__(self, cache_dir, progress=None, session=None, now_ms=None, request_interval=1.05):
        self.cache_dir = Path(cache_dir)
        self.session = session or requests.Session()
        self.progress = progress or (lambda message: None)
        self.now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        self.request_interval = request_interval
        self._last_api_request = 0.0
        self._archives = {}
        self.sources = set()

    def close(self):
        self.session.close()

    def _request(self, url, *, params=None, stream=False, missing_ok=False):
        for attempt in range(3):
            try:
                if url == API:
                    remaining = self.request_interval - (time.monotonic() - self._last_api_request)
                    if remaining > 0:
                        time.sleep(remaining)
                    self._last_api_request = time.monotonic()
                response = self.session.get(url, params=params, timeout=(10, 45), stream=stream)
                code = response.status_code
                if code == 404 and missing_ok:
                    response.close()
                    return None
                if code in (418, 429):
                    retry = response.headers.get("Retry-After", "a few minutes")
                    response.close()
                    raise BinanceError(f"Binance rate limit reached. Wait: {retry}. Please try again later.")
                if code in (403, 451):
                    response.close()
                    raise BinanceError(f"Binance denied access from this network or region (HTTP {code}).")
                if code >= 500 and attempt < 2:
                    response.close()
                    time.sleep(attempt + 1)
                    continue
                if code != 200:
                    detail = response.text[:240]
                    response.close()
                    raise BinanceError(f"Binance data request failed (HTTP {code}): {detail}")
                return response
            except requests.RequestException as exc:
                if attempt == 2:
                    raise BinanceError("Could not connect to Binance. Check your internet connection and try again.") from exc
                time.sleep(attempt + 1)

    def _archive(self, symbol, day):
        key = (symbol, day)
        if key in self._archives:
            return self._archives[key]
        name = f"{symbol}-aggTrades-{day}.zip"
        url = f"{ARCHIVE}/{symbol}/{name}"
        self.progress(f"{day} · Checking the official Binance archive…")
        checksum = self._request(url + ".CHECKSUM", missing_ok=True)
        if checksum is None:
            self._archives[key] = None
            return None
        try:
            parts = checksum.text.split()
            expected = parts[0].lower() if parts else ""
        finally:
            checksum.close()
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("Invalid Binance archive checksum file.")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        destination = self.cache_dir / name
        if destination.exists():
            with destination.open("rb") as source:
                valid = hashlib.file_digest(source, "sha256").hexdigest() == expected
            if valid:
                self._archives[key] = destination
                self.sources.add(url)
                return destination
        response = self._request(url, stream=True, missing_ok=True)
        if response is None:
            self._archives[key] = None
            return None
        temporary = None
        try:
            digest = hashlib.sha256()
            size = 0
            with tempfile.NamedTemporaryFile(dir=self.cache_dir, suffix=".part", delete=False) as target:
                temporary = Path(target.name)
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    target.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    self.progress(f"{day} · Downloading the official archive: {size / 1_048_576:.1f} MB")
            if digest.hexdigest() != expected:
                raise ValueError("Archive SHA-256 verification failed. Please try again.")
            temporary.replace(destination)
        except requests.RequestException as exc:
            raise BinanceError("Archive download was interrupted; please try again.") from exc
        finally:
            response.close()
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self._archives[key] = destination
        self.sources.add(url)
        return destination

    def _api_page(self, symbol, **params):
        response = self._request(API, params={"symbol": symbol, "limit": 1000, **params})
        try:
            data = response.json()
        except ValueError as exc:
            raise BinanceError("Binance returned an invalid response.") from exc
        finally:
            response.close()
        if not isinstance(data, list):
            raise BinanceError("Binance did not return a trade list.")
        self.sources.add(API)
        return [parse_trade(row) for row in data]

    def _rest(self, symbol, start_ms, end_ms):
        if start_ms < self.now_ms - 48 * HOUR_MS:
            raise BinanceError("This date is outside the Binance API 48 hour window and no official archive was found. "
                               "Check the symbol and date; no result was produced from incomplete data.")
        cursor = start_ms
        previous = None
        while cursor <= end_ms:
            if previous is None:
                window_end = min(end_ms, cursor + HOUR_MS - 1)
                page = self._api_page(symbol, startTime=cursor, endTime=window_end)
                if not page:
                    cursor = window_end + 1
                    continue
            else:
                # IDs avoid skipping trades with the same timestamp.
                page = self._api_page(symbol, fromId=previous.id + 1)
                if not page:
                    return
            for trade in page:
                if trade.time_ms < start_ms:
                    raise ValueError("Binance returned a record outside the requested time range.")
                if previous is not None and (trade.id != previous.id + 1 or trade.time_ms < previous.time_ms):
                    raise ValueError("Trades are missing or out of order between Binance API pages.")
                if trade.time_ms > end_ms:
                    return
                yield trade
                previous = trade
            self.progress("Reviewing Binance trades (Istanbul): "
                          + datetime.fromtimestamp(previous.time_ms / 1000, ZoneInfo("Europe/Istanbul")).strftime("%d %b %Y, %H:%M:%S"))

    def _day_trades(self, symbol, start_ms, end_ms):
        day_start = start_ms // DAY_MS * DAY_MS
        if day_start < self.now_ms // DAY_MS * DAY_MS:
            day = datetime.fromtimestamp(day_start / 1000, timezone.utc).date().isoformat()
            path = self._archive(symbol, day)
            if path is not None:
                previous = None
                for count, trade in enumerate(read_archive(path)):
                    if not day_start <= trade.time_ms < day_start + DAY_MS:
                        raise ValueError("An archive trade falls outside the expected day.")
                    if previous is not None and (trade.id != previous.id + 1 or trade.time_ms < previous.time_ms):
                        raise ValueError("The Binance archive contains missing or out of order trades.")
                    previous = trade
                    if trade.time_ms > end_ms:
                        return
                    if trade.time_ms >= start_ms:
                        yield trade
                    if count % 100_000 == 0:
                        self.progress(f"{day} · Reviewing Binance trade records…")
                return
        yield from self._rest(symbol, start_ms, end_ms)

    def reference_trade(self, symbol, signal_ms):
        symbol = validate_symbol(symbol)
        if signal_ms > self.now_ms:
            raise ValueError("The signal time cannot be in the future.")
        day_start = signal_ms // DAY_MS * DAY_MS
        # Try a short window before scanning the whole day.
        start = max(day_start, signal_ms - 60_000)
        while True:
            last = None
            for trade in self.iter_trades(symbol, start, signal_ms):
                last = trade
            if last is not None:
                return last
            if start > day_start:
                start = day_start
            elif start == day_start:
                start = day_start - DAY_MS
            else:
                raise BinanceError("No price record was found at the signal time or on the previous day. Check the symbol and date.")

    def iter_trades(self, symbol, start_ms, end_ms):
        symbol = validate_symbol(symbol)
        if end_ms > self.now_ms:
            raise ValueError("The review end cannot be in the future.")
        cursor = start_ms
        while cursor <= end_ms:
            day_end = min(end_ms, (cursor // DAY_MS + 1) * DAY_MS - 1)
            yield from self._day_trades(symbol, cursor, day_end)
            cursor = day_end + 1
