import hashlib
import io
import zipfile
from decimal import Decimal as D

import pytest

from binance import API, DAY_MS, HOUR_MS, BinanceData, BinanceError, read_archive, validate_symbol

B = 1_704_067_200_000  # 2024-01-01 UTC


class Response:
    def __init__(self, data=None, status=200, content=b"", text="", headers=None):
        self.data, self.status_code, self.content, self.text = data, status, content, text
        self.headers = headers or {}

    def json(self):
        return self.data

    def close(self):
        pass

    def iter_content(self, chunk_size):
        yield self.content


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        assert self.responses, "Unexpected network request"
        return self.responses.pop(0)

    def close(self):
        pass


def item(i, timestamp=B, price="100"):
    return {"a": i, "T": timestamp, "p": price}


def client(tmp_path, session, now=B + HOUR_MS):
    return BinanceData(tmp_path, session=session, now_ms=now, request_interval=0)


def zipped(csv):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("sample.csv", csv)
    return buffer.getvalue()


@pytest.mark.parametrize("header", ["", "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n"])
def test_archive_with_or_without_header(tmp_path, header):
    path = tmp_path / "sample.zip"
    path.write_bytes(zipped(header + f"1,100.01,2,1,2,{B},true\n2,101,1,3,3,{B},false\n"))
    result = list(read_archive(path))
    assert [r.id for r in result] == [1, 2]
    assert result[0].price == D("100.01")


def test_archive_column_names_tolerate_extra_column(tmp_path):
    path = tmp_path / "sample.zip"
    path.write_bytes(zipped("agg_trade_id,price,quantity,normal_quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n"
                            f"1,100,1,1,10,11,{B},true\n"))
    assert list(read_archive(path))[0].time_ms == B


def test_pagination_never_skips_equal_timestamps(tmp_path):
    first = [item(i) for i in range(1, 1001)]
    second = [item(1001), item(1002, B + 1)]
    session = Session(Response(first), Response(second))
    data = client(tmp_path, session)
    records = list(data._rest("BTCUSDT", B, B))
    assert len(records) == 1001
    assert session.calls[1][1]["params"] == {"symbol": "BTCUSDT", "limit": 1000, "fromId": 1001}


def test_empty_time_window_advances_less_than_hour_windows(tmp_path):
    session = Session(Response([]), Response([item(1, B + HOUR_MS)]), Response([]))
    records = list(client(tmp_path, session, B + 2 * HOUR_MS)._rest("BTCUSDT", B, B + HOUR_MS))
    assert len(records) == 1
    for _, request in session.calls[:2]:
        params = request["params"]
        assert params["endTime"] - params["startTime"] < HOUR_MS


def test_api_gap_detected(tmp_path):
    session = Session(Response([item(1)]), Response([item(3, B + 1)]))
    with pytest.raises(match="missing"):
        list(client(tmp_path, session)._rest("BTCUSDT", B, B + 10))


def test_api_intra_page_order_detected(tmp_path):
    session = Session(Response([item(1, B + 1), item(2, B)]))
    with pytest.raises(ValueError):
        list(client(tmp_path, session)._rest("BTCUSDT", B, B + 10))


def test_api_48_hour_limit_is_explicit(tmp_path):
    session = Session()
    with pytest.raises(BinanceError, match="48 hour"):
        list(client(tmp_path, session, B + 3 * DAY_MS)._rest("BTCUSDT", B, B + 10))
    assert not session.calls


def test_archive_download_checksum_and_cache(tmp_path):
    content = zipped(f"1,100,1,1,1,{B},true\n")
    digest = hashlib.sha256(content).hexdigest()
    session = Session(Response(text=digest + " file.zip"), Response(content=content))
    data = client(tmp_path, session, B + 4 * DAY_MS)
    path = data._archive("BTCUSDT", "2024-01-01")
    assert path.read_bytes() == content
    assert data._archive("BTCUSDT", "2024-01-01") == path
    assert len(session.calls) == 2
    session2 = Session(Response(text=digest + " file.zip"))
    assert client(tmp_path, session2)._archive("BTCUSDT", "2024-01-01") == path
    assert len(session2.calls) == 1


