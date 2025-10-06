import time ,re ,ccxt ,json ,os
import pandas as pd
import streamlit as st

st .set_page_config (page_title ="Cross-Exchange Arbitrage Scanner",layout ="wide")

st .markdown ("""
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
    .stButton>button { background-color: #1976D2; color: white; border-radius: 8px; padding: 0.6em 1.2em; font-size: 16px; font-weight: 600; border: none; cursor: pointer; transition: background-color 0.3s ease; }
    .stButton>button:hover { background-color: #1565C0; }
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
    .small { color: #BDBDBD; font-size: 12px; }
    .good { color: #4CAF50; font-weight: 700; }
    .bad { color: #FF5252; font-weight: 700; }
    .spread { color: #42A5F5; font-weight: 700; }
    </style>
""",unsafe_allow_html=True)

st.title("🌍 Cross-Exchange Arbitrage Scanner")

SETTINGS_FILE = "settings.json"
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE,"r") as f:
                return json.load(f)
        except:
            return {}
    return {}
def save_settings(s):
    with open(SETTINGS_FILE,"w") as f:
        json.dump(s,f)
saved = load_settings()

TOP_20_CCXT_EXCHANGES = ["binance","okx","coinbase","kraken","bybit","kucoin","mexc","bitfinex","bitget","gateio","cryptocom","upbit","whitebit","poloniex","bingx","lbank","bitstamp","gemini","bitrue","xt","bitmart","huobi"]
EXCHANGE_NAMES = {"binance":"Binance","okx":"OKX","coinbase":"Coinbase","kraken":"Kraken","bybit":"Bybit","kucoin":"KuCoin","mexc":"MEXC","bitfinex":"Bitfinex","bitget":"Bitget","gateio":"Gate.io","cryptocom":"Crypto.com","upbit":"Upbit","whitebit":"WhiteBIT","poloniex":"Poloniex","bingx":"BingX","lbank":"LBank","bitstamp":"Bitstamp","gemini":"Gemini","bitrue":"Bitrue","xt":"XT.com","bitmart":"BitMart","huobi":"HTX"}
EXTRA_OPTS = {"bybit":{"options":{"defaultType":"spot"}},"okx":{"options":{"defaultType":"spot"}},"bingx":{"options":{"defaultType":"spot"}},"mexc":{"options":{"defaultType":"spot"}},"bitrue":{"options":{"defaultType":"spot"}},"xt":{"options":{"defaultType":"spot"}},"bitmart":{"options":{"defaultType":"spot"}},"huobi":{"options":{"defaultType":"spot"}}}
USD_QUOTES = {"USDT","USD","USDC","BUSD"}
LOW_FEE_CHAIN_PRIORITY = ["TRC20","BEP20","BSC","SOL","MATIC","ARB","OP","Polygon","TON","AVAX","ETH"]
LEV_REGEX = re.compile(r"\b(\d+[LS]|UP|DOWN|BULL|BEAR)\b",re.IGNORECASE)
MAX_SYMBOLS_PER_PAIR = 200

st.sidebar.header("Scanner Controls")
buy_exchanges = st.sidebar.multiselect("Buy Exchanges (up to 10)",TOP_20_CCXT_EXCHANGES,default=saved.get("buy_exchanges",[]),max_selections=10,format_func=lambda x:EXCHANGE_NAMES.get(x,x))
sell_exchanges = st.sidebar.multiselect("Sell Exchanges (up to 10)",TOP_20_CCXT_EXCHANGES,default=saved.get("sell_exchanges",[]),max_selections=10,format_func=lambda x:EXCHANGE_NAMES.get(x,x))
min_profit = st.sidebar.number_input("Minimum Profit % (after fees)",0.0,100.0,saved.get("min_profit",1.0),0.1)
max_profit = st.sidebar.number_input("Maximum Profit % (after fees)",0.0,200.0,saved.get("max_profit",20.0),0.1)
min_24h_vol_usd = st.sidebar.number_input("Min 24h Volume (USD)",0.0,1_000_000_000.0,saved.get("min_24h_vol_usd",100000.0),50000.0)
exclude_chains = st.sidebar.multiselect("Exclude Blockchains",["ETH","TRC20","BEP20","BSC","SOL","MATIC","ARB","OP","Polygon","TON","AVAX"],default=saved.get("exclude_chains",["ETH"]))
include_all_chains = st.sidebar.checkbox("Include all blockchains (ignore exclusion)",value=saved.get("include_all_chains",False))
auto_refresh = st.sidebar.checkbox("🔄 Auto Refresh Every 20s",value=saved.get("auto_refresh",False))
scan_now = st.button("🚀 Scan Now")
if scan_now:
    save_settings({"buy_exchanges":buy_exchanges,"sell_exchanges":sell_exchanges,"min_profit":min_profit,"max_profit":max_profit,"min_24h_vol_usd":min_24h_vol_usd,"exclude_chains":exclude_chains,"include_all_chains":include_all_chains,"auto_refresh":auto_refresh})

