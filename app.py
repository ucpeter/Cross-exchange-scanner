import streamlit as st
import ccxt
import pandas as pd
import time

st.set_page_config(page_title="Cross-Exchange Arbitrage Scanner", layout="wide")
st.title("🌍 Cross-Exchange Arbitrage Scanner")

# ------------------- Top 20 High-Volume Spot Exchanges -------------------
TOP_20_CCXT_EXCHANGES = [
    "binance", "okx", "coinbase", "kraken", "bybit", "kucoin",
    "mexc3", "bitfinex", "bitget", "gateio", "htx", "crypto_com",
    "upbit", "bitmart", "whitebit", "poloniex", "bingx", "lbank",
    "bitstamp", "gemini"
]

EXCHANGE_NAMES = {
    "binance": "Binance",
    "okx": "OKX",
    "coinbase": "Coinbase",
    "kraken": "Kraken",
    "bybit": "Bybit",
    "kucoin": "KuCoin",
    "mexc3": "MEXC",
    "bitfinex": "Bitfinex",
    "bitget": "Bitget",
    "gateio": "Gate.io",
    "htx": "HTX (Huobi)",
    "crypto_com": "Crypto.com",
    "upbit": "Upbit",
    "bitmart": "Bitmart",
    "whitebit": "WhiteBIT",
    "poloniex": "Poloniex",
    "bingx": "BingX",
    "lbank": "LBank",
    "bitstamp": "Bitstamp",
    "gemini": "Gemini"
}

# ------------------- Streamlit UI -------------------
col1, col2 = st.columns(2)
with col1:
    buy_exchanges = st.multiselect("Select up to 3 Buy Exchanges", TOP_20_CCXT_EXCHANGES, max_selections=3, format_func=lambda x: EXCHANGE_NAMES[x])
with col2:
    sell_exchanges = st.multiselect("Select up to 3 Sell Exchanges", TOP_20_CCXT_EXCHANGES, max_selections=3, format_func=lambda x: EXCHANGE_NAMES[x])

col3, col4 = st.columns(2)
with col3:
    min_profit = st.number_input("Minimum Profit %", min_value=0.0, value=0.2, step=0.1)
with col4:
    max_profit = st.number_input("Maximum Profit %", min_value=0.0, value=5.0, step=0.1)

auto_refresh = st.checkbox("🔄 Auto Refresh Every 20 Seconds", value=False)

scan_now = st.button("🚀 Scan Now")

st.write(f"🔍 Scanning Buy: {[EXCHANGE_NAMES[e] for e in buy_exchanges]} → Sell: {[EXCHANGE_NAMES[e] for e in sell_exchanges]}")
st.write(f"📊 Filtering opportunities between **{min_profit}%** and **{max_profit}%**")

# ------------------- Utility Functions -------------------
if "op_cache" not in st.session_state:
    st.session_state.op_cache = {}

def format_usd(val):
    if val > 1e9:
        return f"${val/1e9:.2f}B"
    elif val > 1e6:
        return f"${val/1e6:.2f}M"
    else:
        return f"${val:,.0f}"

def check_transferability(ex1, ex2, coin):
    try:
        c1 = ex1.currencies.get(coin, {})
        c2 = ex2.currencies.get(coin, {})
        nets1 = set(c1.get("networks", {}).keys())
        nets2 = set(c2.get("networks", {}).keys())
        common = nets1 & nets2
        if not common:
            return "❌ No common chain"
        for net in common:
            n1 = c1["networks"][net]
            n2 = c2["networks"][net]
            if n1.get("withdraw") and n2.get("deposit"):
                return f"✅ {net}"
        return "❌ Not transferable"
    except Exception:
        return "❌ Unknown"

def estimate_stability(key, profit_after):
    now = time.time()
    cache = st.session_state.op_cache
    if key not in cache:
        cache[key] = [(now, profit_after)]
        return "Fleeting"
    cache[key].append((now, profit_after))
    cache[key] = cache[key][-5:]
    if len(cache[key]) >= 3:
        if all(p > 0 for (_, p) in cache[key]):
            return "Likely Stable"
    return "Fleeting"

# ------------------- Arbitrage Scan Function -------------------
def run_scan():
    if not buy_exchanges or not sell_exchanges:
        st.warning("Please select at least one Buy and one Sell exchange.")
        return

    try:
        exchanges = {}
        for eid in set(buy_exchanges + sell_exchanges):
            exchanges[eid] = getattr(ccxt, eid)({"enableRateLimit": True})
            exchanges[eid].load_markets()

        results = []
        for bex in buy_exchanges:
            for sex in sell_exchanges:
                if bex == sex:
                    continue
                ex1 = exchanges[bex]
                ex2 = exchanges[sex]

                common_markets = list(set(ex1.markets.keys()) & set(ex2.markets.keys()))
                MAX_MARKETS = 200
                common_markets = common_markets[:MAX_MARKETS]

                for m in common_markets:
                    try:
                        t1 = ex1.fetch_ticker(m, params={"timeout": 5000})
                        t2 = ex2.fetch_ticker(m, params={"timeout": 5000})
                    except Exception:
                        continue

                    ask1, bid1 = t1.get("ask"), t1.get("bid")
                    ask2, bid2 = t2.get("ask"), t2.get("bid")
                    if not ask1 or not bid1 or not ask2 or not bid2:
                        continue

                    f1 = ex1.markets[m].get("taker", 0.001)
                    f2 = ex2.markets[m].get("taker", 0.001)

                    vol1 = t1.get("baseVolume", 0) * t1.get("last", 0)
                    vol2 = t2.get("baseVolume", 0) * t2.get("last", 0)

                    profit_raw = (bid2 / ask1 - 1) * 100
                    profit_after = profit_raw - (f1*100 + f2*100)
                    if min_profit <= profit_after <= max_profit:
                        base, _ = m.split("/")
                        transfer = check_transferability(ex1, ex2, base)
                        stability = estimate_stability(f"{m}-{ex1.id}-{ex2.id}", profit_after)
                        results.append({
                            "Pair": m,
                            "Buy@": EXCHANGE_NAMES[bex],
                            "Sell@": EXCHANGE_NAMES[sex],
                            "Profit % After Fees": round(profit_after, 3),
                            "Buy Vol (24h)": format_usd(vol1),
                            "Sell Vol (24h)": format_usd(vol2),
                            "Transferable": transfer,
                            "Stability": stability
                        })

        if results:
            df = pd.DataFrame(results).sort_values("Profit % After Fees", ascending=False)
            st.subheader("Profitable Arbitrage Opportunities")
            st.dataframe(df, use_container_width=True)
            st.download_button("⬇️ Download CSV", df.to_csv(index=False), "arbitrage_opportunities.csv", "text/csv")
        else:
            st.info("No profitable opportunities in range.")

    except Exception as e:
        st.error(f"Error: {e}")

# ------------------- Auto Refresh with Countdown -------------------
if scan_now or auto_refresh:
    run_scan()
    if auto_refresh:
        countdown = st.empty()
        for i in range(20, 0, -1):
            countdown.write(f"⏳ Refreshing in {i} seconds...")
            time.sleep(1)
        st.experimental_rerun()
