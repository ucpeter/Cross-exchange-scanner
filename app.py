import time,re,ccxt,json,os
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Cross-Exchange Arbitrage Scanner", layout="wide")
st.markdown("""<style>body,.stApp{background-color:#111111;color:#E0E0E0}.stSidebar{background-color:#1A1A1A!important}.stDataFrame th{background-color:#222!important;color:#EEE!important;font-weight:600}.stDataFrame td{color:#EEE!important}.stDataFrame tbody tr:nth-child(even){background-color:#1E1E1E!important}.stDataFrame tbody tr:hover{background-color:#2A2A2A!important}.good{color:#4CAF50;font-weight:600}.bad{color:#FF5252;font-weight:600}.spread{color:#42A5F5;font-weight:600}.stButton>button{background-color:#1976D2;color:white;border-radius:8px;padding:0.6em 1.2em;font-size:16px;font-weight:600;border:none;cursor:pointer;transition:background-color .3s}.stButton>button:hover{background-color:#1565C0}.pill{padding:2px 10px;border-radius:999px;font-weight:700;font-size:12px}.pill-green{background:#1B5E20;color:#E8F5E9;border:1px solid #2E7D32}.pill-red{background:#7F1D1D;color:#FEE2E2;border:1px solid #991B1B}.pill-blue{background:#0D47A1;color:#E3F2FD;border:1px solid #1565C0}.table-wrap{overflow-x:auto;border-radius:10px;border:1px solid #2A2A2A}table.arb-table{width:100%;border-collapse:collapse}table.arb-table th,table.arb-table td{padding:8px 10px;border-bottom:1px solid #222}table.arb-table th{background:#1D1D1D;text-align:left}table.arb-table tr:nth-child(even){background:#161616}table.arb-table tr:hover{background:#202020}.num{text-align:right;white-space:nowrap}.mono{font-variant-numeric:tabular-nums;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}.small{color:#BDBDBD;font-size:12px}</style>""", unsafe_allow_html=True)
st.title("🌍 Cross-Exchange Arbitrage Scanner")

SETTINGS_FILE="settings.json"
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE,"r") as f: return json.load(f)
        except: return {}
    return {}
def save_settings(s):
    with open(SETTINGS_FILE,"w") as f: json.dump(s,f)
saved=load_settings()

TOP_20_CCXT_EXCHANGES=["binance","okx","coinbaseexchange","kraken","bybit","kucoin","mexc","bitfinex","bitget","gateio","cryptocom","upbit","whitebit","poloniex","bingx","lbank","bitstamp","gemini","bitrue","xtcom","bitmart","htx"]
EXCHANGE_NAMES={"binance":"Binance","okx":"OKX","coinbaseexchange":"Coinbase","kraken":"Kraken","bybit":"Bybit","kucoin":"KuCoin","mexc":"MEXC","bitfinex":"Bitfinex","bitget":"Bitget","gateio":"Gate.io","cryptocom":"Crypto.com","upbit":"Upbit","whitebit":"WhiteBIT","poloniex":"Poloniex","bingx":"BingX","lbank":"LBank","bitstamp":"Bitstamp","gemini":"Gemini","bitrue":"Bitrue","xtcom":"XT.com","bitmart":"BitMart","htx":"HTX"}
EXTRA_OPTS={"bybit":{"options":{"defaultType":"spot"}},"okx":{"options":{"defaultType":"spot"}},"bingx":{"options":{"defaultType":"spot"}},"mexc":{"options":{"defaultType":"spot"}},"bitrue":{"options":{"defaultType":"spot"}},"xtcom":{"options":{"defaultType":"spot"}},"bitmart":{"options":{"defaultType":"spot"}},"htx":{"options":{"defaultType":"spot"}}}
USD_QUOTES={"USDT","USD","USDC","BUSD"}
LOW_FEE_CHAIN_PRIORITY=["TRC20","BEP20","BSC","SOL","MATIC","ARB","OP","Polygon","TON","AVAX","ETH"]
LEV_PATTERNS=[r"\b\d+[LS]\b",r"\bUP\b",r"\bDOWN\b",r"\bBULL\b",r"\bBEAR\b"]
LEV_REGEX=re.compile("|".join(LEV_PATTERNS),re.IGNORECASE)