def test_bad_checksum_does_not_cache_partial_file(tmp_path):
    session = Session(Response(text="0" * 64), Response(content=b"broken"))
    with pytest.raises(match="SHA-256"):
        client(tmp_path, session)._archive("BTCUSDT", "2024-01-01")
    assert not list(tmp_path.iterdir())


def test_old_history_uses_only_official_um_archive(tmp_path):
    content = zipped(f"1,100,1,1,1,{B},true\n2,110,1,2,2,{B+1},true\n")
    session = Session(Response(text=hashlib.sha256(content).hexdigest()), Response(content=content))
    data = client(tmp_path, session, B + 100 * DAY_MS)
    records = list(data.iter_trades("BTCUSDT", B, B + 100))
    assert [r.id for r in records] == [1, 2]
    assert all(url.startswith("https://data.binance.vision/data/futures/um/") for url, _ in session.calls)


def test_missing_old_archive_fails_without_fabricated_result(tmp_path):
    session = Session(Response(status=404))
    with pytest.raises(BinanceError, match="48 hour"):
        list(client(tmp_path, session, B + 100 * DAY_MS).iter_trades("BTCUSDT", B, B + 1))


def test_missing_recent_archive_falls_back_to_official_api(tmp_path):
    session = Session(Response(status=404), Response([item(1), item(2, B + 2)]))
    records = list(client(tmp_path, session, B + DAY_MS + HOUR_MS).iter_trades("BTCUSDT", B, B + 1))
    assert len(records) == 1
    assert session.calls[1][0] == API


def test_reference_is_last_at_or_before_signal_not_next_price(tmp_path):
    session = Session(Response([item(1, B + 5), item(2, B + 5), item(3, B + 6)]))
    reference = client(tmp_path, session).reference_trade("BTCUSDT", B + 5)
    assert reference.id == 2


def test_reference_at_midnight_reads_previous_day_when_needed(tmp_path):
    content = zipped(f"10,100,1,10,10,{B-1},true\n")
    session = Session(Response([]), Response(text=hashlib.sha256(content).hexdigest()),
                      Response(content=content), Response([]))
    reference = client(tmp_path, session).reference_trade("BTCUSDT", B)
    assert reference.id == 10
    assert reference.time_ms == B - 1


def test_yesterday_reference_limits_rest_scan_when_archive_is_delayed(tmp_path):
    start = B + 12 * HOUR_MS
    session = Session(Response(status=404), Response([item(10, start - 1), item(11, start + 1)]))
    reference = client(tmp_path, session, B + DAY_MS + HOUR_MS).reference_trade("BTCUSDT", start)
    assert reference.id == 10
    assert session.calls[1][1]["params"]["startTime"] == start - 60_000


@pytest.mark.parametrize("status,match", [(429, "rate limit"), (418, "rate limit"), (451, "denied access"), (403, "denied access"), (400, "failed")])
def test_http_errors_are_explained(tmp_path, status, match):
    session = Session(Response(status=status))
    with pytest.raises(BinanceError, match=match):
        client(tmp_path, session)._api_page("BTCUSDT", startTime=B, endTime=B + 1)


def test_corrupted_archive_and_invalid_timestamp(tmp_path):
    path = tmp_path / "bad.zip"
    path.write_bytes(b"not a zip")
    with pytest.raises(ValueError):
        list(read_archive(path))
    path.write_bytes(zipped("1,100,1,1,1,1704067200000000,true\n"))
    with pytest.raises(ValueError):
        list(read_archive(path))


def test_archive_gap_is_not_silently_ignored(tmp_path):
    content = zipped(f"1,100,1,1,1,{B},true\n3,101,1,3,3,{B+1},true\n")
    session = Session(Response(text=hashlib.sha256(content).hexdigest()), Response(content=content))
    with pytest.raises(ValueError):
        list(client(tmp_path, session, B + 3 * DAY_MS).iter_trades("BTCUSDT", B, B + 10))


def test_future_range_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="future"):
        list(client(tmp_path, Session()).iter_trades("BTCUSDT", B, B + DAY_MS))


@pytest.mark.parametrize("symbol", ["../BTCUSDT", "BTC/USDT", "https://example.com", ""])
def test_symbol_cannot_change_archive_path(symbol):
    with pytest.raises(ValueError):
        validate_symbol(symbol)
