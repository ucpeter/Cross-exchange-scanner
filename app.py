import time,re,ccxt,json,os
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Cross-Exchange Arbitrage Scanner", layout="wide")
st.markdown("""<style>body,.stApp{background:#111;color:#E0E0E0}.stSidebar{background:#1A1A1A!important}.stDataFrame th{background:#222!important;color:#EEE!important;font-weight:600}.stDataFrame td{color:#EEE!important}.stDataFrame tbody tr:nth-child(even){background:#1E1E1E!important}.stDataFrame tbody tr:hover{background:#2A2A2A!important}.good{color:#4CAF50;font-weight:600}.bad{color:#FF5252;font-weight:600}.spread{color:#42A5F5;font-weight:600}.stButton>button{background:#1976D2;color:#fff;border-radius:8px;padding:.6em 1.2em;font-size:16px;border:none}.pill{padding:2px 10px;border-radius:999px;font-weight:700;font-size:12px}.pill-green{background:#1B5E20;color:#E8F5E9}.pill-red{background:#7F1D1D;color:#FEE2E2}.pill-blue{background:#0D47A1;color:#E3F2FD}.table-wrap{overflow-x:auto;border-radius:10px;border:1px solid #2A2A2A}table.arb-table{width:100%;border-collapse:collapse}table.arb-table th,table.arb-table td{padding:8px 10px;border-bottom:1px solid #222}table.arb-table th{background:#1D1D1D;text-align:left}table.arb-table tr:nth-child(even){background:#161616}table.arb-table tr:hover{background:#202020}.num{text-align:right;white-space:nowrap}.mono{font-variant-numeric:tabular-nums;font-family:ui-monospace,Menlo,Consolas,monospace}.small{color:#BDBDBD;font-size:12px}</style>""", unsafe_allow_html=True)
st.title("🌍 Cross-Exchange Arbitrage Scanner")

SETTINGS_FILE = "settings.json"
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try: return json.load(open(SETTINGS_FILE))
        except: return {}
    return {}
def save_settings(s): json.dump(s, open(SETTINGS_FILE, "w"))
saved = load_settings()

TOP_EXCHANGES = ["binance","okx","coinbase","kraken","bybit","kucoin","mexc","bitfinex","bitget","gateio","cryptocom","upbit","whitebit","poloniex","bingx","lbank","bitstamp","gemini","bitrue","xt","bitmart","huobi"]
EXCHANGE_NAMES = {"binance":"Binance","okx":"OKX","coinbase":"Coinbase","kraken":"Kraken","bybit":"Bybit","kucoin":"KuCoin","mexc":"MEXC","bitfinex":"Bitfinex","bitget":"Bitget","gateio":"Gate.io","cryptocom":"Crypto.com","upbit":"Upbit","whitebit":"WhiteBIT","poloniex":"Poloniex","bingx":"BingX","lbank":"LBank","bitstamp":"Bitstamp","gemini":"Gemini","bitrue":"Bitrue","xt":"XT.com","bitmart":"BitMart","huobi":"HTX"}
EXTRA_OPTS = {"bybit":{"options":{"defaultType":"spot"}},"okx":{"options":{"defaultType":"spot"}},"bingx":{"options":{"defaultType":"spot"}},"mexc":{"options":{"defaultType":"spot"}},"bitrue":{"options":{"defaultType":"spot"}},"xt":{"options":{"defaultType":"spot"}},"bitmart":{"options":{"defaultType":"spot"}},"huobi":{"options":{"defaultType":"spot"}}}
USD_QUOTES = {"USDT","USD","USDC","BUSD"}
LOW_FEE_CHAIN_PRIORITY = ["TRC20","BEP20","BSC","SOL","MATIC","ARB","OP","Polygon","TON","AVAX","ETH"]
LEV_PATTERNS = [r"\b\d+[LS]\b", r"\bUP\b", r"\bDOWN\b", r"\bBULL\b", r"\bBEAR\b"]
LEV_REGEX = re.compile("|".join(LEV_PATTERNS), re.IGNORECASE)
INFO_VOL_KEYS = ["quoteVolume","baseVolume","vol","vol24h","volCcy24h","volValue","turnover","turnover24h","quoteVolume24h","amount","value","acc_trade_price_24h","quote_volume_24h","base_volume_24h"]

