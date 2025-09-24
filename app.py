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
    
# Extra CSS for pill badges and compact table
st.markdown("""
    <style>
    .pill { padding: 2px 10px; border-radius: 999px; font-weight: 700; font-size: 12px; }
    .pill-green { background: #1B5E20; color: #E8F5E9; border: 1px solid #2E7D32; }
    .pill-red { background: #7F1D1D; color: #FEE2E2; border: 1px solid #991B1B; }
    .pill-blue { background: #0D47A1; color: #E3F2FD; border: 1px solid #1565C0; }
    .table-wrap { overflow-x: auto; border-radius: 10px; border: 1px solid #2A2A2A; }
    table.arb-table { width: 100%; border-collapse: collapse; }
    table.arb-table th, table.arb-table td { padding: 8px 10px; border-bottom: 1px solid #222; }
    table.arb-table th { background: #1D1D1D; text-align: left; }
    table.arb-table tr:nth-child(even) { background: #161616; }
    table.arb-table tr:hover { background: #202020; }
    .num { text-align: right; white-space: nowrap; }
    .mono { font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .good { color: #4CAF50; font-weight: 700; }
    .bad { color: #FF5252; font-weight: 700; }
    .spread { color: #42A5F5; font-weight: 700; }
    .small { color: #BDBDBD; font-size: 12px; }
    </style>
""", unsafe_allow_html=True)

# ---------- Runtime state for opportunity lifetime tracking ----------
if "op_cache" not in st.session_state:
    st.session_state.op_cache = {}  # key -> [(timestamp, profit%), ...]
if "lifetime_history" not in st.session_state:
    st.session_state.lifetime_history = {}  # key -> [durations (sec)]
if "last_seen_keys" not in st.session_state:
    st.session_state.last_seen_keys = set()

# ---------- Helpers ----------
USD_QUOTES = {"USDT", "USD", "USDC", "BUSD"}
LOW_FEE_CHAIN_PRIORITY = ["TRC20", "BEP20", "BSC", "SOL", "MATIC", "ARB", "OP", "Polygon", "TON", "AVAX", "ETH"]
LEV_REGEX = re.compile(r"\b(\d+[LS]|UP|DOWN|BULL|BEAR)\b", re.IGNORECASE)

def parse_symbol(symbol: str):
    base, quote = symbol.split("/")[0], symbol.split("/")[1].split(":")[0]
    return base, quote

def market_price_from_ticker(t):
    if not t:
        return None
    last = t.get("last")
    if last is not None:
        try:
            return float(last)
        except:
            pass
    bid, ask = t.get("bid"), t.get("ask")
    if bid is not None and ask is not None:
        try:
            return (float(bid) + float(ask)) / 2.0
        except:
            return None
    return None

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

def secs_to_label(secs):
    return f"{int(secs)}s" if secs < 90 else f"{secs/60:.1f}m"

def update_lifetime_for_disappeared(current_keys):
    gone = st.session_state.last_seen_keys - set(current_keys)
    for key in gone:
        trail = st.session_state.op_cache.get(key, [])
        if trail:
            duration = trail[-1][0] - trail[0][0]
            if duration > 0:
                st.session_state.lifetime_history.setdefault(key, []).append(duration)
    st.session_state.last_seen_keys = set(current_keys)

def stability_and_expiry(key, current_profit):
    now = time.time()
    trail = st.session_state.op_cache.get(key, [])
    if not trail:
        st.session_state.op_cache[key] = [(now, current_profit)]
        return "⏳ new", "~unknown"
    trail.append((now, current_profit))
    st.session_state.op_cache[key] = trail[-30:]  # last ~10 min if 20s cadence
    duration = trail[-1][0] - trail[0][0]
    observed = f"⏳ {secs_to_label(duration)} observed"
    hist = st.session_state.lifetime_history.get(key, [])
    if not hist:
        expiry = "~unknown"
    else:
        avg_life = sum(hist) / len(hist)
        remaining = avg_life - duration
        expiry = "⚠️ past avg" if remaining <= 0 else f"~{secs_to_label(remaining)} left"
    return observed, expiry

