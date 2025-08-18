import streamlit as st
import ccxt
import pandas as pd
import time

st.set_page_config(page_title="Cross-Exchange Arbitrage (Market Price)", layout="wide")
st.title("🌍 Cross-Exchange Arbitrage Scanner — Market Price (CMC-style)")

# ------------------- Top 20 High-Volume Spot Exchanges -------------------
TOP_20_CCXT_EXCHANGES = [
    "binance", "okx", "coinbase", "kraken", "bybit", "kucoin",
    "mexc3", "bitfinex", "bitget", "gateio", "htx", "crypto_com",
    "upbit", "bitmart", "whitebit", "poloniex", "bingx", "lbank",
    "bitstamp", "gemini",
]

EXCHANGE_NAMES = {
    "binance": "Binance", "okx": "OKX", "coinbase": "Coinbase",
    "kraken": "Kraken", "bybit": "Bybit", "kucoin": "KuCoin",
    "mexc3": "MEXC", "bitfinex": "Bitfinex", "bitget": "Bitget",
    "gateio": "Gate.io", "htx": "HTX (Huobi)", "crypto_com": "Crypto.com",
    "upbit": "Upbit", "bitmart": "Bitmart", "whitebit": "WhiteBIT",
    "poloniex": "Poloniex", "bingx": "BingX", "lbank": "LBank",
    "bitstamp": "Bitstamp", "gemini": "Gemini",
}

# Some exchanges need explicit "spot" mode to avoid futures API issues
EXTRA_OPTS = {
    "bitmart": {"options": {"defaultType": "spot"}},
    "bybit": {"options": {"defaultType": "spot"}},
    "okx": {"options": {"defaultType": "spot"}},
    "bingx": {"options": {"defaultType": "spot"}},
}

# ------------------- UI -------------------
l, r = st.columns(2)
with l:
    buy_exchanges = st.multiselect(
        "Select up to 3 Buy Exchanges",
        TOP_20_CCXT_EXCHANGES, max_selections=3,
        format_func=lambda x: EXCHANGE_NAMES[x]
    )
with r:
    sell_exchanges = st.multiselect(
        "Select up to 3 Sell Exchanges",
        TOP_20_CCXT_EXCHANGES, max_selections=3,
        format_func=lambda x: EXCHANGE_NAMES[x]
    )

l2, r2 = st.columns(2)
with l2:
    min_profit = st.number_input("Minimum Profit % (after fees)", 0.0, 100.0, 0.5, 0.1)
with r2:
    max_profit = st.number_input("Maximum Profit % (after fees)", 0.0, 200.0, 20.0, 0.1)

auto_refresh = st.checkbox("🔄 Auto Refresh Every 20 Seconds", value=False)
scan_now = st.button("🚀 Scan Now")

st.write(f"🔍 **Buy:** {[EXCHANGE_NAMES[e] for e in buy_exchanges]}  →  **Sell:** {[EXCHANGE_NAMES[e] for e in sell_exchanges]}")
st.write(f"📊 Showing opportunities with **{min_profit}% ≤ Profit ≤ {max_profit}%**")

# ------------------- State for stability -------------------
if "op_cache" not in st.session_state:
    st.session_state.op_cache = {}  # key -> [(ts, profit%), ...]

# ------------------- Helpers -------------------
LOW_FEE_CHAIN_PRIORITY = ["TRC20", "BEP20", "BSC", "SOL", "MATIC", "ARB", "OP", "Polygon", "TON", "AVAX", "ETH"]

