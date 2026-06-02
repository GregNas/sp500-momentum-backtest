"""Data layer: ticker list, price downloads (cache-aware), monthly returns."""

from __future__ import annotations

import os
import platform
import subprocess
from io import StringIO
from pathlib import Path
from typing import Callable

import pandas as pd
import requests
import yfinance as yf

from lib import cache


def _ensure_ssl_trust() -> None:
    """On macOS in corporate networks (e.g. with TLS-inspection proxies), Python's
    bundled CA list may not trust the proxy's root. Export the macOS keychain
    certs to a bundle and point requests + urllib at it, once per process.

    No-op on other platforms or if SSL_CERT_FILE / REQUESTS_CA_BUNDLE is already set.
    """
    if platform.system() != "Darwin":
        return
    if os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE"):
        return

    bundle = Path("./cache/macos_ca_bundle.pem").resolve()
    if not bundle.exists():
        bundle.parent.mkdir(exist_ok=True)
        keychains = [
            "/Library/Keychains/System.keychain",
            "/System/Library/Keychains/SystemRootCertificates.keychain",
            str(Path.home() / "Library/Keychains/login.keychain-db"),
        ]
        chunks: list[str] = []
        for kc in keychains:
            if not Path(kc).exists():
                continue
            try:
                out = subprocess.run(
                    ["security", "find-certificate", "-a", "-p", kc],
                    capture_output=True, text=True, timeout=15, check=False,
                )
                if out.returncode == 0 and out.stdout:
                    chunks.append(out.stdout)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return  # `security` unavailable; let caller see the SSL error
        if not chunks:
            return
        bundle.write_text("".join(chunks))

    os.environ["REQUESTS_CA_BUNDLE"] = str(bundle)
    os.environ["SSL_CERT_FILE"] = str(bundle)


_ensure_ssl_trust()


def get_sp500_tickers() -> list[str]:
    """Current S&P 500 tickers from Wikipedia. Cached on disk with 24h TTL."""
    cached = cache.load_tickers()
    if cached is not None:
        return cached

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    resp.raise_for_status()
    table = pd.read_html(StringIO(resp.text))[0]
    tickers = table["Symbol"].astype(str).tolist()
    # Yahoo uses '-' where Wikipedia uses '.' (e.g. BRK.B -> BRK-B)
    tickers = [t.replace(".", "-") for t in tickers]
    cache.save_tickers(tickers)
    return tickers


def get_sp500_metadata() -> dict[str, dict]:
    """Per-ticker company name and GICS sector from Wikipedia. 24h disk cache.

    Returns: {"AAPL": {"name": "Apple Inc.", "sector": "Information Technology"}, ...}
    """
    cached = cache.load_metadata()
    if cached is not None:
        return cached

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    resp.raise_for_status()
    table = pd.read_html(StringIO(resp.text))[0]

    metadata: dict[str, dict] = {}
    for _, row in table.iterrows():
        symbol = str(row["Symbol"]).replace(".", "-")
        metadata[symbol] = {
            "name": str(row.get("Security", "")),
            "sector": str(row.get("GICS Sector", "")),
        }
    cache.save_metadata(metadata)
    return metadata


def _fetch_prices(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """Raw yfinance download. Adjusted close, daily."""
    data = yf.download(
        tickers,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    # yfinance returns multi-index columns when given >1 ticker
    if isinstance(data.columns, pd.MultiIndex):
        prices = data["Close"]
    else:
        prices = data[["Close"]].rename(columns={"Close": tickers[0]}) if len(tickers) == 1 else data
    prices = prices.dropna(axis=1, how="all")
    return prices


def get_prices(
    tickers: list[str],
    start: str,
    end: str,
    on_status: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    """Cache-aware price fetcher. Returns DataFrame[date, ticker] of adjusted closes.

    `on_status(msg)` is called with progress strings ("cache hit", "fetching delta...")
    so the UI can surface them.
    """
    return cache.get_prices_cached(tickers, start, end, _fetch_prices, on_status=on_status)


def get_universe(name: str) -> tuple[list[str], dict[str, dict]]:
    """Resolve a universe key to (tickers, metadata).

    Supported keys: "sp500", "global_etfs", "us_sector_etfs".
    """
    if name == "sp500":
        return get_sp500_tickers(), get_sp500_metadata()
    from lib import universes
    if name == "global_etfs":
        return universes.get_global_etfs()
    if name == "us_sector_etfs":
        return universes.get_us_sector_etfs()
    raise ValueError(f"Unknown universe: {name!r}")


def to_monthly_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Daily prices -> monthly returns (last close of each month)."""
    monthly = prices.resample("ME").last()
    return monthly.pct_change().dropna(how="all")