st.sidebar.header("Scanner Controls")
buy_exchanges = st.sidebar.multiselect("Buy Exchanges (up to 10)", TOP_EXCHANGES, default=saved.get("buy_exchanges", []), max_selections=10, format_func=lambda x: EXCHANGE_NAMES.get(x,x))
sell_exchanges = st.sidebar.multiselect("Sell Exchanges (up to 10)", TOP_EXCHANGES, default=saved.get("sell_exchanges", []), max_selections=10, format_func=lambda x: EXCHANGE_NAMES.get(x,x))
min_profit = st.sidebar.number_input("Minimum Profit % (after fees)", 0.0, 100.0, saved.get("min_profit", 1.0), 0.1)
max_profit = st.sidebar.number_input("Maximum Profit % (after fees)", 0.0, 200.0, saved.get("max_profit", 20.0), 0.1)
min_24h_vol_usd = st.sidebar.number_input("Min 24h Volume (USD)", 0.0, 1e9, saved.get("min_24h_vol_usd", 100000.0), 50000.0)
exclude_chains = st.sidebar.multiselect("Exclude Blockchains", ["ETH","TRC20","BEP20","BSC","SOL","MATIC","ARB","OP","Polygon","TON","AVAX"], default=saved.get("exclude_chains", ["ETH"]))
include_all_chains = st.sidebar.checkbox("Include all blockchains (ignore exclusion)", value=saved.get("include_all_chains", False))
auto_refresh = st.sidebar.checkbox("🔄 Auto Refresh Every 20s", value=saved.get("auto_refresh", False))
scan_now = st.button("🚀 Scan Now")
if scan_now: save_settings({"buy_exchanges":buy_exchanges,"sell_exchanges":sell_exchanges,"min_profit":min_profit,"max_profit":max_profit,"min_24h_vol_usd":min_24h_vol_usd,"exclude_chains":exclude_chains,"include_all_chains":include_all_chains,"auto_refresh":auto_refresh})

if "op_cache" not in st.session_state: st.session_state.op_cache = {}
if "lifetime_history" not in st.session_state: st.session_state.lifetime_history = {}
if "last_seen_keys" not in st.session_state: st.session_state.last_seen_keys = set()

def normalize_symbol(sym):
    if not isinstance(sym, str): return sym
    s = sym.split(":")[0]
    s = s.replace("‐", "-").replace("—","-").strip()  # normalize dashes
    return s.upper()

def parse_symbol(sym):
    try:
        base = sym.split("/")[0]
        quote = sym.split("/")[1].split(":")[0]
        return base, quote
    except:
        return sym, ""

def market_price_from_ticker(t):
    if not t: return None
    last = t.get("last")
    if last is not None:
        try: return float(last)
        except: pass
    bid, ask = t.get("bid"), t.get("ask")
    if bid is not None and ask is not None:
        try: return (float(bid)+float(ask))/2.0
        except: return None
    return None

def is_ticker_fresh(t, max_age_sec=300):
    if not t: return True
    ts = t.get("timestamp")
    if ts is None: return True
    now = int(time.time()*1000)
    try: return (now - int(ts)) <= max_age_sec*1000
    except: return True

def fmt_usd(x):
    try:
        x = float(x or 0)
        if x>=1e9: return f"${x/1e9:.2f}B"
        if x>=1e6: return f"${x/1e6:.2f}M"
        if x>=1e3: return f"${x/1e3:.0f}K"
        return f"${x:,.0f}"
    except: return "$0"

def secs_to_label(secs): return f"{int(secs)}s" if secs<90 else f"{secs/60:.1f}m"

def update_lifetime_for_disappeared(current_keys):
    gone = st.session_state.last_seen_keys - set(current_keys)
    for key in gone:
        trail = st.session_state.op_cache.get(key, [])
        if trail:
            duration = trail[-1][0] - trail[0][0]
            if duration>0: st.session_state.lifetime_history.setdefault(key,[]).append(duration)
    st.session_state.last_seen_keys = set(current_keys)

def stability_and_expiry(key, current_profit):
    now = time.time()
    trail = st.session_state.op_cache.get(key, [])
    if not trail:
        st.session_state.op_cache[key] = [(now, current_profit)]
        return "⏳ new", "~unknown"
    trail.append((now, current_profit)); st.session_state.op_cache[key] = trail[-30:]
    duration = trail[-1][0] - trail[0][0]; observed = f"⏳ {secs_to_label(duration)} observed"
    hist = st.session_state.lifetime_history.get(key, [])
    if not hist: expiry = "~unknown"
    else:
        avg = sum(hist)/len(hist); rem = avg-duration; expiry = "⚠️ past avg" if rem<=0 else f"~{secs_to_label(rem)} left"
    return observed, expiry

