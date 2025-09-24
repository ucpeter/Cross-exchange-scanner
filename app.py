# full_scanner_patched.py
import time, re, ccxt, json, os
import pandas as pd
import streamlit as st

# ------------------- Page Config -------------------
st.set_page_config(page_title="Cross-Exchange Arbitrage Scanner", layout="wide")

# ------------------- Custom Dark Theme -------------------
st.markdown("""
    <style>
    body, .stApp { background-color: #111111; color: #E0E0E0; }
    .stSidebar { background-color: #1A1A1A !important; }
    .stDataFrame th { background-color: #222 !important; color: #EEE !important; font-weight: 600; }
    .stDataFrame td { color: #EEE !important; }
    .stDataFrame tbody tr:nth-child(even) { background-color: #1E1E1E !important; }
    .stDataFrame tbody tr:hover { background-color: #2A2A2A !important; }
    .good { color: #4CAF50; font-weight: 600; }
    .bad { color: #FF5252; font-weight: 600; }
    .spread { color: #42A5F5; font-weight: 600; }
    .stButton>button {
        background-color: #1976D2; color: white; border-radius: 8px;
        padding: 0.6em 1.2em; font-size: 16px; font-weight: 600;
        border: none; cursor: pointer;
        transition: background-color 0.3s ease;
    }
    .stButton>button:hover { background-color: #1565C0; }
    </style>
""", unsafe_allow_html=True)

st.title("🌍 Cross-Exchange Arbitrage Scanner")

# ------------------- Settings Persistence -------------------
SETTINGS_FILE = "settings.json"

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)

saved = load_settings()

# ------------------- Exchange List -------------------
TOP_20_CCXT_EXCHANGES = [
    "binance", "okx", "coinbase", "kraken", "bybit", "kucoin",
    "mexc", "bitfinex", "bitget", "gateio", "crypto_com",
    "upbit", "whitebit", "poloniex", "bingx", "lbank",
    "bitstamp", "gemini", "bitrue", "xt",
]

EXCHANGE_NAMES = {
    "binance": "Binance", "okx": "OKX", "coinbase": "Coinbase",
    "kraken": "Kraken", "bybit": "Bybit", "kucoin": "KuCoin",
    "mexc": "MEXC", "bitfinex": "Bitfinex", "bitget": "Bitget",
    "gateio": "Gate.io", "crypto_com": "Crypto.com", "upbit": "Upbit",
    "whitebit": "WhiteBIT", "poloniex": "Poloniex", "bingx": "BingX",
    "lbank": "LBank", "bitstamp": "Bitstamp", "gemini": "Gemini",
    "bitrue": "Bitrue", "xt": "XT",
}

EXTRA_OPTS = {
    "bybit": {"options": {"defaultType": "spot"}},
    "okx": {"options": {"defaultType": "spot"}},
    "bingx": {"options": {"defaultType": "spot"}},
    "mexc": {"options": {"defaultType": "spot"}},
    "bitrue": {"options": {"defaultType": "spot"}},
    "xt": {"options": {"defaultType": "spot"}},
}

USD_QUOTES = {"USDT", "USD", "USDC", "BUSD"}

LOW_FEE_CHAIN_PRIORITY = ["TRC20", "BEP20", "BSC", "SOL", "MATIC", "ARB", "OP", "Polygon", "TON", "AVAX", "ETH"]

LEV_PATTERNS = [r"\b\d+[LS]\b", r"\bUP\b", r"\bDOWN\b", r"\bBULL\b", r"\bBEAR\b"]
LEV_REGEX = re.compile("|".join(LEV_PATTERNS), re.IGNORECASE)

# ------------------- Sidebar -------------------
st.sidebar.header("Scanner Controls")

buy_exchanges = st.sidebar.multiselect(
    "Buy Exchanges (up to 5)",
    TOP_20_CCXT_EXCHANGES,
    default=saved.get("buy_exchanges", []),
    max_selections=5,
    format_func=lambda x: EXCHANGE_NAMES[x],
)