def choose_common_chain(ex1, ex2, coin):
    """Return (chain_name, withdraw_ok_on_buy, deposit_ok_on_sell) with low-fee priority."""
    try:
        c1 = ex1.currencies.get(coin, {}) or {}
        c2 = ex2.currencies.get(coin, {}) or {}
        nets1 = c1.get("networks", {}) or {}
        nets2 = c2.get("networks", {}) or {}
        common = set(nets1.keys()) & set(nets2.keys())
        if not common:
            return "❌ No chain", "❌", "❌"
        # pick best by priority
        best = None
        for pref in LOW_FEE_CHAIN_PRIORITY:
            if pref in common:
                best = pref
                break
        if not best:
            best = sorted(list(common))[0]
        w_ok = "✅" if nets1.get(best, {}).get("withdraw") else "❌"
        d_ok = "✅" if nets2.get(best, {}).get("deposit") else "❌"
        return best, w_ok, d_ok
    except Exception:
        return "❌ Unknown", "❌", "❌"

def market_price_from_ticker(t):
    """CMC-style price: use last; if missing, use (bid+ask)/2; else None."""
    if not t:
        return None
    last = t.get("last")
    if last:
        return last
    bid, ask = t.get("bid"), t.get("ask")
    if bid and ask:
        return (bid + ask) / 2.0
    return None

def estimate_stability(key, current_profit):
    """
    Track how long an opp stays profitable.
    We store timestamps whenever we see it profitable.
    We return a short human label like '⏳ 35s observed' or '⏳ 2.0m observed'.
    """
    now = time.time()
    trail = st.session_state.op_cache.get(key, [])
    # if new or profit turned negative earlier, reset window
    if not trail or current_profit <= 0:
        st.session_state.op_cache[key] = [(now, current_profit)]
        return "⏳ new"
    # append and keep last 30 points (~10 min if 20s refresh)
    trail.append((now, current_profit))
    st.session_state.op_cache[key] = trail[-30:]
    duration = trail[-1][0] - trail[0][0]
    if duration < 90:
        return f"⏳ {int(duration)}s observed"
    else:
        return f"⏳ {duration/60:.1f}m observed"

def safe_usd_volume(symbol, ticker, all_tickers):
    """
    Best-effort USD volume using only what we already have:
    - Prefer quote = USD/USDT/USDC → baseVolume * price
    - Else if quote has a USDT market in 'all_tickers', use that to convert
    - Else fall back to ticker's quoteVolume if present
    - Else 0
    """
    try:
        base_vol = ticker.get("baseVolume") or 0
        price = market_price_from_ticker(ticker) or 0
        if base_vol and price:
            base, quote = symbol.split("/")
            quote = quote.upper()
            # direct USD-like quotes
            if quote in ("USD", "USDT", "USDC", "BUSD"):
                return float(base_vol) * float(price)
            # try quote/USDT conversion
            qsym = f"{quote}/USDT"
            qt = all_tickers.get(qsym)
            qprice = market_price_from_ticker(qt)
            if qprice:
                return float(base_vol) * float(price) * float(qprice)
        # fallback: use quoteVolume if it's already in USD-ish terms
        qvol = ticker.get("quoteVolume")
        return float(qvol) if qvol else 0.0
    except Exception:
        return 0.0

def fmt_usd(x):
    try:
        x = float(x or 0)
        if x >= 1e9: return f"${x/1e9:.2f}B"
        if x >= 1e6: return f"${x/1e6:.2f}M"
        if x >= 1e3: return f"${x/1e3:.2f}K"
        return f"${x:,.0f}"
    except Exception:
        return "$0"

