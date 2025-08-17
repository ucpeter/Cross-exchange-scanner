import streamlit as st
import requests
import ccxt
import pandas as pd
import time

st.set_page_config(page_title="Cross-Exchange Arbitrage Scanner", layout="wide")
st.title("Cross-Exchange Arbitrage Scanner")

# ------------------- Fetch top 20 exchanges -------------------
@st.cache_data
def get_top_exchanges(limit=20):
    url = f"https://api.coingecko.com/api/v3/exchanges?per_page={limit}&page=1"
    r = requests.get(url, timeout=10)
    data = r.json()
    # Only keep name + id
    return [(ex["name"], ex["id"]) for ex in data]

top_exchanges = get_top_exchanges()

# ------------------- Streamlit UI -------------------
col1, col2, col3 = st.columns(3)
with col1:
    exch1_name = st.selectbox("Select Buy Exchange", [name for name, _id in top_exchanges])
with col2:
    exch2_name = st.selectbox("Select Sell Exchange", [name for name, _id in top_exchanges])
with col3:
    min_profit = st.number_input("Profit % Threshold", min_value=0.0, value=0.2, step=0.1)

# Save selected exchange ids
exch1_id = [eid for name, eid in top_exchanges if name == exch1_name][0]
exch2_id = [eid for name, eid in top_exchanges if name == exch2_name][0]

st.write(f"🔍 Ready to scan opportunities between **{exch1_name}** and **{exch2_name}** with min profit {min_profit}% ...")

# ------------------- Cache for opportunity stability -------------------
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
        # Check withdraw/deposit on at least one network
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
    cache[key] = cache[key][-5:]  # keep last 5
    if len(cache[key]) >= 3:
        if all(p > 0 for (_, p) in cache[key]):
            return "Likely Stable"
    return "Fleeting"

# ------------------- Main Scan Button -------------------
if st.button("Scan Now"):
    with st.spinner("Scanning arbitrage opportunities..."):
        try:
            # Load exchanges
            ex1 = getattr(ccxt, exch1_id)()
            ex2 = getattr(ccxt, exch2_id)()
            ex1.load_markets()
            ex2.load_markets()

            common_markets = set(ex1.markets.keys()) & set(ex2.markets.keys())
            results = []

            for m in common_markets:
                try:
                    t1 = ex1.fetch_ticker(m)
                    t2 = ex2.fetch_ticker(m)
                except Exception:
                    continue

                if not t1 or not t2:
                    continue

                ask1, bid1 = t1.get("ask"), t1.get("bid")
                ask2, bid2 = t2.get("ask"), t2.get("bid")
                if not ask1 or not bid1 or not ask2 or not bid2:
                    continue

                # Fees
                f1 = ex1.markets[m].get("taker", 0.001)
                f2 = ex2.markets[m].get("taker", 0.001)

                # Volumes in USD
                vol1 = t1.get("baseVolume", 0) * t1.get("last", 0)
                vol2 = t2.get("baseVolume", 0) * t2.get("last", 0)

                # Case 1: Buy on ex1, sell on ex2
                profit_raw = (bid2 / ask1 - 1) * 100
                profit_after = profit_raw - (f1*100 + f2*100)
                if profit_after > min_profit:
                    base, quote = m.split("/")
                    transfer = check_transferability(ex1, ex2, base)
                    stability = estimate_stability(f"{m}-{ex1.id}-{ex2.id}", profit_after)
                    results.append({
                        "Pair": m,
                        "Buy@": exch1_name,
                        "Ask": ask1,
                        "Buy Vol (24h)": format_usd(vol1),
                        "Sell@": exch2_name,
                        "Bid": bid2,
                        "Sell Vol (24h)": format_usd(vol2),
                        "Profit % Raw": round(profit_raw, 3),
                        "Profit % After Fees": round(profit_after, 3),
                        "Transferable": transfer,
                        "Stability": stability
                    })

                # Case 2: Buy on ex2, sell on ex1
                profit_raw = (bid1 / ask2 - 1) * 100
                profit_after = profit_raw - (f1*100 + f2*100)
                if profit_after > min_profit:
                    base, quote = m.split("/")
                    transfer = check_transferability(ex2, ex1, base)
                    stability = estimate_stability(f"{m}-{ex2.id}-{ex1.id}", profit_after)
                    results.append({
                        "Pair": m,
                        "Buy@": exch2_name,
                        "Ask": ask2,
                        "Buy Vol (24h)": format_usd(vol2),
                        "Sell@": exch1_name,
                        "Bid": bid1,
                        "Sell Vol (24h)": format_usd(vol1),
                        "Profit % Raw": round(profit_raw, 3),
                        "Profit % After Fees": round(profit_after, 3),
                        "Transferable": transfer,
                        "Stability": stability
                    })

            if results:
                df = pd.DataFrame(results).sort_values("Profit % After Fees", ascending=False)
                st.subheader("Profitable Arbitrage Opportunities")
                st.dataframe(df, use_container_width=True)
            else:
                st.info("No profitable opportunities above threshold.")

        except Exception as e:
            st.error(f"Error: {e}")