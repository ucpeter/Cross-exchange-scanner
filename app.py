import streamlit as st
import ccxt
import pandas as pd
import time

st.set_page_config(page_title="Cross-Exchange Arbitrage — Market Price", layout="wide")
st.title("🌍 Cross-Exchange Arbitrage Scanner — Market Price (CMC-style)")

# ------------------- High-liquidity spot exchanges -------------------
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

# Force spot where needed (avoids Bitmart futures deprecation error)
EXTRA_OPTS = {
    "bitmart": {"options": {"defaultType": "spot"}},
    "bybit": {"options": {"defaultType": "spot"}},
    "okx": {"options": {"defaultType": "spot"}},
    "bingx": {"options": {"defaultType": "spot"}},
}

# Only compare these quotes (USD-standardized)
USD_QUOTES = {"USDT", "USD", "USDC", "BUSD"}

LOW_FEE_CHAIN_PRIORITY = ["TRC20", "BEP20", "BSC", "SOL", "MATIC", "ARB", "OP", "Polygon", "TON", "AVAX", "ETH"]

# ------------------- UI -------------------
l, r = st.columns(2)
with l:
    buy_exchanges = st.multiselect("Select up to 3 Buy Exchanges", TOP_20_CCXT_EXCHANGES, max_selections=3, format_func=lambda x: EXCHANGE_NAMES[x])
with r:
    sell_exchanges = st.multiselect("Select up to 3 Sell Exchanges", TOP_20_CCXT_EXCHANGES, max_selections=3, format_func=lambda x: EXCHANGE_NAMES[x])

l2, r2 = st.columns(2)
with l2:
    min_profit = st.number_input("Minimum Profit % (after fees)", 0.0, 100.0, 0.5, 0.1)
with r2:
    max_profit = st.number_input("Maximum Profit % (after fees)", 0.0, 200.0, 20.0, 0.1)

auto_refresh = st.checkbox("🔄 Auto Refresh Every 20 Seconds", value=False)
scan_now = st.button("🚀 Scan Now")

st.caption("Tip: For the cleanest results, pick exchanges you actually use and keep buy/sell quotes in USDT/USD/USDC/BUSD.")

# ------------------- Stability cache -------------------
if "op_cache" not in st.session_state:
    st.session_state.op_cache = {}  # key -> [(ts, profit%), ...]

# ------------------- Helpers -------------------
def parse_symbol(symbol: str):
    # ccxt normalized symbols are like "SUI/USDT" or "SUI/USDT:USDT"
    base, quote = symbol.split("/")[0], symbol.split("/")[1].split(":")[0]
    return base, quote

def market_price_from_ticker(t):
    """CMC-style market price: last; fallback to (bid+ask)/2; else None."""
    if not t:
        return None
    last = t.get("last")
    if last is not None:
        return float(last)
    bid, ask = t.get("bid"), t.get("ask")
    if bid is not None and ask is not None:
        return (float(bid) + float(ask)) / 2.0
    return None

def is_ticker_fresh(t, max_age_sec=300):
    ts = t.get("timestamp")
    if ts is None:
        return True  # many exchanges omit timestamp; don't over-prune
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
    """Return (chain_name, withdraw_ok_on_buy, deposit_ok_on_sell) with low-fee priority."""
    try:
        c1 = ex1.currencies.get(coin, {}) or {}
        c2 = ex2.currencies.get(coin, {}) or {}
        nets1 = c1.get("networks", {}) or {}
        nets2 = c2.get("networks", {}) or {}
        common = set(nets1.keys()) & set(nets2.keys())
        if not common:
            return "❌ No chain", "❌", "❌"
        # prefer low-fee chains
        for pref in LOW_FEE_CHAIN_PRIORITY:
            if pref in common:
                best = pref
                break
        else:
            best = sorted(list(common))[0]
        w_ok = "✅" if nets1.get(best, {}).get("withdraw") else "❌"
        d_ok = "✅" if nets2.get(best, {}).get("deposit") else "❌"
        return best, w_ok, d_ok
    except Exception:
        return "❌ Unknown", "❌", "❌"

def estimate_stability(key, current_profit):
    """Return '⏳ 35s observed' style label based on how long it keeps showing up."""
    now = time.time()
    trail = st.session_state.op_cache.get(key, [])
    if not trail or current_profit <= 0:
        st.session_state.op_cache[key] = [(now, current_profit)]
        return "⏳ new"
    trail.append((now, current_profit))
    st.session_state.op_cache[key] = trail[-30:]  # last ~10 mins if 20s refresh
    duration = trail[-1][0] - trail[0][0]
    if duration < 90:
        return f"⏳ {int(duration)}s observed"
    return f"⏳ {duration/60:.1f}m observed"

# Fallback volume keys seen across exchanges inside ticker['info']
INFO_VOLUME_CANDIDATES = [
    "quoteVolume", "baseVolume", "vol", "vol24h", "volCcy24h", "volValue",
    "turnover", "turnover24h", "quoteVolume24h", "amount", "value", "V", "v",
    "acc_trade_price_24h", "quote_volume_24h", "base_volume_24h",
]