INFO_VOLUME_CANDIDATES = [
    "quoteVolume", "baseVolume", "vol", "vol24h", "volCcy24h", "volValue",
    "turnover", "turnover24h", "quoteVolume24h", "amount", "value",
    "acc_trade_price_24h", "quote_volume_24h", "base_volume_24h",
]

def safe_usd_volume(ex_id, symbol, ticker, price, all_tickers):
    try:
        base, quote = parse_symbol(symbol)
        q_upper = quote.upper()
        qvol = ticker.get("quoteVolume")
        bvol = ticker.get("baseVolume")
        if q_upper in USD_QUOTES and qvol:
            return float(qvol)
        if bvol and price:
            return float(bvol) * float(price)
        info = ticker.get("info") or {}
        raw = None
        for key in INFO_VOLUME_CANDIDATES:
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
            if q_upper in USD_QUOTES:
                return float(raw)
            conv_sym = f"{q_upper}/USDT"
            conv_t = all_tickers.get(conv_sym)
            conv_px = market_price_from_ticker(conv_t)
            if conv_px:
                return float(raw) * float(conv_px)
        if qvol:
            conv_sym = f"{q_upper}/USDT"
            conv_t = all_tickers.get(conv_sym)
            conv_px = market_price_from_ticker(conv_t)
            if conv_px:
                return float(qvol) * float(conv_px)
        return 0.0
    except:
        return 0.0

def symbol_ok(ex_obj, symbol):
    m = ex_obj.markets.get(symbol, {})
    if not m:
        return False
    # Spot only
    if not m.get("spot", True):
        return False
    # USD-standardized quotes only
    base, quote = parse_symbol(symbol)
    if quote.upper() not in USD_QUOTES:
        return False
    # Exclude leveraged/ETP tokens
    if LEV_REGEX.search(symbol):
        return False
    # Active markets only (if provided)
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
        # Build preferred list honoring exclusions
        preferred = [n for n in LOW_FEE_CHAIN_PRIORITY if (include_all_chains or n not in exclude_chains)]
        best = None
        for pref in preferred:
            if pref in common:
                best = pref; break
        if not best:
            # fallback: first common, but reject if excluded and not include_all
            candidate = sorted(list(common))[0]
            if not include_all_chains and candidate in exclude_chains:
                return "❌ No chain", "❌", "❌"
            best = candidate
        w_ok = "✅" if nets1.get(best, {}).get("withdraw") else "❌"
        d_ok = "✅" if nets2.get(best, {}).get("deposit") else "❌"
        return best, w_ok, d_ok
    except:
        return "❌ Unknown", "❌", "❌"

