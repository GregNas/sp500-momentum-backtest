"""Curated ETF universes for momentum backtests.

Hardcoded constants — no I/O. Country/sector labels live in `group` and are
exposed via the same `sector` key the existing dashboard uses, so the sector-
breakdown panel rebrands automatically.
"""

from __future__ import annotations


GLOBAL_EQUITY_ETFS: dict[str, dict] = {
    # Developed single-country (iShares MSCI)
    "EWA":  {"name": "iShares MSCI Australia",      "group": "Australia",       "type": "developed"},
    "EWO":  {"name": "iShares MSCI Austria",        "group": "Austria",         "type": "developed"},
    "EWK":  {"name": "iShares MSCI Belgium",        "group": "Belgium",         "type": "developed"},
    "EWC":  {"name": "iShares MSCI Canada",         "group": "Canada",          "type": "developed"},
    "EWQ":  {"name": "iShares MSCI France",         "group": "France",          "type": "developed"},
    "EWG":  {"name": "iShares MSCI Germany",        "group": "Germany",         "type": "developed"},
    "EWH":  {"name": "iShares MSCI Hong Kong",      "group": "Hong Kong",       "type": "developed"},
    "EWI":  {"name": "iShares MSCI Italy",          "group": "Italy",           "type": "developed"},
    "EWJ":  {"name": "iShares MSCI Japan",          "group": "Japan",           "type": "developed"},
    "EWN":  {"name": "iShares MSCI Netherlands",    "group": "Netherlands",     "type": "developed"},
    "EWS":  {"name": "iShares MSCI Singapore",      "group": "Singapore",       "type": "developed"},
    "EWP":  {"name": "iShares MSCI Spain",          "group": "Spain",           "type": "developed"},
    "EWD":  {"name": "iShares MSCI Sweden",         "group": "Sweden",          "type": "developed"},
    "EWL":  {"name": "iShares MSCI Switzerland",    "group": "Switzerland",     "type": "developed"},
    "EWU":  {"name": "iShares MSCI United Kingdom", "group": "United Kingdom",  "type": "developed"},
    "EIS":  {"name": "iShares MSCI Israel",         "group": "Israel",          "type": "developed"},
    # Emerging single-country
    "EWZ":  {"name": "iShares MSCI Brazil",         "group": "Brazil",          "type": "emerging"},
    "MCHI": {"name": "iShares MSCI China",          "group": "China",           "type": "emerging"},
    "INDA": {"name": "iShares MSCI India",          "group": "India",           "type": "emerging"},
    "EIDO": {"name": "iShares MSCI Indonesia",      "group": "Indonesia",       "type": "emerging"},
    "EWW":  {"name": "iShares MSCI Mexico",         "group": "Mexico",          "type": "emerging"},
    "EPHE": {"name": "iShares MSCI Philippines",    "group": "Philippines",     "type": "emerging"},
    "EPOL": {"name": "iShares MSCI Poland",         "group": "Poland",          "type": "emerging"},
    "EZA":  {"name": "iShares MSCI South Africa",   "group": "South Africa",    "type": "emerging"},
    "EWY":  {"name": "iShares MSCI South Korea",    "group": "South Korea",     "type": "emerging"},
    "EWT":  {"name": "iShares MSCI Taiwan",         "group": "Taiwan",          "type": "emerging"},
    "THD":  {"name": "iShares MSCI Thailand",       "group": "Thailand",        "type": "emerging"},
    "TUR":  {"name": "iShares MSCI Turkey",         "group": "Turkey",          "type": "emerging"},
    # Regional / style baskets
    "EEM":  {"name": "iShares MSCI Emerging Markets",     "group": "Emerging Markets",     "type": "regional"},
    "VWO":  {"name": "Vanguard FTSE Emerging Markets",    "group": "Emerging Markets",     "type": "regional"},
    "EFA":  {"name": "iShares MSCI EAFE",                  "group": "Developed ex-US",     "type": "regional"},
    "VEA":  {"name": "Vanguard Developed Markets",         "group": "Developed ex-US",     "type": "regional"},
    "VGK":  {"name": "Vanguard FTSE Europe",               "group": "Europe",              "type": "regional"},
    "EWX":  {"name": "SPDR S&P EM Small Cap",              "group": "EM Small-Cap",        "type": "regional"},
    "FM":   {"name": "iShares Frontier & Select EM",       "group": "Frontier",            "type": "regional"},
    "AAXJ": {"name": "iShares MSCI All Country Asia ex-Japan", "group": "Asia ex-Japan",   "type": "regional"},
    "ILF":  {"name": "iShares Latin America 40",           "group": "Latin America",       "type": "regional"},
}


US_SECTOR_ETFS: dict[str, dict] = {
    "XLK":  {"name": "Technology Select Sector SPDR",          "group": "Technology"},
    "XLF":  {"name": "Financial Select Sector SPDR",           "group": "Financials"},
    "XLE":  {"name": "Energy Select Sector SPDR",              "group": "Energy"},
    "XLV":  {"name": "Health Care Select Sector SPDR",         "group": "Health Care"},
    "XLY":  {"name": "Consumer Discretionary Select SPDR",     "group": "Consumer Discretionary"},
    "XLP":  {"name": "Consumer Staples Select Sector SPDR",    "group": "Consumer Staples"},
    "XLI":  {"name": "Industrial Select Sector SPDR",          "group": "Industrials"},
    "XLB":  {"name": "Materials Select Sector SPDR",           "group": "Materials"},
    "XLU":  {"name": "Utilities Select Sector SPDR",           "group": "Utilities"},
    "XLC":  {"name": "Communication Services Select SPDR",     "group": "Communication Services"},
    "XLRE": {"name": "Real Estate Select Sector SPDR",         "group": "Real Estate"},
}


DEFAULT_BENCHMARK: dict[str, str] = {
    "sp500":          "SPY",
    "global_etfs":    "ACWI",
    "us_sector_etfs": "SPY",
}


META_LABEL: dict[str, str] = {
    "sp500":          "Sector",
    "global_etfs":    "Country / Region",
    "us_sector_etfs": "Sector",
}


UNIVERSE_LABEL: dict[str, str] = {
    "sp500":          "S&P 500 single names",
    "global_etfs":    "Global equity ETFs",
    "us_sector_etfs": "US sector ETFs",
}


def _expose(table: dict[str, dict]) -> tuple[list[str], dict[str, dict]]:
    return list(table.keys()), {
        t: {"name": v["name"], "sector": v["group"]}
        for t, v in table.items()
    }


def get_global_etfs() -> tuple[list[str], dict[str, dict]]:
    return _expose(GLOBAL_EQUITY_ETFS)


def get_us_sector_etfs() -> tuple[list[str], dict[str, dict]]:
    return _expose(US_SECTOR_ETFS)
