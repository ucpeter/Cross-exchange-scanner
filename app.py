import time
import re
import ccxt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Cross-Exchange Arbitrage — Market Price", layout="wide")
st.title("🌍 Cross-Exchange Arbitrage Scanner — Market Price (CMC-style)")

# ------------------- Exchange lists & options -------------------
TOP_20_CCXT_EXCHANGES = [
    "binance", "okx", "coinbase", "kraken", "bybit", "kucoin",
    "mexc", "bitfinex", "bitget", "gateio", "htx", "crypto_com",
    "upbit", "bitmart", "whitebit", "poloniex", "bingx", "lbank",
    "bitstamp", "gemini",
]

EXCHANGE_NAMES = {
    "binance": "Binance", "okx": "OKX", "coinbase": "Coinbase",
    "kraken": "Kraken", "bybit": "Bybit", "kucoin": "KuCoin",
    "mexc": "MEXC", "bitfinex": "Bitfinex", "bitget": "Bitget",
    "gateio": "Gate.io", "htx": "HTX (Huobi)", "crypto_com": "Crypto.com",
    "upbit": "Upbit", "bitmart": "Bitmart", "whitebit": "WhiteBIT",
    "poloniex": "Poloniex", "bingx": "BingX", "lbank": "LBank",
    "bitstamp": "Bitstamp", "gemini": "Gemini",
}

# Force spot endpoints everywhere we can
EXTRA_OPTS = {
    "bitmart": {"options": {"defaultType": "spot", "defaultSubType": "linear"}},  # try to dodge futures v1
    "bybit": {"options": {"defaultType": "spot"}},
    "okx": {"options": {"defaultType": "spot"}},
    "bingx": {"options": {"defaultType": "spot"}},
    "mexc": {"options": {"defaultType": "spot"}},
}

USD_QUOTES = {"USDT", "USD", "USDC", "BUSD"}

# Low-fee networks preferred for transfers (order matters)
LOW_FEE_CHAIN_PRIORITY = ["TRC20", "BEP20", "BSC", "SOL", "MATIC", "ARB", "OP", "Polygon", "TON", "AVAX", "ETH"]

# Regex/keywords to drop leveraged tokens & ETPs even if they’re “spot”
LEV_PATTERNS = [
    r"\b\d+[LS]\b",     # 3L, 5S, etc
    r"\bUP\b", r"\bDOWN\b",
    r"\bBULL\b", r"\bBEAR\b",
    r"\b3L\b", r"\b3S\b", r"\b5L\b", r"\b5S\b",
]
LEV_REGEX = re.compile("|".join(LEV_PATTERNS), re.IGNORECASE)

# ------------------- Sidebar Controls -------------------
st.sidebar.header("Scanner Controls")

buy_exchanges = st.sidebar.multiselect(
    "Buy Exchanges (up to 5)",
    TOP_20_CCXT_EXCHANGES, max_selections=5,
    format_func=lambda x: EXCHANGE_NAMES[x],
)

sell_exchanges = st.sidebar.multiselect(
    "Sell Exchanges (up to 5)",
    TOP_20_CCXT_EXCHANGES, max_selections=5,
    format_func=lambda x: EXCHANGE_NAMES[x],
)

min_profit = st.sidebar.number_input("Minimum Profit % (after fees)", 0.0, 100.0, 1.0, 0.1)
max_profit = st.sidebar.number_input("Maximum Profit % (after fees)", 0.0, 200.0, 20.0, 0.1)

min_24h_vol_usd = st.sidebar.number_input("Min 24h Volume (USD) per side", 0.0, 1_000_000_000.0, 100_000.0, 50_000.0)

st.sidebar.markdown("---")
exclude_chains = st.sidebar.multiselect(
    "Exclude Blockchains (skip these networks)",
    ["ETH", "TRC20", "BEP20", "BSC", "SOL", "MATIC", "ARB", "OP", "Polygon", "TON", "AVAX"],
    default=["ETH"],  # default: skip expensive ERC20
)
include_all_chains = st.sidebar.checkbox("Include all blockchains (ignore exclusion)", value=False)