# ------------------- Core Scan -------------------
def run_scan():
    if not buy_exchanges or not sell_exchanges:
        st.warning("Please select at least one Buy and one Sell exchange.")
        return

    try:
        # 1) instantiate & load markets
        ex_objs = {}
        for ex_id in set(buy_exchanges + sell_exchanges):
            opts = {"enableRateLimit": True, "timeout": 7000}
            if ex_id in EXTRA_OPTS:
                opts.update(EXTRA_OPTS[ex_id])
            ex = getattr(ccxt, ex_id)(opts)
            ex.load_markets()
            ex_objs[ex_id] = ex

        # 2) bulk fetch tickers for each selected exchange (fast path)
        bulk_tickers = {}
        for ex_id, ex in ex_objs.items():
            try:
                bulk_tickers[ex_id] = ex.fetch_tickers()
            except Exception as e:
                st.warning(f"⚠️ {EXCHANGE_NAMES[ex_id]} fetch_tickers failed: {e}")
                bulk_tickers[ex_id] = {}

        results = []

        # 3) compare market prices across exchange pairs on their *common* symbols
        for buy_id in buy_exchanges:
            for sell_id in sell_exchanges:
                if buy_id == sell_id:
                    continue
                buy_ex, sell_ex = ex_objs[buy_id], ex_objs[sell_id]
                buy_tk, sell_tk = bulk_tickers[buy_id], bulk_tickers[sell_id]

                common = set(buy_ex.markets.keys()) & set(sell_ex.markets.keys())
                # optional: keep it snappy
                # common = list(common)[:300]

                for sym in common:
                    bt = buy_tk.get(sym)  # buy exchange ticker for this symbol
                    st_ = sell_tk.get(sym)  # sell exchange ticker for this symbol
                    buy_px = market_price_from_ticker(bt)
                    sell_px = market_price_from_ticker(st_)
                    if not buy_px or not sell_px:
                        continue  # skip if either side missing a sensible price

                    # fees (taker as conservative default)
                    buy_fee = buy_ex.markets.get(sym, {}).get("taker", 0.001) or 0.001
                    sell_fee = sell_ex.markets.get(sym, {}).get("taker", 0.001) or 0.001

                    # spread & profit (CMC-style)
                    spread = (sell_px - buy_px) / buy_px * 100.0
                    profit_after = spread - (buy_fee * 100.0 + sell_fee * 100.0)

                    if profit_after < min_profit or profit_after > max_profit:
                        continue

                    # 24h volumes (USD-ish)
                    buy_vol_usd = safe_usd_volume(sym, bt or {}, buy_tk)
                    sell_vol_usd = safe_usd_volume(sym, st_ or {}, sell_tk)

                    # transferability & chain
                    base, _quote = sym.split("/")
                    chain, w_ok, d_ok = choose_common_chain(buy_ex, sell_ex, base)

                    key = f"{sym}|{buy_id}>{sell_id}"
                    stability = estimate_stability(key, profit_after)

                    results.append({
                        "Pair": sym,
                        "Buy@": EXCHANGE_NAMES[buy_id],
                        "Buy Price": round(float(buy_px), 8),
                        "Sell@": EXCHANGE_NAMES[sell_id],
                        "Sell Price": round(float(sell_px), 8),
                        "Spread %": round(spread, 3),
                        "Profit % After Fees": round(profit_after, 3),
                        "Buy Vol (24h)": fmt_usd(buy_vol_usd),
                        "Sell Vol (24h)": fmt_usd(sell_vol_usd),
                        "Withdraw?": w_ok,
                        "Deposit?": d_ok,
                        "Blockchain": chain,
                        "Stability": stability,
                    })

        if results:
            df = pd.DataFrame(results).sort_values("Profit % After Fees", ascending=False)
            st.subheader("✅ Profitable Opportunities (Market Price)")
            st.dataframe(df, use_container_width=True)
            st.download_button("⬇️ Download CSV", df.to_csv(index=False), "arbitrage_opportunities_market_price.csv", "text/csv")
        else:
            st.info("No opportunities in the selected profit range right now.")

    except Exception as e:
        st.error(f"Error: {e}")

# ------------------- Trigger -------------------
if scan_now or auto_refresh:
    with st.spinner("🔍 Scanning exchanges (market prices)…"):
        run_scan()
    if auto_refresh:
        holder = st.empty()
        for i in range(20, 0, -1):
            holder.write(f"⏳ Refreshing in {i}s…")
            time.sleep(1)
        st.experimental_rerun()
