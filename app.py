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
    </style>
""", unsafe_allow_html=True)

st.title("🌍 Cross-Exchange Arbitrage Scanner (Debug Mode)")

# ------------------- Exchange List -------------------
TOP_20_CCXT_EXCHANGES = [
    "binance", "okx", "coinbase", "kraken", "bybit", "kucoin",
    "mexc", "bitfinex", "bitget", "gateio", "crypto_com",
    "upbit", "whitebit", "poloniex", "bingx", "lbank",
    "bitstamp", "gemini", "bitrue", "xt",
]

EXCHANGE_NAMES = {ex: ex.capitalize() for ex in TOP_20_CCXT_EXCHANGES}

EXTRA_OPTS = {
    "bybit": {"options": {"defaultType": "spot"}},
    "okx": {"options": {"defaultType": "spot"}},
    "bingx": {"options": {"defaultType": "spot"}},
    "mexc": {"options": {"defaultType": "spot"}},
    "bitrue": {"options": {"defaultType": "spot"}},
    "xt": {"options": {"defaultType": "spot"}},
}

USD_QUOTES = {"USDT", "USD", "USDC", "BUSD"}

# ------------------- Helpers -------------------
def parse_symbol(symbol: str):
    base, quote = symbol.split("/")[0], symbol.split("/")[1].split(":")[0]
    return base.upper(), quote.upper()

def normalize_symbol(symbol: str):
    """Normalize symbols across exchanges: remove prefixes like 1000SHIB -> SHIB"""
    try:
        base, quote = parse_symbol(symbol)
        base = re.sub(r"^\d+", "", base)  # drop leading multipliers
        base = base.replace("1000", "").replace("100", "")  # extra cleanup
        return f"{base}/{quote}"
    except Exception:
        return symbol

def market_price_from_ticker(t):
    if not t:
        return None
    last = t.get("last")
    if last:
        try: return float(last)
        except: pass
    bid, ask = t.get("bid"), t.get("ask")
    if bid and ask:
        try: return (float(bid) + float(ask)) / 2
        except: return None
    return None

def is_ticker_fresh(t, max_age_sec=300):
    ts = t.get("timestamp")
    if ts is None: return True
    now = int(time.time() * 1000)
    return (now - int(ts)) <= max_age_sec * 1000

# ------------------- Core Scan -------------------
def run_scan(buy_exchanges, sell_exchanges, min_profit=1.0, max_profit=20.0, min_24h_vol_usd=100000):
    if not buy_exchanges or not sell_exchanges:
        st.warning("Please select at least one Buy and one Sell exchange.")
        return

    try:
        # 1) init exchanges
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
                print(f"[WARN] {ex_id} fetch_tickers failed: {e}")
                bulk_tickers[ex_id] = {}

        results = []

        # 3) compare across exchange pairs
        for buy_id in buy_exchanges:
            for sell_id in sell_exchanges:
                if buy_id == sell_id: continue
                buy_ex, sell_ex = ex_objs[buy_id], ex_objs[sell_id]
                buy_tk, sell_tk = bulk_tickers[buy_id], bulk_tickers[sell_id]

                # normalize symbols
                buy_syms = {normalize_symbol(s): s for s in buy_ex.markets.keys()}
                sell_syms = {normalize_symbol(s): s for s in sell_ex.markets.keys()}

                common = set(buy_syms.keys()) & set(sell_syms.keys())
                print(f"[INFO] {buy_id}->{sell_id} common={len(common)}")

                for nsym in list(common)[:300]:  # limit for speed
                    b_sym, s_sym = buy_syms[nsym], sell_syms[nsym]
                    bt, st_ = buy_tk.get(b_sym), sell_tk.get(s_sym)
                    if not bt or not st_:
                        print(f"[SKIP] Missing ticker {nsym} {buy_id}/{sell_id}")
                        continue

                    if not is_ticker_fresh(bt) or not is_ticker_fresh(st_):
                        print(f"[SKIP] Stale ticker {nsym}")
                        continue

                    buy_px = market_price_from_ticker(bt)
                    sell_px = market_price_from_ticker(st_)
                    if not buy_px or not sell_px:
                        print(f"[SKIP] No market price {nsym} buy={buy_px} sell={sell_px}")
                        continue

                    gap = abs(sell_px - buy_px) / buy_px
                    if gap > 0.5:
                        print(f"[SKIP] Gap too high {nsym} buy={buy_px} sell={sell_px}")
                        continue

                    spread = (sell_px - buy_px) / buy_px * 100.0
                    profit_after = spread - 0.2  # assume ~0.1% fee each side
                    if profit_after < min_profit or profit_after > max_profit:
                        print(f"[SKIP] Profit out of range {nsym} {profit_after:.2f}%")
                        continue

                    results.append({
                        "Pair": nsym,
                        "Buy@": buy_id, "Buy Price": buy_px,
                        "Sell@": sell_id, "Sell Price": sell_px,
                        "Spread %": round(spread, 4),
                        "Profit % After Fees": round(profit_after, 4),
                    })

        if results:
            df = pd.DataFrame(results).sort_values("Profit % After Fees", ascending=False)
            st.dataframe(df)
        else:
            st.info("❌ No opportunities found this round")

    except Exception as e:
        st.error(f"Scan failed: {e}")