sell_exchanges = st.sidebar.multiselect(
    "Sell Exchanges (up to 5)",
    TOP_20_CCXT_EXCHANGES,
    default=saved.get("sell_exchanges", []),
    max_selections=5,
    format_func=lambda x: EXCHANGE_NAMES[x],
)

min_profit = st.sidebar.number_input(
    "Minimum Profit % (after fees)", 0.0, 100.0,
    saved.get("min_profit", 1.0), 0.1
)

max_profit = st.sidebar.number_input(
    "Maximum Profit % (after fees)", 0.0, 200.0,
    saved.get("max_profit", 20.0), 0.1
)

min_24h_vol_usd = st.sidebar.number_input(
    "Min 24h Volume (USD)", 0.0, 1_000_000_000.0,
    saved.get("min_24h_vol_usd", 100000.0), 50000.0
)

exclude_chains = st.sidebar.multiselect(
    "Exclude Blockchains",
    ["ETH", "TRC20", "BEP20", "BSC", "SOL", "MATIC", "ARB", "OP", "Polygon", "TON", "AVAX"],
    default=saved.get("exclude_chains", ["ETH"])
)
include_all_chains = st.sidebar.checkbox(
    "Include all blockchains (ignore exclusion)",
    value=saved.get("include_all_chains", False)
)

auto_refresh = st.sidebar.checkbox("🔄 Auto Refresh Every 20s", value=saved.get("auto_refresh", False))
scan_now = st.button("🚀 Scan Now")

# ------------------- Save settings only when Scan Now clicked -------------------
if scan_now:
    save_settings({
        "buy_exchanges": buy_exchanges,
        "sell_exchanges": sell_exchanges,
        "min_profit": min_profit,
        "max_profit": max_profit,
        "min_24h_vol_usd": min_24h_vol_usd,
        "exclude_chains": exclude_chains,
        "include_all_chains": include_all_chains,
        "auto_refresh": auto_refresh,
    })

# ---------- Runtime state ----------
if "op_cache" not in st.session_state:
    st.session_state.op_cache = {}
if "lifetime_history" not in st.session_state:
    st.session_state.lifetime_history = {}
if "last_seen_keys" not in st.session_state:
    st.session_state.last_seen_keys = set()

# ---------- Helpers (PATCHED ONLY) ----------
def parse_symbol(symbol: str):
    # keep original behavior (don't change signature/return shape)
    base, quote = symbol.split("/")[0], symbol.split("/")[1].split(":")[0]
    return base, quote

def market_price_from_ticker(t):
    """
    Robust extraction of a 'market price' from ccxt ticker dict.
    Prefer 'last' (last trade), then common fallback fields, then 'info' keys,
    finally fall back to mid of bid/ask if available. Returns float or None.
    """
    if not t:
        return None

    # 1) prefer standardized 'last'
    for k in ("last", "close", "price", "lastPrice", "tradePrice", "last_traded", "lastTradePrice", "lastPrice24h"):
        # check top-level
        val = t.get(k)
        if val is None:
            # check inside info dict (different exchanges put it here)
            info = t.get("info") or {}
            val = info.get(k)
        if val is not None:
            try:
                return float(val)
            except:
                # sometimes it's nested or stringy; try coerced float
                try:
                    return float(str(val))
                except:
                    pass

    # 2) try average/vwap/weighted fields
    for k in ("average", "vwap", "weightedAverage", "avgPrice"):
        val = t.get(k) or (t.get("info") or {}).get(k)
        if val is not None:
            try:
                return float(val)
            except:
                pass

    # 3) last resort: use bid/ask mid if both present (still preferable to returning None)
    bid, ask = t.get("bid"), t.get("ask")
    if bid is not None and ask is not None:
        try:
            return (float(bid) + float(ask)) / 2.0
        except:
            pass

    return None

