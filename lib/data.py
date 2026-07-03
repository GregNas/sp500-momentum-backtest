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


def _export_macos_certs(bundle: Path) -> bool:
    """Export macOS keychain certs to a PEM bundle. Returns True on success."""
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
            return False  # `security` unavailable; let caller see the SSL error
    if not chunks:
        return False
    bundle.parent.mkdir(exist_ok=True)
    bundle.write_text("".join(chunks))
    return True


def _export_windows_certs(bundle: Path) -> bool:
    """Export the Windows certificate store (ROOT + CA) to a PEM bundle.
    Returns True on success."""
    import ssl

    if not hasattr(ssl, "enum_certificates"):  # non-Windows builds
        return False
    chunks: list[str] = []
    for store in ("ROOT", "CA"):
        try:
            certs = ssl.enum_certificates(store)
        except OSError:
            continue
        for cert, encoding, _trust in certs:
            if encoding == "x509_asn":
                chunks.append(ssl.DER_cert_to_PEM_cert(cert))
    if not chunks:
        return False
    bundle.parent.mkdir(exist_ok=True)
    bundle.write_text("".join(chunks))
    return True


def _ensure_ssl_trust() -> None:
    """In corporate networks with TLS-inspection proxies, Python's bundled CA
    list may not trust the proxy's root. Export the OS trust store (macOS
    keychain / Windows cert store) to a PEM bundle and point requests + urllib
    + curl at it, once per process.

    No-op on Linux or if SSL_CERT_FILE / REQUESTS_CA_BUNDLE is already set.
    """
    if os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE"):
        return

    system = platform.system()
    if system == "Darwin":
        bundle = Path("./cache/macos_ca_bundle.pem").resolve()
        if not bundle.exists() and not _export_macos_certs(bundle):
            return
    elif system == "Windows":
        bundle = Path("./cache/windows_ca_bundle.pem").resolve()
        if not bundle.exists() and not _export_windows_certs(bundle):
            return
    else:
        return

    os.environ["REQUESTS_CA_BUNDLE"] = str(bundle)
    os.environ["SSL_CERT_FILE"] = str(bundle)
    # curl_cffi (used by newer yfinance) follows curl's convention.
    os.environ.setdefault("CURL_CA_BUNDLE", str(bundle))


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
    """Daily prices -> monthly returns (last close of each month).

    ``fill_method=None`` so a month with no price stays NaN instead of being
    forward-filled to a fake 0% return (which would keep a delisted/halted name
    eligible for ranking on a stale price).
    """
    monthly = prices.resample("ME").last()
    return monthly.pct_change(fill_method=None).dropna(how="all")