st.sidebar.markdown("---")
auto_refresh = st.sidebar.checkbox("🔄 Auto Refresh Every 20s", value=False)
scan_now = st.button("🚀 Scan Now")

st.caption(
    "Prices use *last* (or mid of bid/ask). Only **spot** pairs with **USDT/USD/USDC/BUSD** are compared. "
    "We also skip leveraged tokens (3L/3S, UP/DOWN, BULL/BEAR). "
    "Both sides must meet **Min 24h Vol (USD)** and **WD/DP must be enabled** on the chosen chain."
)

# ------------------- Session State -------------------
if "op_cache" not in st.session_state:
    st.session_state.op_cache = {}  # key -> [(timestamp, profit%), ...]
if "lifetime_history" not in st.session_state:
    st.session_state.lifetime_history = {}  # key -> [durations (sec)]
if "last_seen_keys" not in st.session_state:
    st.session_state.last_seen_keys = set()

# ------------------- Helpers -------------------
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
        except Exception:
            pass
    bid, ask = t.get("bid"), t.get("ask")
    if bid is not None and ask is not None:
        try:
            return (float(bid) + float(ask)) / 2.0
        except Exception:
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
        if x >= 1e3: return f"${x/1e3:.2f}K"
        return f"${x:,.0f}"
    except Exception:
        return "$0"

def choose_common_chain(ex1, ex2, coin):
    try:
        c1 = ex1.currencies.get(coin, {}) or {}
        c2 = ex2.currencies.get(coin, {}) or {}
        nets1 = c1.get("networks", {}) or {}
        nets2 = c2.get("networks", {}) or {}
        common = set(nets1.keys()) & set(nets2.keys())
        if not common:
            return "❌ No chain", "❌", "❌"
        # honor user exclusions unless "include all"
        preferred_list = LOW_FEE_CHAIN_PRIORITY[:]
        if include_all_chains is False and exclude_chains:
            preferred_list = [n for n in preferred_list if n not in exclude_chains]
        # pick best common
        best = None
        for pref in preferred_list:
            if pref in common:
                best = pref; break
        if not best:
            # if no preferred common, pick lexicographically
            best = sorted(list(common))[0]
            # if excluded and not forced to include-all, reject entirely
            if include_all_chains is False and best in exclude_chains:
                return "❌ No chain", "❌", "❌"
        w_ok = "✅" if nets1.get(best, {}).get("withdraw") else "❌"
        d_ok = "✅" if nets2.get(best, {}).get("deposit") else "❌"
        return best, w_ok, d_ok
    except Exception:
        return "❌ Unknown", "❌", "❌"

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
            except Exception:
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
    except Exception:
        return 0.0

def symbol_ok(ex_obj, symbol):
    m = ex_obj.markets.get(symbol, {})
    if not m:
        return False
    # hard spot only
    if not m.get("spot", True):
        return False
    # USD quotes only
    base, quote = parse_symbol(symbol)
    if quote.upper() not in USD_QUOTES:
        return False
    # exclude leveraged/ETP names
    if LEV_REGEX.search(symbol):
        return False
    return True