# extended list of candidate volume keys (search both ticker and ticker['info'])
INFO_VOLUME_CANDIDATES = [
    "quoteVolume", "quoteVolume24h", "quote_volume_24h", "quote_volume", "quoteVol24h", "quoteVol",
    "baseVolume", "baseVolume24h", "base_volume_24h", "base_volume", "baseVol24h", "baseVol",
    "vol", "vol24h", "volCcy24h", "volValue", "value", "amount", "turnover", "turnover24h",
    "volume", "volumeUsd", "acc_trade_price_24h",
    "q", "Q", "v", "volume24h", "quoteAmount"
]

def safe_usd_volume(ex_id, symbol, ticker, price, all_tickers):
    """
    Estimate 24h USD volume for (ex_id, symbol, ticker).
    Tries:
      - quoteVolume (already USD if quote is USD-like)
      - baseVolume * price
      - common info keys
      - convert quote-volume using a available quote->USDT pair from all_tickers
    Returns float USD amount or 0.0 on failure.
    """
    try:
        base, quote = parse_symbol(symbol)
        q_upper = quote.upper()

        # standard ccxt fields
        qvol = ticker.get("quoteVolume") or ticker.get("quoteVolume24h") or ticker.get("quote_volume")
        bvol = ticker.get("baseVolume") or ticker.get("baseVolume24h") or ticker.get("base_volume")

        # Case A: quote is already USD-like and quoteVolume present
        if q_upper in USD_QUOTES and qvol:
            try:
                return float(qvol)
            except:
                pass

        # Case B: baseVolume * price (when base volume provided)
        if bvol and price:
            try:
                return float(bvol) * float(price)
            except:
                pass

        # Case C: look inside info for many candidate keys
        info = ticker.get("info") or {}
        raw = None
        for key in INFO_VOLUME_CANDIDATES:
            # check ticker top-level then info
            val = ticker.get(key)
            if val is None:
                val = info.get(key)
            if val is None:
                continue
            try:
                fval = float(val)
                if fval > 0:
                    raw = fval
                    break
            except:
                continue

        if raw is not None:
            # if quote is USD-like then raw likely already in USD (quoteVolume-like)
            if q_upper in USD_QUOTES:
                return float(raw)
            # else attempt to convert raw (quote-volume) to USD using available quote->USDT/USDC/BUSD/USD pairs
            for conv_quote in ("USDT", "USDC", "BUSD", "USD"):
                conv_sym = f"{q_upper}/{conv_quote}"
                conv_t = all_tickers.get(conv_sym)
                conv_px = market_price_from_ticker(conv_t)
                if conv_px:
                    return float(raw) * float(conv_px)

        # Case D: if qvol exists but we couldn't confirm, try to convert qvol via a conv_sym
        if qvol:
            for conv_quote in ("USDT", "USDC", "BUSD", "USD"):
                conv_sym = f"{q_upper}/{conv_quote}"
                conv_t = all_tickers.get(conv_sym)
                conv_px = market_price_from_ticker(conv_t)
                if conv_px:
                    try:
                        return float(qvol) * float(conv_px)
                    except:
                        pass

        # final fallback: give up and return 0.0
        return 0.0
    except Exception:
        return 0.0

# ---------- unchanged helpers ----------
def is_ticker_fresh(t, max_age_sec=300):
    ts = t.get("timestamp")
    if ts is None:
        return True
    now = int(time.time() * 1000)
    return (now - int(ts)) <= max_age_sec * 1000

def fmt_usd(x):
    try:
        x = float(x or 0)
        if x >= 1e9: return f"${x/1e9:.2f}B"
        if x >= 1e6: return f"${x/1e6:.2f}M"
        if x >= 1e3: return f"${x/1e3:.0f}K"
        return f"${x:,.0f}"
    except:
        return "$0"

# ---------- original functions preserved below (unchanged) ----------
def symbol_ok(ex_obj, symbol):
    m = ex_obj.markets.get(symbol, {})
    if not m:
        return False
    if not m.get("spot", True):
        return False
    base, quote = parse_symbol(symbol)
    if quote.upper() not in USD_QUOTES:
        return False
    if LEV_REGEX.search(symbol):
        return False
    if m.get("active") is False:
        return False
    return True