st.sidebar.header("Scanner Controls")
buy_exchanges=st.sidebar.multiselect("Buy Exchanges (up to 10)",TOP_20_CCXT_EXCHANGES,default=saved.get("buy_exchanges",[]),max_selections=10,format_func=lambda x: EXCHANGE_NAMES.get(x,x))
sell_exchanges=st.sidebar.multiselect("Sell Exchanges (up to 10)",TOP_20_CCXT_EXCHANGES,default=saved.get("sell_exchanges",[]),max_selections=10,format_func=lambda x: EXCHANGE_NAMES.get(x,x))
min_profit=st.sidebar.number_input("Minimum Profit % (after fees)",0.0,100.0,saved.get("min_profit",1.0),0.1)
max_profit=st.sidebar.number_input("Maximum Profit % (after fees)",0.0,200.0,saved.get("max_profit",20.0),0.1)
min_24h_vol_usd=st.sidebar.number_input("Min 24h Volume (USD)",0.0,1_000_000_000.0,saved.get("min_24h_vol_usd",100000.0),50000.0)
exclude_chains=st.sidebar.multiselect("Exclude Blockchains",["ETH","TRC20","BEP20","BSC","SOL","MATIC","ARB","OP","Polygon","TON","AVAX"],default=saved.get("exclude_chains",["ETH"]))
include_all_chains=st.sidebar.checkbox("Include all blockchains (ignore exclusion)",value=saved.get("include_all_chains",False))
auto_refresh=st.sidebar.checkbox("🔄 Auto Refresh Every 20s",value=saved.get("auto_refresh",False))
scan_now=st.button("🚀 Scan Now")
if scan_now:
    save_settings({"buy_exchanges":buy_exchanges,"sell_exchanges":sell_exchanges,"min_profit":min_profit,"max_profit":max_profit,"min_24h_vol_usd":min_24h_vol_usd,"exclude_chains":exclude_chains,"include_all_chains":include_all_chains,"auto_refresh":auto_refresh})

if "op_cache" not in st.session_state: st.session_state.op_cache={}
if "lifetime_history" not in st.session_state: st.session_state.lifetime_history={}
if "last_seen_keys" not in st.session_state: st.session_state.last_seen_keys=set()

def parse_symbol(symbol:str):
    try:
        base=symbol.split("/")[0]; quote=symbol.split("/")[1].split(":")[0]; return base,quote
    except: return symbol,""
def market_price_from_ticker(t):
    if not t: return None
    last=t.get("last")
    if last is not None:
        try: return float(last)
        except: pass
    bid,ask=t.get("bid"),t.get("ask")
    if bid is not None and ask is not None:
        try: return (float(bid)+float(ask))/2.0
        except: return None
    return None
def safe_price(ex,sym):
    try:
        ob=ex.fetch_order_book(sym,limit=5)
        bid=ob.get('bids')[0][0] if ob.get('bids') else None
        ask=ob.get('asks')[0][0] if ob.get('asks') else None
        if bid and ask: return (float(bid)+float(ask))/2.0
    except: return None
    return None
def approx_liquidity_from_orderbook(ex,sym,side='asks',depth=5):
    try:
        ob=ex.fetch_order_book(sym,limit=depth); arr=ob.get(side,[]); total=0.0
        for price,amount in arr[:depth]:
            try: total+=float(price)*float(amount)
            except: continue
        return float(total)
    except: return 0.0
def is_ticker_fresh(t,max_age_sec=300):
    if not t: return True
    ts=t.get("timestamp")
    if ts is None: return True
    now=int(time.time()*1000)
    try: return (now-int(ts))<=max_age_sec*1000
    except: return True
