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
TOP_CCXT_EXCHANGES = [
    "binance", "okx", "coinbaseexchange", "kraken", "bybit", "kucoin",
    "mexc", "bitfinex", "bitget", "gateio", "cryptocom",
    "upbit", "whitebit", "poloniex", "bingx", "lbank",
    "bitstamp", "gemini", "bitrue", "xtcom", "bitmart", "htx",
]

EXCHANGE_NAMES = {
    "binance": "Binance", "okx": "OKX", "coinbaseexchange": "Coinbase",
    "kraken": "Kraken", "bybit": "Bybit", "kucoin": "KuCoin",
    "mexc": "MEXC", "bitfinex": "Bitfinex", "bitget": "Bitget",
    "gateio": "Gate.io", "cryptocom": "Crypto.com", "upbit": "Upbit",
    "whitebit": "WhiteBIT", "poloniex": "Poloniex", "bingx": "BingX",
    "lbank": "LBank", "bitstamp": "Bitstamp", "gemini": "Gemini",
    "bitrue": "Bitrue", "xtcom": "XT.com", "bitmart": "BitMart", "htx": "HTX",
}

EXTRA_OPTS = {
    "bybit": {"options": {"defaultType": "spot"}},
    "okx": {"options": {"defaultType": "spot"}},
    "bingx": {"options": {"defaultType": "spot"}},
    "mexc": {"options": {"defaultType": "spot"}},
    "bitrue": {"options": {"defaultType": "spot"}},
    "xtcom": {"options": {"defaultType": "spot"}},
    "bitmart": {"options": {"defaultType": "spot"}},
    "htx": {"options": {"defaultType": "spot"}},
}

USD_QUOTES = {"USDT", "USD", "USDC", "BUSD"}
LOW_FEE_CHAIN_PRIORITY = ["TRC20", "BEP20", "BSC", "SOL", "MATIC", "ARB", "OP", "Polygon", "TON", "AVAX", "ETH"]

LEV_PATTERNS = [r"\b\d+[LS]\b", r"\bUP\b", r"\bDOWN\b", r"\bBULL\b", r"\bBEAR\b"]
LEV_REGEX = re.compile("|".join(LEV_PATTERNS), re.IGNORECASE)

# ------------------- Sidebar -------------------
st.sidebar.header("Scanner Controls")

buy_exchanges = st.sidebar.multiselect(
    "Buy Exchanges (up to 10)",
    TOP_CCXT_EXCHANGES,
    default=saved.get("buy_exchanges", []),
    max_selections=10,
    format_func=lambda x: EXCHANGE_NAMES[x],
)