def choose_common_chain(ex1, ex2, coin, exclude_chains, include_all_chains):
    try:
        c1 = ex1.currencies.get(coin, {}) or {}
        c2 = ex2.currencies.get(coin, {}) or {}
        nets1 = c1.get("networks", {}) or {}
        nets2 = c2.get("networks", {}) or {}
        common = set(nets1.keys()) & set(nets2.keys())
        if not common:
            return "❌ No chain", "❌", "❌"
        preferred = [n for n in LOW_FEE_CHAIN_PRIORITY if (include_all_chains or n not in exclude_chains)]
        best = None
        for pref in preferred:
            if pref in common:
                best = pref; break
        if not best:
            candidate = sorted(list(common))[0]
            if not include_all_chains and candidate in exclude_chains:
                return "❌ No chain", "❌", "❌"
            best = candidate
        w_ok = "✅" if nets1.get(best, {}).get("withdraw") else "❌"
        d_ok = "✅" if nets2.get(best, {}).get("deposit") else "❌"
        return best, w_ok, d_ok
    except:
        return "❌ Unknown", "❌", "❌"

# ------------------- Main Scan -------------------
def run_scan():
    if not buy_exchanges or not sell_exchanges:
        st.warning("⚠️ Please select at least one buy and one sell exchange")
        return pd.DataFrame()

    results = []
    for buy_id in buy_exchanges:
        try:
            buy_ex = getattr(ccxt, buy_id)(EXTRA_OPTS.get(buy_id, {}))
            buy_ex.load_markets()
            buy_tickers = buy_ex.fetch_tickers()
        except Exception as e:
            st.error(f"❌ Failed to fetch {buy_id}: {e}")
            continue

        for sell_id in sell_exchanges:
            if buy_id == sell_id:
                continue
            try:
                sell_ex = getattr(ccxt, sell_id)(EXTRA_OPTS.get(sell_id, {}))
                sell_ex.load_markets()
                sell_tickers = sell_ex.fetch_tickers()
            except Exception as e:
                st.error(f"❌ Failed to fetch {sell_id}: {e}")
                continue

            common = set(buy_tickers.keys()) & set(sell_tickers.keys())
            for sym in common:
                if not (symbol_ok(buy_ex, sym) and symbol_ok(sell_ex, sym)):
                    continue

                b_tick = buy_tickers.get(sym)
                s_tick = sell_tickers.get(sym)
                b_price = market_price_from_ticker(b_tick)
                s_price = market_price_from_ticker(s_tick)
                if not (b_price and s_price):
                    continue

                spread = ((s_price - b_price) / b_price) * 100
                if spread < min_profit or spread > max_profit:
                    continue

                b_vol = safe_usd_volume(buy_id, sym, b_tick, b_price, buy_tickers)
                s_vol = safe_usd_volume(sell_id, sym, s_tick, s_price, sell_tickers)
                min_vol = min(b_vol, s_vol)
                if min_vol < min_24h_vol_usd:
                    continue

                base, _ = parse_symbol(sym)
                chain, w_ok, d_ok = choose_common_chain(buy_ex, sell_ex, base, exclude_chains, include_all_chains)
                if chain.startswith("❌"):
                    continue

                results.append({
                    "Pair": sym,
                    "BuyEx": EXCHANGE_NAMES[buy_id],
                    "SellEx": EXCHANGE_NAMES[sell_id],
                    "Buy": round(b_price, 6),
                    "Sell": round(s_price, 6),
                    "Spread%": round(spread, 2),
                    "BuyVol": fmt_usd(b_vol),
                    "SellVol": fmt_usd(s_vol),
                    "Chain": chain,
                    "WithdrawOK": w_ok,
                    "DepositOK": d_ok,
                })

    return pd.DataFrame(results)

# ------------------- Run + Display -------------------
if scan_now or auto_refresh:
    df = run_scan()
    if not df.empty:
        st.dataframe(df, use_container_width=True)
    else:
        st.warning("⚠️ No arbitrage opportunities found")