def fmt_usd(x):
    try:
        x=float(x or 0)
        if x>=1e9: return f"${x/1e9:.2f}B"
        if x>=1e6: return f"${x/1e6:.2f}M"
        if x>=1e3: return f"${x/1e3:.0f}K"
        return f"${x:,.0f}"
    except: return "$0"
def secs_to_label(secs): return f"{int(secs)}s" if secs<90 else f"{secs/60:.1f}m"
def update_lifetime_for_disappeared(current_keys):
    gone=st.session_state.last_seen_keys-set(current_keys)
    for key in gone:
        trail=st.session_state.op_cache.get(key,[])
        if trail:
            duration=trail[-1][0]-trail[0][0]
            if duration>0: st.session_state.lifetime_history.setdefault(key,[]).append(duration)
    st.session_state.last_seen_keys=set(current_keys)
def stability_and_expiry(key,current_profit):
    now=time.time(); trail=st.session_state.op_cache.get(key,[])
    if not trail:
        st.session_state.op_cache[key]=[(now,current_profit)]; return "⏳ new","~unknown"
    trail.append((now,current_profit)); st.session_state.op_cache[key]=trail[-30:]
    duration=trail[-1][0]-trail[0][0]; observed=f"⏳ {secs_to_label(duration)} observed"
    hist=st.session_state.lifetime_history.get(key,[])
    if not hist: expiry="~unknown"
    else:
        avg_life=sum(hist)/len(hist); remaining=avg_life-duration
        expiry="⚠️ past avg" if remaining<=0 else f"~{secs_to_label(remaining)} left"
    return observed,expiry

INFO_VOLUME_CANDIDATES=["quoteVolume","baseVolume","vol","vol24h","volCcy24h","volValue","turnover","turnover24h","quoteVolume24h","amount","value","acc_trade_price_24h","quote_volume_24h","base_volume_24h"]
def safe_usd_volume(ex_id,symbol,ticker,price,all_tickers):
    try:
        base,quote=parse_symbol(symbol); q_upper=quote.upper()
        qvol=None; bvol=None
        if ticker: qvol=ticker.get("quoteVolume"); bvol=ticker.get("baseVolume")
        if q_upper in USD_QUOTES and qvol:
            try: return float(qvol)
            except: pass
        if bvol and price:
            try: return float(bvol)*float(price)
            except: pass
        info=(ticker.get("info") if ticker else {}) or {}
        raw=None
        for key in INFO_VOLUME_CANDIDATES:
            val=info.get(key)
            if val is None: continue
            try:
                fval=float(val)
                if fval>0: raw=fval; break
            except: continue
        if raw is not None:
            if q_upper in USD_QUOTES: return float(raw)
            conv_sym=f"{q_upper}/USDT"; conv_t=all_tickers.get(conv_sym); conv_px=market_price_from_ticker(conv_t)
            if conv_px: return float(raw)*float(conv_px)
        if qvol:
            conv_sym=f"{q_upper}/USDT"; conv_t=all_tickers.get(conv_sym); conv_px=market_price_from_ticker(conv_t)
            if conv_px:
                try: return float(qvol)*float(conv_px)
                except: pass
        return 0.0
    except: return 0.0

def symbol_ok(ex_obj,symbol):
    m=ex_obj.markets.get(symbol,{})
    if not m: return False
    if not m.get("spot",True): return False
    base,quote=parse_symbol(symbol)
    if quote.upper() not in USD_QUOTES: return False
    if LEV_REGEX.search(symbol): return False
    if m.get("active") is False: return False
    return True

def choose_common_chain(ex1,ex2,coin,exclude_chains,include_all_chains):
    try:
        c1=ex1.currencies.get(coin,{}) or {}; c2=ex2.currencies.get(coin,{}) or {}
        nets1=c1.get("networks",{}) or {}; nets2=c2.get("networks",{}) or {}
        common=set(nets1.keys())&set(nets2.keys())
        if not common: return "❌ No chain","❌","❌"
        preferred=[n for n in LOW_FEE_CHAIN_PRIORITY if (include_all_chains or n not in exclude_chains)]
        best=None
        for pref in preferred:
            if pref in common: best=pref; break
        if not best:
            candidate=sorted(list(common))[0]
            if not include_all_chains and candidate in exclude_chains: return "❌ No chain","❌","❌"
            best=candidate
        w_ok="✅" if nets1.get(best,{}).get("withdraw") else "❌"
        d_ok="✅" if nets2.get(best,{}).get("deposit") else "❌"
        return best,w_ok,d_ok
    except: return "❌ Unknown","❌","❌"