if "op_cache" not in st.session_state: st.session_state.op_cache = {}
if "lifetime_history" not in st.session_state: st.session_state.lifetime_history = {}
if "last_seen_keys" not in st.session_state: st.session_state.last_seen_keys = set()

def parse_symbol(s):
    try:
        return s.split("/")[0], s.split("/")[1].split(":")[0]
    except:
        return s, ""
def market_price_from_ticker(t):
    if not t: return None
    last = t.get("last")
    if last is not None:
        try: return float(last)
        except: pass
    bid, ask = t.get("bid"), t.get("ask")
    if bid and ask:
        try: return (float(bid) + float(ask)) / 2
        except: return None
    return None
def normalize_symbol(s):
    try:
        return s.split(":")[0].upper().replace('\u2010','-').replace('\u2013','-').strip() if isinstance(s,str) else s
    except: return s
def build_symbol_map(ex):
    m = {}
    try:
        for mk in getattr(ex,"markets",{}) or {}:
            m.setdefault(normalize_symbol(mk), []).append(mk)
    except: pass
    return m
def safe_fetch_tickers(ex, ex_id, symbols=None):
    try:
        if symbols: return ex.fetch_tickers(symbols) or {}
        if ex_id == "upbit":
            syms = list(getattr(ex,"symbols",[]) or []); out = {}
            for i in range(0, len(syms), 200):
                try: out.update(ex.fetch_tickers(syms[i:i+200]) or {})
                except: break
            return out
        try: return ex.fetch_tickers() or {}
        except:
            syms = list(getattr(ex,"symbols",[]) or []); out = {}
            for i in range(0, min(len(syms), 600), 200):
                try: out.update(ex.fetch_tickers(syms[i:i+200]) or {})
                except: break
            return out
    except: return {}
def safe_ccxt_id(e): return {"coinbaseexchange":"coinbase","xtcom":"xt","crypto_com":"cryptocom","crypto.com":"cryptocom","coinbasepro":"coinbase","htx":"huobi"}.get(e,e)
def is_ticker_fresh(t, age=300):
    if not t: return True
    ts = t.get("timestamp")
    if ts is None: return True
    now = int(time.time()*1000)
    try: return (now - int(ts)) <= age*1000
    except: return True
def fmt_usd(x):
    try:
        x = float(x or 0)
        if x >= 1e9: return f"${x/1e9:.2f}B"
        if x >= 1e6: return f"${x/1e6:.2f}M"
        if x >= 1e3: return f"${x/1e3:.0f}K"
        return f"${x:,.0f}"
    except: return "$0"
def secs_to_label(s): return f"{int(s)}s" if s < 90 else f"{s/60:.1f}m"
def update_lifetime_for_disappeared(keys):
    gone = st.session_state.last_seen_keys - set(keys)
    for k in gone:
        trail = st.session_state.op_cache.get(k, [])
        if trail:
            d = trail[-1][0] - trail[0][0]
            if d > 0: st.session_state.lifetime_history.setdefault(k, []).append(d)
    st.session_state.last_seen_keys = set(keys)
def stability_and_expiry(k, p):
    now = time.time(); trail = st.session_state.op_cache.get(k, [])
    if not trail: st.session_state.op_cache[k] = [(now, p)]; return "⏳ new", "~unknown"
    trail.append((now, p)); st.session_state.op_cache[k] = trail[-30:]
    d = trail[-1][0] - trail[0][0]; obs = f"⏳ {secs_to_label(d)} observed"; hist = st.session_state.lifetime_history.get(k, [])
    if not hist: exp = "~unknown"
    else:
        avg = sum(hist) / len(hist); rem = avg - d; exp = "⚠️ past avg" if rem <= 0 else f"~{secs_to_label(rem)} left"
    return obs, exp
    INFO_VOL_KEYS = ["quoteVolume","baseVolume","vol","vol24h","volCcy24h","volValue","turnover","turnover24h","quoteVolume24h","amount","value","acc_trade_price_24h","quote_volume_24h","base_volume_24h"]
def safe_usd_volume(eid, sym, t, px, tks):
    try:
        b, q = parse_symbol(sym); qU = q.upper(); qv = t.get("quoteVolume") if t else None; bv = t.get("baseVolume") if t else None
        if qU in USD_QUOTES and qv: return float(qv)
        if bv and px: return float(bv) * float(px)
        info = (t.get("info") if t else {}) or {}; raw = None
        for k in INFO_VOL_KEYS:
            v = info.get(k)
            if v is None: continue
            try:
                fv = float(v)
                if fv > 0: raw = fv; break
            except: continue
        if raw is not None:
            if qU in USD_QUOTES: return float(raw)
            conv = f"{qU}/USDT"; ct = tks.get(conv); cp = market_price_from_ticker(ct)
            if cp: return float(raw) * float(cp)
        if qv:
            conv = f"{qU}/USDT"; ct = tks.get(conv); cp = market_price_from_ticker(ct)
            if cp: return float(qv) * float(cp)
        return 0.0
    except: return 0.0