def secs_to_label(secs):
    if secs < 90:
        return f"{int(secs)}s"
    return f"{secs/60:.1f}m"

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
    st.session_state.op_cache[key] = trail[-30:]
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
# ------------------- Core Scan -------------------
def run_scan():
    if not buy_exchanges or not sell_exchanges:
        st.warning("Please select at least one Buy and one Sell exchange.")
        return

    try:
        # 1) init exchanges & load spot markets
        ex_objs = {}
        for ex_id in set(buy_exchanges + sell_exchanges):
            opts = {"enableRateLimit": True, "timeout": 10000}
            if ex_id in EXTRA_OPTS:
                opts.update(EXTRA_OPTS[ex_id])
            ex = getattr(ccxt, ex_id)(opts)
            ex.load_markets()
            ex_objs[ex_id] = ex

        # 2) bulk tickers
        bulk_tickers = {}
        for ex_id, ex in ex_objs.items():
            try:
                bulk_tickers[ex_id] = ex.fetch_tickers()
            except Exception as e:
                st.warning(f"⚠️ {EXCHANGE_NAMES[ex_id]} fetch_tickers failed: {e}")
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
                symbols = symbols[:700]  # keep snappy even with 5x5 combos

                for sym in symbols:
                    bt, st_ = buy_tk.get(sym), sell_tk.get(sym)
                    if not bt or not st_:
                        continue
                    if not is_ticker_fresh(bt) or not is_ticker_fresh(st_):
                        continue

                    buy_px = market_price_from_ticker(bt)
                    sell_px = market_price_from_ticker(st_)
                    if not buy_px or not sell_px:
                        continue

                    # sanity guard: throw out absurd/stale outliers
                    gap = abs(sell_px - buy_px) / buy_px
                    if gap > 0.5:
                        continue

                    buy_fee = buy_ex.markets.get(sym, {}).get("taker", 0.001) or 0.001
                    sell_fee = sell_ex.markets.get(sym, {}).get("taker", 0.001) or 0.001

                    spread = (sell_px - buy_px) / buy_px * 100.0
                    profit_after = spread - (buy_fee * 100.0 + sell_fee * 100.0)
                    if profit_after < min_profit or profit_after > max_profit:
                        continue

                    # volumes (USD) + liquidity filter
                    buy_vol_usd = safe_usd_volume(buy_id, sym, bt, buy_px, buy_tk)
                    sell_vol_usd = safe_usd_volume(sell_id, sym, st_, sell_px, sell_tk)
                    if buy_vol_usd < min_24h_vol_usd or sell_vol_usd < min_24h_vol_usd:
                        continue

                    base, quote = parse_symbol(sym)
                    chain, w_ok, d_ok = choose_common_chain(buy_ex, sell_ex, base)

                    # Respect chain exclusions (unless include_all_chains)
                    if include_all_chains is False and chain in exclude_chains:
                        continue

                    # Require WD/DP enabled
                    if w_ok != "✅" or d_ok != "✅":
                        continue

                    key = f"{sym}|{buy_id}>{sell_id}"
                    current_keys.append(key)
                    observed, expiry = stability_and_expiry(key, profit_after)

                    results.append({
                        "Pair": sym,
                        "Quote": quote,
                        "Buy@": EXCHANGE_NAMES[buy_id],
                        "Buy Price": round(float(buy_px), 10),
                        "Sell@": EXCHANGE_NAMES[sell_id],
                        "Sell Price": round(float(sell_px), 10),
                        "Spread %": round(spread, 4),
                        "Profit % After Fees": round(profit_after, 4),
                        "Buy Vol (24h)": fmt_usd(buy_vol_usd),
                        "Sell Vol (24h)": fmt_usd(sell_vol_usd),
                        "Withdraw?": w_ok,
                        "Deposit?": d_ok,
                        "Blockchain": chain,
                        "Stability": observed,
                        "Est. Expiry": expiry,
                    })

        # 4) record lifetimes for disappeared ops, then update last_seen
        update_lifetime_for_disappeared(current_keys)

        # 5) display
        if results:
            df = pd.DataFrame(results).sort_values(
                ["Profit % After Fees", "Spread %"], ascending=False
            ).reset_index(drop=True)
            df.insert(0, "#", range(1, len(df) + 1))  # clean numbering
            st.subheader("✅ Profitable Opportunities (Market Price, USD-standardized)")
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.download_button(
                "⬇️ Download CSV",
                df.to_csv(index=False),
                "arbitrage_opportunities.csv",
                "text/csv",
            )
        else:
            st.info("No opportunities matched your profit/volume/chain filters right now.")

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
        st.experimental_rerun()