def run_scan():
    if not buy_exchanges or not sell_exchanges:
        st.warning("Please select at least one Buy and one Sell exchange."); return
    try:
        ex_objs={}
        for ex_id in set(buy_exchanges+sell_exchanges):
            if not hasattr(ccxt,ex_id):
                st.warning(f"⚠️ ccxt has no attribute '{ex_id}'. Skipping that exchange."); continue
            opts={"enableRateLimit":True,"timeout":12000}; opts.update(EXTRA_OPTS.get(ex_id,{}))
            try: ex=getattr(ccxt,ex_id)(opts)
            except Exception as e:
                st.warning(f"⚠️ Could not instantiate {EXCHANGE_NAMES.get(ex_id,ex_id)} ({ex_id}): {e}"); continue
            try: ex.load_markets()
            except Exception as e: st.warning(f"⚠️ {EXCHANGE_NAMES.get(ex_id,ex_id)} load_markets issue: {e}")
            ex_objs[ex_id]=ex

        available_buy=[e for e in buy_exchanges if e in ex_objs]; available_sell=[e for e in sell_exchanges if e in ex_objs]
        if not available_buy or not available_sell:
            st.warning("After initialization some selected exchanges were unavailable. Please check the warnings and adjust your selection."); return

        bulk_tickers={}
        for ex_id,ex in ex_objs.items():
            try:
                if hasattr(ex,"fetch_tickers"):
                    if ex_id=="upbit":
                        syms=list(getattr(ex,"symbols",[])); bulk_tickers[ex_id]={}
                        for i in range(0,len(syms),200):
                            chunk=syms[i:i+200]
                            try: bulk_tickers[ex_id].update(ex.fetch_tickers(chunk))
                            except Exception as ce:
                                st.warning(f"⚠️ {EXCHANGE_NAMES.get(ex_id,ex_id)} chunk fetch failed: {ce}"); break
                    else:
                        try:
                            bulk_tickers[ex_id]=ex.fetch_tickers() or {}
                            if not isinstance(bulk_tickers[ex_id],dict): bulk_tickers[ex_id]={}
                        except Exception:
                            syms=list(getattr(ex,"symbols",[])) or []; bulk={}
                            for i in range(0,min(len(syms),600),200):
                                chunk=syms[i:i+200]
                                try: bulk.update(ex.fetch_tickers(chunk))
                                except: break
                            bulk_tickers[ex_id]=bulk; st.warning(f"⚠️ {EXCHANGE_NAMES.get(ex_id,ex_id)} fetch_tickers fallback used.")
                else: bulk_tickers[ex_id]={}
            except Exception as e:
                st.warning(f"⚠️ {EXCHANGE_NAMES.get(ex_id,ex_id)} fetch_tickers failed: {e}"); bulk_tickers[ex_id]={}

        results=[]; current_keys=[]
        for buy_id in available_buy:
            for sell_id in available_sell:
                if buy_id==sell_id: continue
                buy_ex,sell_ex=ex_objs[buy_id],ex_objs[sell_id]
                buy_tk,sell_tk=bulk_tickers.get(buy_id,{}),bulk_tickers.get(sell_id,{})
                common=set(getattr(buy_ex,"markets",{}).keys())&set(getattr(sell_ex,"markets",{}).keys())
                symbols=[s for s in common if symbol_ok(buy_ex,s) and symbol_ok(sell_ex,s)]
                symbols=symbols[:700]
                for sym in symbols:
                    try:
                        bt,st_=buy_tk.get(sym),sell_tk.get(sym)
                        if bt and not is_ticker_fresh(bt): continue
                        if st_ and not is_ticker_fresh(st_): continue
                        buy_px=market_price_from_ticker(bt) if bt else None
                        if buy_px is None: buy_px=safe_price(buy_ex,sym)
                        sell_px=market_price_from_ticker(st_) if st_ else None
                        if sell_px is None: sell_px=safe_price(sell_ex,sym)
                        if not buy_px or not sell_px: continue
                        gap=abs(sell_px-buy_px)/buy_px
                        if gap>0.5: continue
                        buy_fee=buy_ex.markets.get(sym,{}).get("taker",0.001) or 0.001
                        sell_fee=sell_ex.markets.get(sym,{}).get("taker",0.001) or 0.001
                        spread=(sell_px-buy_px)/buy_px*100.0
                        profit_after=spread-(buy_fee*100.0+sell_fee*100.0)
                        if profit_after<min_profit or profit_after>max_profit: continue
                        buy_vol_usd=safe_usd_volume(buy_id,sym,bt,buy_px,buy_tk)
                        sell_vol_usd=safe_usd_volume(sell_id,sym,st_,sell_px,sell_tk)
                        if (not buy_tk or buy_vol_usd<=0) and buy_vol_usd<min_24h_vol_usd:
                            buy_vol_usd=approx_liquidity_from_orderbook(buy_ex,sym,side='asks',depth=8)
                        if (not sell_tk or sell_vol_usd<=0) and sell_vol_usd<min_24h_vol_usd:
                            sell_vol_usd=approx_liquidity_from_orderbook(sell_ex,sym,side='bids',depth=8)
                        if buy_vol_usd<min_24h_vol_usd or sell_vol_usd<min_24h_vol_usd: continue
                        base,quote=parse_symbol(sym)
                        chain,w_ok,d_ok=choose_common_chain(buy_ex,sell_ex,base,exclude_chains,include_all_chains)
                        if not include_all_chains and (chain in exclude_chains or (isinstance(chain,str) and chain.startswith("❌"))): continue
                        if w_ok!="✅" or d_ok!="✅": continue
                        key=f"{sym}|{buy_id}>{sell_id}"; current_keys.append(key)
                        observed,expiry=stability_and_expiry(key,profit_after)
                        results.append({"#":None,"Pair":sym,"Quote":quote,"Buy@":EXCHANGE_NAMES.get(buy_id,buy_id),"Buy Price":round(float(buy_px),10),"Sell@":EXCHANGE_NAMES.get(sell_id,sell_id),"Sell Price":round(float(sell_px),10),"Spread %":round(spread,4),"Profit % After Fees":round(profit_after,4),"Buy Vol (24h)":fmt_usd(buy_vol_usd),"Sell Vol (24h)":fmt_usd(sell_vol_usd),"Withdraw?":w_ok,"Deposit?":d_ok,"Blockchain":chain,"Stability":observed,"Est. Expiry":expiry})
                    except Exception:
                        continue

        update_lifetime_for_disappeared(current_keys)
        if results:
            df=pd.DataFrame(results).sort_values(["Profit % After Fees","Spread %"],ascending=False).reset_index(drop=True)
            df["#"]=range(1,len(df)+1)
            def pill(val,ok=True): return f'<span class="pill {"pill-green" if ok else "pill-red"}">{val}</span>'
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
            st.subheader("✅ Profitable Arbitrage Opportunities"); st.markdown(html,unsafe_allow_html=True)
            csv_df=df.copy()
            st.download_button("⬇️ Download CSV",csv_df.to_csv(index=False),"arbitrage_opportunities.csv","text/csv")
        else:
            st.info("No opportunities matched your profit/volume/chain filters right now.")
    except Exception as e:
        st.error(f"Error: {e}")

if scan_now or auto_refresh:
    with st.spinner("🔍 Scanning exchanges…"):
        run_scan()
    if auto_refresh:
        holder=st.empty()
        for i in range(20,0,-1):
            holder.write(f"⏳ Refreshing in {i}s…"); time.sleep(1)
        st.rerun()