sell_exchanges = st.sidebar.multiselect(
    "Sell Exchanges (up to 10)",
    TOP_CCXT_EXCHANGES,
    default=saved.get("sell_exchanges", []),
    max_selections=10,
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

# ------------------- Helpers -------------------
def parse_symbol(symbol: str):
    base, quote = symbol.split("/")[0], symbol.split("/")[1].split(":")[0]
    return base, quote

def market_price_from_ticker(t):
    if not t: return None
    last = t.get("last")
    if last is not None:
        try: return float(last)
        except: pass
    bid, ask = t.get("bid"), t.get("ask")
    if bid and ask:
        try: return (float(bid) + float(ask)) / 2.0
        except: return None
    return None

def safe_price(ex, sym):
    try:
        ob = ex.fetch_order_book(sym, limit=5)
        bid = ob['bids'][0][0] if ob['bids'] else None
        ask = ob['asks'][0][0] if ob['asks'] else None
        if bid and ask:
            return (bid + ask) / 2.0
    except:
        return None
    return None

def is_ticker_fresh(t, max_age_sec=300):
    ts = t.get("timestamp")
    if ts is None: return True
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

def symbol_ok(ex_obj, symbol):
    m = ex_obj.markets.get(symbol, {})
    if not m: return False
    if not m.get("spot", True): return False
    base, quote = parse_symbol(symbol)
    if quote.upper() not in USD_QUOTES: return False
    if LEV_REGEX.search(symbol): return False
    if m.get("active") is False: return False
    return True

def choose_common_chain(ex1, ex2, coin, exclude_chains, include_all_chains):
    try:
        c1 = ex1.currencies.get(coin, {}) or {}
        c2 = ex2.currencies.get(coin, {}) or {}
        nets1 = c1.get("networks", {}) or {}
        nets2 = c2.get("networks", {}) or {}
        common = set(nets1.keys()) & set(nets2.keys())
        if not common: return "❌ No chain", "❌", "❌"
        preferred = [n for n in LOW_FEE_CHAIN_PRIORITY if (include_all_chains or n not in exclude_chains)]
        best = None
        for pref in preferred:
            if pref in common: best = pref; break
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

# ------------------- Core Scan -------------------
def run_scan():
    if not buy_exchanges or not sell_exchanges:
        st.warning("Please select at least one Buy and one Sell exchange.")
        return

    try:
        ex_objs = {}
        for ex_id in set(buy_exchanges + sell_exchanges):
            opts = {"enableRateLimit": True, "timeout": 12000}
            opts.update(EXTRA_OPTS.get(ex_id, {}))
            ex = getattr(ccxt, ex_id)(opts)
            ex.load_markets()
            ex_objs[ex_id] = ex

        bulk_tickers = {}
        for ex_id, ex in ex_objs.items():
            try:
                if ex_id == "upbit":
                    syms = list(ex.symbols)
                    bulk_tickers[ex_id] = {}
                    for i in range(0, len(syms), 200):
                        chunk = syms[i:i+200]
                        bulk_tickers[ex_id].update(ex.fetch_tickers(chunk))
                else:
                    bulk_tickers[ex_id] = ex.fetch_tickers()
            except Exception as e:
                st.warning(f"⚠️ {EXCHANGE_NAMES.get(ex_id, ex_id)} fetch_tickers failed: {e}")
                bulk_tickers[ex_id] = {}

        results = []
        for buy_id in buy_exchanges:
            for sell_id in sell_exchanges:
                if buy_id == sell_id: continue
                buy_ex, sell_ex = ex_objs[buy_id], ex_objs[sell_id]
                buy_tk, sell_tk = bulk_tickers.get(buy_id, {}), bulk_tickers.get(sell_id, {})

                common = set(buy_ex.markets.keys()) & set(sell_ex.markets.keys())
                symbols = [s for s in common if symbol_ok(buy_ex, s) and symbol_ok(sell_ex, s)]
                symbols = symbols[:500]

                for sym in symbols:
                    bt, st_ = buy_tk.get(sym), sell_tk.get(sym)
                    buy_px = market_price_from_ticker(bt) or safe_price(buy_ex, sym)
                    sell_px = market_price_from_ticker(st_) or safe_price(sell_ex, sym)
                    if not buy_px or not sell_px: continue
                    gap = abs(sell_px - buy_px) / buy_px
                    if gap > 0.5: continue

                    buy_fee = buy_ex.markets.get(sym, {}).get("taker", 0.001) or 0.001
                    sell_fee = sell_ex.markets.get(sym, {}).get("taker", 0.001) or 0.001

                    spread = (sell_px - buy_px) / buy_px * 100.0
                    profit_after = spread - (buy_fee * 100.0 + sell_fee * 100.0)
                    if profit_after < min_profit or profit_after > max_profit: continue

                    base, quote = parse_symbol(sym)
                    chain, w_ok, d_ok = choose_common_chain(buy_ex, sell_ex, base, exclude_chains, include_all_chains)
                    if not include_all_chains and (chain in exclude_chains or chain.startswith("❌")): continue
                    if w_ok != "✅" or d_ok != "✅": continue

                    results.append({
                        "Pair": sym,
                        "Buy@": EXCHANGE_NAMES.get(buy_id, buy_id),
                        "Buy Price": round(float(buy_px), 10),
                        "Sell@": EXCHANGE_NAMES.get(sell_id, sell_id),
                        "Sell Price": round(float(sell_px), 10),
                        "Spread %": round(spread, 4),
                        "Profit % After Fees": round(profit_after, 4),
                        "Blockchain": chain,
                        "Withdraw?": w_ok,
                        "Deposit?": d_ok,
                    })

        if results:
            df = pd.DataFrame(results).sort_values(["Profit % After Fees", "Spread %"], ascending=False)
            st.subheader("✅ Profitable Arbitrage Opportunities")
            st.dataframe(df, use_container_width=True)
            st.download_button("⬇️ Download CSV", df.to_csv(index=False), "arbitrage.csv", "text/csv")
        else:
            st.info("No opportunities matched your filters right now.")
    except Exception as e:
        st.error(f"Error: {e}")

# ------------------- Trigger -------------------
if scan_now or auto_refresh:
    with st.spinner("🔍 Scanning exchanges…"):
        run_scan()
    if auto_refresh:
        holder = st.empty()
        for i in range(20, 0, -1):
            holder.write(f"⏳ Refreshing in {i}s…")
            time.sleep(1)
        st.rerun()
