"""
Market symbol definitions for the /markets page.
Single source of truth — imported by the Flask route and Jinja2 template.
"""

MAJOR_INDICES = [
    {"label": "S&P 500",                  "symbol": "SP:SPX"},
    {"label": "US Composite (Nasdaq)",    "symbol": "TVC:IXIC"},
    {"label": "Dow Jones",                "symbol": "DJ:DJI"},
    {"label": "CBOE VIX",                 "symbol": "CBOE:VIX"},
    {"label": "S&P/TSX Composite",        "symbol": "TSX:TSX"},
    {"label": "UK 100 (FTSE)",            "symbol": "TVC:UKX"},
    {"label": "DAX (Germany)",            "symbol": "XETR:DAX"},
    {"label": "CAC 40 (France)",          "symbol": "EURONEXT:PX1"},
    {"label": "FTSE MIB (Italy)",         "symbol": "TVC:FTMIB"},
    {"label": "Nikkei 225 (Japan)",       "symbol": "TVC:NI225"},
    {"label": "KOSPI (South Korea)",      "symbol": "TVC:KOSPI"},
    {"label": "SSE Composite (China)",    "symbol": "SSE:000001"},
    {"label": "Shenzhen Component",       "symbol": "SZSE:399001"},
    {"label": "ASX 200 (Australia)",      "symbol": "ASX:XJO"},
    {"label": "IDX Composite (Indonesia)","symbol": "IDX:COMPOSITE"},
    {"label": "STOXX 50 (Europe)",        "symbol": "TVC:SX5E"},
    {"label": "BIST 100 (Turkey)",        "symbol": "BIST:XU100"},
    {"label": "South Africa Top 40",      "symbol": "TVC:SA40"},
    {"label": "Nifty 50 (India)",         "symbol": "NSE:NIFTY"},
]

SP_SECTORS = [
    {"label": "Consumer Discretionary",  "symbol": "SP:S5COND"},
    {"label": "Consumer Staples",        "symbol": "SP:S5CONS"},
    {"label": "Health Care",             "symbol": "SP:S5HLTH"},
    {"label": "Industrials",             "symbol": "SP:S5INDU"},
    {"label": "Information Technology",  "symbol": "SP:S5INFT"},
    {"label": "Materials",               "symbol": "SP:S5MATR"},
    {"label": "Real Estate",             "symbol": "SP:S5REAS"},
    {"label": "Communication Services",  "symbol": "SP:S5TELS"},
    {"label": "Utilities",               "symbol": "SP:S5UTIL"},
    {"label": "Financials",              "symbol": "SP:SPF"},
    {"label": "Energy",                  "symbol": "SP:SPN"},
]

CURRENCIES = [
    {"label": "US Dollar Index (DXY)",         "symbol": "TVC:DXY"},
    {"label": "Euro Index (EXY)",              "symbol": "TVC:EXY"},
    {"label": "British Pound Index (BXY)",     "symbol": "TVC:BXY"},
    {"label": "Swiss Franc Index (SXY)",       "symbol": "TVC:SXY"},
    {"label": "Japanese Yen Index (JXY)",      "symbol": "TVC:JXY"},
    {"label": "Canadian Dollar Index (CXY)",   "symbol": "TVC:CXY"},
    {"label": "Australian Dollar Index (AXY)", "symbol": "TVC:AXY"},
    {"label": "NZ Dollar Index (ZXY)",         "symbol": "TVC:ZXY"},
]

CRYPTO_TOP_10 = [
    {"label": "Bitcoin (BTC)",    "symbol": "BINANCE:BTCUSDT"},
    {"label": "Ethereum (ETH)",   "symbol": "BINANCE:ETHUSDT"},
    {"label": "Tether (USDT)",    "symbol": "BINANCE:USDTUSD"},
    {"label": "BNB",              "symbol": "BINANCE:BNBUSDT"},
    {"label": "Solana (SOL)",     "symbol": "BINANCE:SOLUSDT"},
    {"label": "XRP",              "symbol": "BINANCE:XRPUSDT"},
    {"label": "USD Coin (USDC)",  "symbol": "BINANCE:USDCUSD"},
    {"label": "Dogecoin (DOGE)",  "symbol": "BINANCE:DOGEUSDT"},
    {"label": "Cardano (ADA)",    "symbol": "BINANCE:ADAUSDT"},
    {"label": "TRON (TRX)",       "symbol": "BINANCE:TRXUSDT"},
]