def symbol_ok(ex, s):
    try:
        m = ex.markets.get(s, {})
        if not m or not m.get("spot", True): return False
        b, q = parse_symbol(s)
        if q.upper() not in USD_QUOTES or LEV_REGEX.search(s) or m.get("active") is False: return False
        return True
    except: return False
def choose_common_chain(e1, e2, c, excl, inc_all):
    try:
        c1 = e1.currencies.get(c, {}) or {}; c2 = e2.currencies.get(c, {}) or {}
        n1 = c1.get("networks", {}) or {}; n2 = c2.get("networks", {}) or {}
        com = set(n1.keys()) & set(n2.keys())
        if not com: return "❌ No chain", "❌", "❌"
        pref = [n for n in LOW_FEE_CHAIN_PRIORITY if (inc_all or n not in excl)]; best = None
        for p in pref:
            if p in com: best = p; break
        if not best:
            cand = sorted(list(com))[0]
            if not inc_all and cand in excl: return "❌ No chain", "❌", "❌"
            best = cand
        return best, "✅" if n1.get(best, {}).get("withdraw") else "❌", "✅" if n2.get(best, {}).get("deposit") else "❌"
    except: return "❌ Unknown", "❌", "❌"

def run_scan():
    if not buy_exchanges or not sell_exchanges: st.warning("Please select at least one Buy and one Sell exchange."); return
    try:
        exs = {}
        for eid in set(buy_exchanges + sell_exchanges):
            try:
                o = {"enableRateLimit": True, "timeout": 12000}; o.update(EXTRA_OPTS.get(eid, {}))
                sid = safe_ccxt_id(eid)
                if not hasattr(ccxt, sid): st.warning(f"⚠️ ccxt has no attribute '{eid}'. Skipping."); continue
                ex = getattr(ccxt, sid)(o)
                try: ex.load_markets()
                except Exception as e: st.warning(f"⚠️ {EXCHANGE_NAMES.get(eid, sid)} load_markets issue: {e}")
                exs[eid] = ex
            except Exception as e: st.warning(f"⚠️ Could not instantiate {eid}: {e}"); continue
        tks = {}; results = []; keys = []
        for eid, ex in exs.items():
            try: tks[eid] = safe_fetch_tickers(ex, eid)
            except Exception as e: st.warning(f"⚠️ {EXCHANGE_NAMES.get(eid, eid)} fetch_tickers failed: {e}"); tks[eid] = {}
        for b_id in buy_exchanges:
            for s_id in sell_exchanges:
                if b_id == s_id: continue
                b_ex, s_ex = exs[b_id], exs[s_id]; btks, stks = tks[b_id], tks[s_id]
                bmap, smap = build_symbol_map(b_ex), build_symbol_map(s_ex)
                common = list(set(bmap.keys()) & set(smap.keys()))[:MAX_SYMBOLS_PER_PAIR]
                for nsym in common:
                    try:
                        bc = bmap.get(nsym, []); sc = smap.get(nsym, [])
                        bm = bc[0] if bc else None; sm = sc[0] if sc else None
                        if not bm or not sm: continue
                        bt, st_ = btks.get(bm), stks.get(sm)
                        if not bt or not st_ or not is_ticker_fresh(bt) or not is_ticker_fresh(st_): continue
                        sym = bm; bp = market_price_from_ticker(bt); sp = market_price_from_ticker(st_)
                        if not bp:
                            try: ob = b_ex.fetch_order_book(sym, limit=5); bp = (ob.get("bids")[0][0] + ob.get("asks")[0][0]) / 2 if ob.get("bids") and ob.get("asks") else None
                            except: bp = None
                        if not sp:
                            try: ob = s_ex.fetch_order_book(sm, limit=5); sp = (ob.get("bids")[0][0] + ob.get("asks")[0][0]) / 2 if ob.get("bids") and ob.get("asks") else None
                            except: sp = None
                        if not bp or not sp: continue
                        gap = abs(sp - bp) / bp
                        if gap > 0.5: continue
                        bf = b_ex.markets.get(sym, {}).get("taker", 0.001) or 0.001; sf = s_ex.markets.get(sm, {}).get("taker", 0.001) or 0.001
                        spread = (sp - bp) / bp * 100; profit = spread - (bf * 100 + sf * 100)
                        if profit < min_profit or profit > max_profit: continue
                        bv = safe_usd_volume(b_id, sym, bt or {}, bp, btks); sv = safe_usd_volume(s_id, sm, st_ or {}, sp, stks)
                        if (not bt or bv <= 0) and bv < min_24h_vol_usd:
                            try: ob = b_ex.fetch_order_book(sym, limit=8); bv = sum([float(p) * float(q) for p, q in ob.get("asks", [])[:8]])
                            except: bv = 0
                        if (not st_ or sv <= 0) and sv < min_24h_vol_usd:
                            try: ob = s_ex.fetch_order_book(sm, limit=8); sv = sum([float(p) * float(q) for p, q in ob.get("bids", [])[:8]])
                            except: sv = 0
                        if bv < min_24h_vol_usd or sv < min_24h_vol_usd: continue
                        base, quote = parse_symbol(sym); chain, w, d = choose_common_chain(b_ex, s_ex, base, exclude_chains, include_all_chains)
                        if not include_all_chains and (chain in exclude_chains or str(chain).startswith("❌")): continue
                        if w != "✅" or d != "✅": continue
                        k = f"{nsym}|{b_id}>{s_id}"; keys.append(k); obs, exp = stability_and_expiry(k, profit)
                        results.append({"#":None,"Pair":nsym,"Quote":quote,"Buy@":EXCHANGE_NAMES.get(b_id,b_id),"Buy Price":round(float(bp),10),"Sell@":EXCHANGE_NAMES.get(s_id,s_id),"Sell Price":round(float(sp),10),"Spread %":round(spread,4),"Profit % After Fees":round(profit,4),"Buy Vol (24h)":fmt_usd(bv),"Sell Vol (24h)":fmt_usd(sv),"Withdraw?":w,"Deposit?":d,"Blockchain":chain,"Stability":obs,"Est. Expiry":exp})
                    except: continue
        update_lifetime_for_disappeared(keys)
        if results:
            df = pd.DataFrame(results).sort_values(["Profit % After Fees","Spread %"],ascending=False).reset_index(drop=True); df["#"] = range(1, len(df)+1)
           def pill(v,ok=True): cls="pill-green" if ok else "pill-red"; return '<span class="pill '+cls+'">'+str(v)+'</span>'
           def cpr(p): return f'<span class="good mono">{p:.4f}%</span>' if p>=0 else f'<span class="bad mono">{p:.4f}%</span>'
           def cs(s): return f'<span class="spread mono">{s:.4f}%</span>'
            headers = ["#","Pair","Quote","Buy@","Buy Price","Sell@","Sell Price","Spread %","Profit % After Fees","Buy Vol (24h)","Sell Vol (24h)","Withdraw?","Deposit?","Blockchain","Stability","Est. Expiry"]
            html = '<div class="table-wrap"><table class="arb-table"><thead><tr>' + "".join([f"<th>{h}</th>" for h in headers]) + "</tr></thead><tbody>"
            for _, r in df.iterrows():
                html += "<tr>" + f'<td class="num mono">{int(r["#"])}</td>' + f'<td class="mono">{r["Pair"]}</td>' + f'<td>{r["Quote"]}</td>' + f'<td>{r["Buy@"]}</td>' + f'<td class="num mono">{r["Buy Price"]}</td>' + f'<td>{r["Sell@"]}</td>' + f'<td class="num mono">{r["Sell Price"]}</td>' + f'<td class="num">{cs(r["Spread %"])}</td>' + f'<td class="num">{cpr(r["Profit % After Fees"])}</td>' + f'<td class="num mono">{r["Buy Vol (24h)"]}</td>' + f'<td class="num mono">{r["Sell Vol (24h)"]}</td>' + f'<td>{pill("✅",True) if r["Withdraw?"]=="✅" else pill("❌",False)}</td>' + f'<td>{pill("✅",True) if r["Deposit?"]=="✅" else pill("❌",False)}</td>' + f'<td><span class="pill pill-blue">{r["Blockchain"]}</span></td>' + f'<td class="small">{r["Stability"]}</td>' + f'<td class="small">{r["Est. Expiry"]}</td>' + "</tr>"
            html += "</tbody></table></div>"
            st.subheader("✅ Profitable Arbitrage Opportunities")
            st.markdown(html, unsafe_allow_html=True)
            st.download_button("⬇️ Download CSV", df.to_csv(index=False), "arbitrage_opportunities.csv", "text/csv")
        else:
            st.info("No opportunities matched your profit/volume/chain filters right now.")
    except Exception as e:
        st.error(f"Error: {e}")

if scan_now or auto_refresh:
    with st.spinner("🔍 Scanning exchanges…"): run_scan()
    if auto_refresh:
        h = st.empty()
        for i in range(20,0,-1): h.write(f"⏳ Refreshing in {i}s…"); time.sleep(1)
        st.experimental_rerun()