def safe_usd_volume(ex_id, symbol, ticker, price, all_tickers):
    try:
        base, quote = parse_symbol(symbol); qU = quote.upper()
        qvol = ticker.get("quoteVolume") if ticker else None
        bvol = ticker.get("baseVolume") if ticker else None
        if qU in USD_QUOTES and qvol: return float(qvol)
        if bvol and price: return float(bvol)*float(price)
        info = (ticker.get("info") if ticker else {}) or {}
        raw = None
        for k in INFO_VOL_KEYS:
            v = info.get(k)
            if v:
                try: f = float(v)
                except: continue
                if f>0: raw=f; break
        if raw is not None:
            if qU in USD_QUOTES: return float(raw)
            conv = f"{qU}/USDT"; conv_t = all_tickers.get(conv); conv_px = market_price_from_ticker(conv_t)
            if conv_px: return float(raw)*float(conv_px)
        if qvol:
            conv = f"{qU}/USDT"; conv_t = all_tickers.get(conv); conv_px = market_price_from_ticker(conv_t)
            if conv_px: return float(qvol)*float(conv_px)
        return 0.0
    except: return 0.0

def symbol_ok(ex_obj, symbol):
    try:
        m = ex_obj.markets.get(symbol, {})
        if not m: return False
        if not m.get("spot", True): return False
        base, quote = parse_symbol(symbol)
        if quote.upper() not in USD_QUOTES: return False
        if LEV_REGEX.search(symbol): return False
        if m.get("active") is False: return False
        return True
    except: return False

def choose_common_chain(ex1, ex2, coin, exclude_chains, include_all_chains):
    try:
        c1 = ex1.currencies.get(coin, {}) or {}; c2 = ex2.currencies.get(coin, {}) or {}
        nets1 = c1.get("networks", {}) or {}; nets2 = c2.get("networks", {}) or {}
        common = set(nets1.keys()) & set(nets2.keys())
        if not common: return "❌ No chain","❌","❌"
        preferred = [n for n in LOW_FEE_CHAIN_PRIORITY if (include_all_chains or n not in exclude_chains)]
        best = None
        for p in preferred:
            if p in common: best = p; break
        if not best:
            cand = sorted(list(common))[0]
            if not include_all_chains and cand in exclude_chains: return "❌ No chain","❌","❌"
            best = cand
        w_ok = "✅" if nets1.get(best,{}).get("withdraw") else "❌"
        d_ok = "✅" if nets2.get(best,{}).get("deposit") else "❌"
        return best, w_ok, d_ok
    except: return "❌ Unknown","❌","❌"

def build_symbol_map(ex):
    m = {}
    try:
        for mk in getattr(ex, "markets", {}) or {}:
            n = normalize_symbol(mk)
            m.setdefault(n, []).append(mk)
    except: pass
    return m

def choose_best_market(market_list):
    if not market_list: return None
    for mk in market_list:
        try:
            _, q = parse_symbol(mk)
            if q.upper() in USD_QUOTES: return mk
        except: continue
    return market_list[0]

def try_alias_for_exid(ex_id):
    if hasattr(ccxt, ex_id): return ex_id
    # alias attempts
    aliases = {"coinbaseexchange":"coinbase","xtcom":"xt","crypto_com":"cryptocom","htx":"huobi"}
    if ex_id in aliases and hasattr(ccxt, aliases[ex_id]): return aliases[ex_id]
    # fallback: return original (will be caught later)
    return ex_id

def instantiate_exchange(ex_id):
    safe_id = try_alias_for_exid(ex_id)
    if not hasattr(ccxt, safe_id):
        raise AttributeError(f"ccxt has no attribute '{ex_id}' or alias '{safe_id}'")
    opts = {"enableRateLimit": True, "timeout": 12000}
    opts.update(EXTRA_OPTS.get(safe_id, {}))
    return getattr(ccxt, safe_id)(opts)