def safe_usd_volume(ex_id, symbol, ticker, price, all_tickers):
    """
    Robust USD volume:
      1) prefer ticker['quoteVolume'] when quote is USD-like
      2) else baseVolume * price
      3) else look into ticker['info'] using known keys
      4) else 0
    Then if quote not USD-like, convert using QUOTE/USDT if available.
    """
    try:
        base, quote = parse_symbol(symbol)
        q_upper = quote.upper()
        qvol = ticker.get("quoteVolume")
        bvol = ticker.get("baseVolume")

        # if quote is USD-like and quoteVolume exists, use it directly
        if q_upper in USD_QUOTES and qvol:
            return float(qvol)

        # baseVolume * price (works for any quote)
        if bvol and price:
            usd_val = float(bvol) * float(price)
            return usd_val

        # try raw info fields
        info = ticker.get("info") or {}
        # take the first numeric candidate
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
            # If quote USD-like, assume raw already in quote currency (≈USD)
            if q_upper in USD_QUOTES:
                return float(raw)
            # else try convert via QUOTE/USDT
            conv_sym = f"{q_upper}/USDT"
            conv_t = all_tickers.get(conv_sym)
            conv_px = market_price_from_ticker(conv_t)
            if conv_px:
                return float(raw) * float(conv_px)

        # last fallback: if quoteVolume exists but quote is not USD-like, convert it
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
    if not m.get("spot", True):
        return False
    if m.get("active") is False:
        return False
    # Only USD-standardized quotes
    base, quote = parse_symbol(symbol)
    return quote.upper() in USD_QUOTES

# ------------------- Core scan -------------------
def run_scan():
    if not buy_exchanges or not sell_exchanges:
        st.warning("Please select at least one Buy and one Sell exchange.")
        return

    try:
        # 1) instantiate and load markets
        ex_objs = {}
        for ex_id in set(buy_exchanges + sell_exchanges):
            opts = {"enableRateLimit": True, "timeout": 8000}
            if ex_id in EXTRA_OPTS:
                opts.update(EXTRA_OPTS[ex_id])
            ex = getattr(ccxt, ex_id)(opts)
            ex.load_markets()
            ex_objs[ex_id] = ex

        # 2) bulk fetch tickers (faster; fewer rate-limit hits)
        bulk_tickers = {}
        for ex_id, ex in ex_objs.items():
            try:
                bulk_tickers[ex_id] = ex.fetch_tickers()
            except Exception as e:
                st.warning(f"⚠️ {EXCHANGE_NAMES[ex_id]} fetch_tickers failed: {e}")
                bulk_tickers[ex_id] = {}

        results = []

        # 3) compare prices for common symbols (spot, active, USD quotes only)
        for buy_id in buy_exchanges:
            for sell_id in sell_exchanges:
                if buy_id == sell_id:
                    continue

                buy_ex, sell_ex = ex_objs[buy_id], ex_objs[sell_id]
                buy_tk, sell_tk = bulk_tickers[buy_id], bulk_tickers[sell_id]

                common = set(buy_ex.markets.keys()) & set(sell_ex.markets.keys())
                # filter to valid symbols on each side
                valid_common = [s for s in common if symbol_ok(buy_ex, s) and symbol_ok(sell_ex, s)]
                # keep snappy
                valid_common = valid_common[:400]

                for sym in valid_common:
                    bt, st_ = buy_tk.get(sym), sell_tk.get(sym)
                    if not bt or not st_:
                        continue
                    if not is_ticker_fresh(bt) or not is_ticker_fresh(st_):
                        continue

                    buy_px = market_price_from_ticker(bt)
                    sell_px = market_price_from_ticker(st_)
                    if not buy_px or not sell_px:
                        continue

                    # sanity guard: discard absurd price gaps that are likely stale (e.g., > 50%)
                    gap = abs(sell_px - buy_px) / buy_px
                    if gap > 0.5:
                        continue

                    # fees (taker as conservative default)
                    buy_fee = buy_ex.markets.get(sym, {}).get("taker", 0.001) or 0.001
                    sell_fee = sell_ex.markets.get(sym, {}).get("taker", 0.001) or 0.001

                    spread = (sell_px - buy_px) / buy_px * 100.0
                    profit_after = spread - (buy_fee * 100.0 + sell_fee * 100.0)
                    if profit_after < min_profit or profit_after > max_profit:
                        continue

                    # volumes (USD)
                    buy_vol_usd = safe_usd_volume(buy_id, sym, bt, buy_px, buy_tk)
                    sell_vol_usd = safe_usd_volume(sell_id, sym, st_, sell_px, sell_tk)

                    base, quote = parse_symbol(sym)
                    chain, w_ok, d_ok = choose_common_chain(buy_ex, sell_ex, base)

                    key = f"{sym}|{buy_id}>{sell_id}"
                    stability = estimate_stability(key, profit_after)

                    results.append({
                        "Pair": sym,
                        "Quote": quote,
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
            df = pd.DataFrame(results).sort_values(["Profit % After Fees", "Spread %"], ascending=False)
            st.subheader("✅ Profitable Opportunities (Market Price, USD-standardized)")
            st.dataframe(df, use_container_width=True)
            st.download_button("⬇️ Download CSV", df.to_csv(index=False), "arbitrage_opportunities_market_price.csv", "text/csv")
        else:
            st.info("No opportunities matched your profit range with clean (USD-quote, spot, active) data right now.")

    except Exception as e:
        st.error(f"Error: {e}")

# ------------------- Trigger -------------------
if scan_now or auto_refresh:
    with st.spinner("🔍 Scanning exchanges (CMC-style market prices)…"):
        run_scan()
    if auto_refresh:
        holder = st.empty()
        for i in range(20, 0, -1):
            holder.write(f"⏳ Refreshing in {i}s…")
            time.sleep(1)
        st.experimental_rerun()