# ---------- Core Scan ----------
def run_scan():
    if not buy_exchanges or not sell_exchanges:
        st.warning("Please select at least one Buy and one Sell exchange.")
        return

    try:
        # 1) init exchanges & load spot markets
        ex_objs = {}
        for ex_id in set(buy_exchanges + sell_exchanges):
            opts = {"enableRateLimit": True, "timeout": 12000}
            opts.update(EXTRA_OPTS.get(ex_id, {}))
            ex = getattr(ccxt, ex_id)(opts)
            ex.load_markets()
            ex_objs[ex_id] = ex

        # 2) bulk tickers
        bulk_tickers = {}
        for ex_id, ex in ex_objs.items():
            try:
                bulk_tickers[ex_id] = ex.fetch_tickers()
            except Exception as e:
                st.warning(f"⚠️ {EXCHANGE_NAMES.get(ex_id, ex_id)} fetch_tickers failed: {e}")
                bulk_tickers[ex_id] = {}

        results = []
        current_keys = []

        # 3) compare across selected exchange pairs
        for buy_id in buy_exchanges:
            for sell_id in sell_exchanges:
                if buy_id == sell_id:
                    continue
                buy_ex, sell_ex = ex_objs[buy_id], ex_objs[sell_id]
                buy_tk, sell_tk = bulk_tickers[buy_id], bulk_tickers[sell_id]

                common = set(buy_ex.markets.keys()) & set(sell_ex.markets.keys())
                symbols = [s for s in common if symbol_ok(buy_ex, s) and symbol_ok(sell_ex, s)]
                symbols = symbols[:700]  # keep snappy

                for sym in symbols:
                    bt, st_ = buy_tk.get(sym), sell_tk.get(sym)
                    if not bt or not st_:
                        continue
                    if not is_ticker_fresh(bt) or not is_ticker_fresh(st_):
                        continue

                    buy_px = market_price_from_ticker(bt)
                    sell_px = market_price_from_ticker(st_)
                    if not buy_px or not sell_px or buy_px <= 0:
                        continue

                    vol_usd_buy = safe_usd_volume(buy_id, sym, bt, buy_px, buy_tk)
                    vol_usd_sell = safe_usd_volume(sell_id, sym, st_, sell_px, sell_tk)
                    min_vol = min(vol_usd_buy, vol_usd_sell)
                    if min_vol < min_24h_vol_usd:
                        continue

                    profit = (sell_px - buy_px) / buy_px * 100
                    if profit < min_profit or profit > max_profit:
                        continue

                    base, quote = parse_symbol(sym)
                    chain, w_ok, d_ok = choose_common_chain(
                        buy_ex, sell_ex, base, exclude_chains, include_all_chains
                    )
                    if chain.startswith("❌"):
                        continue
                    if w_ok != "✅" or d_ok != "✅":
                        continue

                    key = f"{buy_id}-{sell_id}-{sym}"
                    current_keys.append(key)
                    observed, expiry = stability_and_expiry(key, profit)

                    results.append({
                        "buy_ex": buy_id,
                        "sell_ex": sell_id,
                        "symbol": sym,
                        "buy_px": buy_px,
                        "sell_px": sell_px,
                        "profit": profit,
                        "volume": min_vol,
                        "chain": chain,
                        "withdraw": w_ok,
                        "deposit": d_ok,
                        "observed": observed,
                        "expiry": expiry,
                    })

        update_lifetime_for_disappeared(current_keys)

        if not results:
            st.info("No arbitrage opportunities found.")
            return

        df = pd.DataFrame(results)
        df = df.sort_values("profit", ascending=False).reset_index(drop=True)

        # ---------- Render HTML table ----------
        rows = []
        for _, r in df.iterrows():
            rows.append(f"""
            <tr>
                <td><b>{EXCHANGE_NAMES.get(r['buy_ex'], r['buy_ex'])}</b></td>
                <td><b>{EXCHANGE_NAMES.get(r['sell_ex'], r['sell_ex'])}</b></td>
                <td>{r['symbol']}</td>
                <td class='num mono'>{r['buy_px']:.6f}</td>
                <td class='num mono'>{r['sell_px']:.6f}</td>
                <td class='num spread'>{r['profit']:.2f}%</td>
                <td class='num'>{fmt_usd(r['volume'])}</td>
                <td><span class='pill pill-blue'>{r['chain']}</span></td>
                <td>{r['withdraw']}</td>
                <td>{r['deposit']}</td>
                <td class='small'>{r['observed']}</td>
                <td class='small'>{r['expiry']}</td>
            </tr>
            """)

        html = f"""
        <div class='table-wrap'>
        <table class='arb-table'>
        <thead>
            <tr>
                <th>Buy @</th>
                <th>Sell @</th>
                <th>Pair</th>
                <th>Buy Price</th>
                <th>Sell Price</th>
                <th>Profit %</th>
                <th>24h Vol</th>
                <th>Chain</th>
                <th>W</th>
                <th>D</th>
                <th>Observed</th>
                <th>Expiry</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
        </table>
        </div>
        """
        st.markdown(html, unsafe_allow_html=True)

    except Exception as e:
        st.error(f"❌ Error during scan: {e}")

# ------------------- Main Loop -------------------
if auto_refresh and not scan_now:
    placeholder = st.empty()
    while True:
        with placeholder.container():
            run_scan()
        time.sleep(20)
else:
    if scan_now:
        run_scan()