def run_scan():
    if not buy_exchanges or not sell_exchanges:
        st.warning("Please select at least one Buy and one Sell exchange.")
        return
    ex_objs = {}
    symbol_maps = {}
    # instantiate exchanges
    for ex_id in set(buy_exchanges + sell_exchanges):
        try:
            ex = instantiate_exchange(ex_id)
            try: ex.load_markets()
            except Exception as e: st.warning(f"⚠️ {EXCHANGE_NAMES.get(ex_id,ex_id)} load_markets issue: {e}")
            ex_objs[ex_id] = ex
            symbol_maps[ex_id] = build_symbol_map(ex)
        except Exception as e:
            st.warning(f"⚠️ Skipping {ex_id}: {e}")
    if not ex_objs:
        st.error("No exchanges available after initialization.")
        return
    # fetch tickers with upbit chunking and fallbacks
    bulk = {}
    for ex_id, ex in ex_objs.items():
        try:
            if ex_id == "upbit":
                syms = list(getattr(ex, "symbols", []) or [])
                bulk[ex_id] = {}
                for i in range(0, len(syms), 200):
                    try: bulk[ex_id].update(ex.fetch_tickers(syms[i:i+200]))
                    except Exception as e: st.warning(f"⚠️ {EXCHANGE_NAMES.get(ex_id,ex_id)} chunk fetch failed: {e}"); break
            else:
                try: tks = ex.fetch_tickers() or {}; bulk[ex_id] = tks if isinstance(tks, dict) else {}
                except Exception:
                    syms = list(getattr(ex, "symbols", []) or []); b={}
                    for i in range(0, min(len(syms), 600), 200):
                        try: b.update(ex.fetch_tickers(syms[i:i+200]))
                        except: break
                    bulk[ex_id] = b; st.warning(f"⚠️ {EXCHANGE_NAMES.get(ex_id,ex_id)} fetch_tickers fallback used.")
        except Exception as e:
            st.warning(f"⚠️ {EXCHANGE_NAMES.get(ex_id,ex_id)} fetch_tickers failed: {e}")
            bulk[ex_id] = {}
    results = []
    current_keys = []
    # pairwise compare using normalized symbol intersection
    for buy_id in buy_exchanges:
        for sell_id in sell_exchanges:
            if buy_id == sell_id: continue
            buy_ex = ex_objs.get(buy_id); sell_ex = ex_objs.get(sell_id)
            if not buy_ex or not sell_ex: continue
            buy_map = symbol_maps.get(buy_id, {}); sell_map = symbol_maps.get(sell_id, {})
            common = set(buy_map.keys()) & set(sell_map.keys())
            for norm_sym in list(common)[:700]:
                try:
                    buy_candidates = buy_map.get(norm_sym, [])
                    sell_candidates = sell_map.get(norm_sym, [])
                    buy_mk = choose_best_market(buy_candidates)
                    sell_mk = choose_best_market(sell_candidates)
                    if not buy_mk or not sell_mk: continue
                    bt = bulk.get(buy_id, {}).get(buy_mk)
                    stt = bulk.get(sell_id, {}).get(sell_mk)
                    if bt and not is_ticker_fresh(bt): continue
                    if stt and not is_ticker_fresh(stt): continue
                    buy_px = market_price_from_ticker(bt) if bt else None
                    if buy_px is None:
                        try: ob = buy_ex.fetch_order_book(buy_mk, limit=5); buy_px = (ob.get("bids")[0][0]+ob.get("asks")[0][0])/2 if ob.get("bids") and ob.get("asks") else None
                        except: buy_px = None
                    sell_px = market_price_from_ticker(stt) if stt else None
                    if sell_px is None:
                        try: ob = sell_ex.fetch_order_book(sell_mk, limit=5); sell_px = (ob.get("bids")[0][0]+ob.get("asks")[0][0])/2 if ob.get("bids") and ob.get("asks") else None
                        except: sell_px = None
                    if not buy_px or not sell_px: continue
                    gap = abs(sell_px - buy_px) / buy_px
                    if gap > 0.5: continue
                    buy_fee = buy_ex.markets.get(buy_mk, {}).get("taker", 0.001) or 0.001
                    sell_fee = sell_ex.markets.get(sell_mk, {}).get("taker", 0.001) or 0.001
                    spread = (sell_px - buy_px) / buy_px * 100.0
                    profit_after = spread - (buy_fee*100.0 + sell_fee*100.0)
                    if profit_after < min_profit or profit_after > max_profit: continue
                    buy_vol_usd = safe_usd_volume(buy_id, buy_mk, bt or {}, buy_px, bulk.get(buy_id, {}))
                    sell_vol_usd = safe_usd_volume(sell_id, sell_mk, stt or {}, sell_px, bulk.get(sell_id, {}))
                    if (not bt or buy_vol_usd<=0) and buy_vol_usd<min_24h_vol_usd:
                        try:
                            ob = buy_ex.fetch_order_book(buy_mk, limit=8); buy_vol_usd = sum([float(p)*float(q) for p,q in ob.get("asks",[])[:8]])
                        except: buy_vol_usd = 0
                    if (not stt or sell_vol_usd<=0) and sell_vol_usd<min_24h_vol_usd:
                        try:
                            ob = sell_ex.fetch_order_book(sell_mk, limit=8); sell_vol_usd = sum([float(p)*float(q) for p,q in ob.get("bids",[])[:8]])
                        except: sell_vol_usd = 0
                    if buy_vol_usd < min_24h_vol_usd or sell_vol_usd < min_24h_vol_usd: continue
                    base, quote = parse_symbol(buy_mk)
                    chain, w_ok, d_ok = choose_common_chain(buy_ex, sell_ex, base, exclude_chains, include_all_chains)
                    if not include_all_chains and (chain in exclude_chains or (isinstance(chain,str) and chain.startswith("❌"))): continue
                    if w_ok != "✅" or d_ok != "✅": continue
                    key = f"{norm_sym}|{buy_id}>{sell_id}"
                    current_keys.append(key)
                    observed, expiry = stability_and_expiry(key, profit_after)
                    results.append({"#":None,"Pair":norm_sym,"Quote":quote,"Buy@":EXCHANGE_NAMES.get(buy_id,buy_id),"Buy Price":round(float(buy_px),10),"Sell@":EXCHANGE_NAMES.get(sell_id,sell_id),"Sell Price":round(float(sell_px),10),"Spread %":round(spread,4),"Profit % After Fees":round(profit_after,4),"Buy Vol (24h)":fmt_usd(buy_vol_usd),"Sell Vol (24h)":fmt_usd(sell_vol_usd),"Withdraw?":w_ok,"Deposit?":d_ok,"Blockchain":chain,"Stability":observed,"Est. Expiry":expiry})
                except Exception:
                    continue
    update_lifetime_for_disappeared(current_keys)
    if results:
        df = pd.DataFrame(results).sort_values(["Profit % After Fees","Spread %"], ascending=False).reset_index(drop=True); df["#"]=range(1,len(df)+1)
        def pill(v,ok=True): return f'<span class="pill {"pill-green" if ok else "pill-red"}">{v}</span>'
        def color_profit(p): return f'<span class="good mono">{p:.4f}%</span>' if p>=0 else f'<span class="bad mono">{p:.4f}%</span>'
        def color_spread(s): return f'<span class="spread mono">{s:.4f}%</span>'
        headers=["#","Pair","Quote","Buy@","Buy Price","Sell@","Sell Price","Spread %","Profit % After Fees","Buy Vol (24h)","Sell Vol (24h)","Withdraw?","Deposit?","Blockchain","Stability","Est. Expiry"]
        html='<div class="table-wrap"><table class="arb-table"><thead><tr>'
        for h in headers: html+=f"<th>{h}</th>"
        html+="</tr></thead><tbody>"
        for _,r in df.iterrows():
            html+="<tr>"
            html+=f'<td class="num mono">{int(r["#"])}</td>'
            html+=f'<td class="mono">{r["Pair"]}</td>'
            html+=f'<td>{r["Quote"]}</td>'
            html+=f'<td>{r["Buy@"]}</td>'
            html+=f'<td class="num mono">{r["Buy Price"]}</td>'
            html+=f'<td>{r["Sell@"]}</td>'
            html+=f'<td class="num mono">{r["Sell Price"]}</td>'
            html+=f'<td class="num">{color_spread(r["Spread %"])}</td>'
            html+=f'<td class="num">{color_profit(r["Profit % After Fees"])}</td>'
            html+=f'<td class="num mono">{r["Buy Vol (24h)"]}</td>'
            html+=f'<td class="num mono">{r["Sell Vol (24h)"]}</td>'
            html+=f'<td>{pill("✅",True) if r["Withdraw?"]=="✅" else pill("❌",False)}</td>'
            html+=f'<td>{pill("✅",True) if r["Deposit?"]=="✅" else pill("❌",False)}</td>'
            html+=f'<td><span class="pill pill-blue">{r["Blockchain"]}</span></td>'
            html+=f'<td class="small">{r["Stability"]}</td>'
            html+=f'<td class="small">{r["Est. Expiry"]}</td>'
            html+="</tr>"
        html+="</tbody></table></div>"
        st.subheader("✅ Profitable Arbitrage Opportunities"); st.markdown(html, unsafe_allow_html=True)
        st.download_button("⬇️ Download CSV", df.to_csv(index=False), "arbitrage_opportunities.csv", "text/csv")
    else:
        st.info("No opportunities matched your profit/volume/chain filters right now.")

if scan_now or auto_refresh:
    with st.spinner("🔍 Scanning exchanges…"): run_scan()
    if auto_refresh:
        holder = st.empty()
        for i in range(20,0,-1): holder.write(f"⏳ Refreshing in {i}s…"); time.sleep(1)
        st.rerun()
