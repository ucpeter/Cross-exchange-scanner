import ccxt,streamlit as st,pandas as pd,time,re
from datetime import datetime,timedelta
from collections import defaultdict

EXCHANGE_NAMES={"binance":"Binance","okx":"OKX","bybit":"Bybit","kucoin":"KuCoin","gate":"Gate.io","bitget":"Bitget","bitmart":"BitMart","htx":"HTX","bitrue":"Bitrue","poloniex":"Poloniex","coinbase":"Coinbase","gemini":"Gemini","upbit":"Upbit","mexc":"MEXC"}
USD_QUOTES={"USDT","USDC","BUSD","DAI","USD","TUSD","FDUSD"}
LOW_FEE_CHAIN_PRIORITY=["TRC20","BEP20","ERC20","SOL","Polygon","Arbitrum","Optimism","Base","AVAXC","ALGO"]
LEV_REGEX=re.compile(r"[\d]+(S|L|LONG|SHORT|DOWN|UP)$",re.IGNORECASE)
EXTRA_OPTS={
 "okx":{"options":{"defaultType":"spot"}},
 "bybit":{"options":{"defaultType":"spot"}},
 "bitget":{"options":{"defaultType":"spot"}},
 "bitmart":{"options":{"defaultType":"spot"}},
 "htx":{"urls":{"api":{"public":"https://api.huobi.pro"}},"options":{"defaultType":"spot"}},
 "mexc":{"options":{"defaultType":"spot"}}
}

st.set_page_config(page_title="🌍 Cross-Exchange Arbitrage Scanner",layout="wide")
st.title("🌍 Cross-Exchange Arbitrage Scanner")

if "buy_exchanges_selected" not in st.session_state: st.session_state.buy_exchanges_selected=[]
if "sell_exchanges_selected" not in st.session_state: st.session_state.sell_exchanges_selected=[]
def update_buy(): st.session_state.buy_exchanges_selected=st.session_state.buy_exchanges_ui
def update_sell(): st.session_state.sell_exchanges_selected=st.session_state.sell_exchanges_ui
buy_exchanges=st.multiselect("Select Buy Exchanges",list(EXCHANGE_NAMES.keys()),default=st.session_state.buy_exchanges_selected,key="buy_exchanges_ui",on_change=update_buy)
sell_exchanges=st.multiselect("Select Sell Exchanges",list(EXCHANGE_NAMES.keys()),default=st.session_state.sell_exchanges_selected,key="sell_exchanges_ui",on_change=update_sell)
buy_exchanges,sell_exchanges=st.session_state.buy_exchanges_selected,st.session_state.sell_exchanges_selected

min_profit=st.number_input("Min Profit %",value=0.5,step=0.1)
max_profit=st.number_input("Max Profit %",value=15.0,step=0.5)
min_24h_vol_usd=st.number_input("Min 24h Volume (USD)",value=100000,step=5000)
exclude_chains=st.multiselect("Exclude Chains",LOW_FEE_CHAIN_PRIORITY,default=["ERC20"])
include_all_chains=st.checkbox("Include all chains regardless of exclusion list",value=False)
scan_now=st.button("🔍 Run Scan Now")
auto_refresh=st.checkbox("♻️ Auto Refresh every 20s",value=False)

def safe_ccxt_id(eid):return eid.lower().replace(" ","").replace(".","").replace("_","")
def fmt_usd(v):
    try:return f"${float(v):,.0f}"
    except:return "N/A"
def parse_symbol(s):p=re.split(r"[-/:]",s);return p[0],p[1] if len(p)>1 else None
def market_price_from_ticker(t):
    try:b,a=float(t.get("bid",0)),float(t.get("ask",0));return (b+a)/2 if b and a else float(t.get("last",0))
    except:return None
def is_ticker_fresh(t,max_age=120):
    try:
        ts=t.get("timestamp") or t.get("datetime")
        if ts is None:return True
        if isinstance(ts,(int,float)):dt=datetime.utcfromtimestamp(ts/1000 if ts>1e12 else ts)
        elif isinstance(ts,str):dt=datetime.fromisoformat(ts.replace("Z","+00:00"))
        else:return True
        return (datetime.utcnow()-dt)<timedelta(seconds=max_age)
    except:return True

for name in ["lifetime_store","lifetime_history","market_seen_first"]:
    if name not in st.session_state:st.session_state[name]={}
lifetime_store=st.session_state["lifetime_store"];lifetime_history=st.session_state["lifetime_history"];market_seen_first=st.session_state["market_seen_first"]

def _secs_to_label_short(s):
    s=int(s)
    if s<60:return f"{s}s"
    if s<3600:return f"{s//60}m"
    return f"{s//3600}h{(s%3600)//60}m"
def stability_and_expiry(k,p,t1=None,t2=None):
    now=datetime.utcnow();fs,pp,orig=lifetime_store.get(k,(None,None,None))
    if k not in market_seen_first:market_seen_first[k]=now
    fsm=market_seen_first[k]
    if not fs:fs=now
    lifetime_store[k]=(fs,p,fsm)
    ma=(now-fsm).total_seconds()
    if pp is None:stx="New"
    elif p>=pp:stx="Stable"
    else:stx="Unstable"
    stxt=f"{stx} (market {_secs_to_label_short(ma)})"
    h=lifetime_history.get(k,[]);exp="~unknown"
    if h:
        avg=sum(h)/len(h);rem=int(avg-ma);exp="likely ending" if rem<=0 else f"~{_secs_to_label_short(rem)} left"
    return stxt,exp
def update_lifetime_for_disappeared(keys):
    try:
        rm=[]
        for k,v in list(lifetime_store.items()):
            if k not in keys:
                fs,lp,fsm=v;dur=(datetime.utcnow()-(fsm or fs)).total_seconds()
                if dur>0:
                    lifetime_history.setdefault(k,[]).append(dur)
                    if len(lifetime_history[k])>30:lifetime_history[k]=lifetime_history[k][-30:]
                rm.append(k)
        for k in rm:lifetime_store.pop(k,None)
    except:pass
def safe_fetch_tickers(ex,eid):
    try:
        if not ex.markets:return {}
        syms=list(ex.markets.keys())
        if eid=="upbit" and len(syms)>200:return ex.fetch_tickers(syms[:200])
        return ex.fetch_tickers()
    except Exception as e:
        st.warning(f"⚠️ {EXCHANGE_NAMES.get(eid,eid)} fetch_tickers issue: {e}")
        return {}

def normalize_symbol(s):return s.replace("/","-").replace(":","-").upper()
def build_symbol_map(ex):
    m=defaultdict(list)
    for s in ex.markets.keys():m[normalize_symbol(s)].append(s)
    return m

MAX_SYMBOLS_PER_PAIR=200
INFO_VOL_KEYS=["quoteVolume","baseVolume","vol","vol24h","volCcy24h","volValue","turnover","turnover24h","quoteVolume24h","amount","value","acc_trade_price_24h","quote_volume_24h","base_volume_24h"]

def safe_usd_volume(eid,sym,t,px,tks):
    try:
        b,q=parse_symbol(sym);qU=q.upper();qv=t.get("quoteVolume") if t else None;bv=t.get("baseVolume") if t else None
        if qU in USD_QUOTES and qv:return float(qv)
        if bv and px:return float(bv)*float(px)
        info=(t.get("info") if t else {}) or {};raw=None
        for k in INFO_VOL_KEYS:
            v=info.get(k)
            if v is None:continue
            try:fv=float(v)
            except:continue
            if fv>0:raw=fv;break
        if raw is not None:
            if qU in USD_QUOTES:return float(raw)
            conv=f"{qU}/USDT";ct=tks.get(conv);cp=market_price_from_ticker(ct)
            if cp:return float(raw)*float(cp)
        if qv:
            conv=f"{qU}/USDT";ct=tks.get(conv);cp=market_price_from_ticker(ct)
            if cp:return float(qv)*float(cp)
        return 0.0
    except:return 0.0

def symbol_ok(ex,s):
    try:
        m=ex.markets.get(s,{})
        if not m or not m.get("spot",True):return False
        b,q=parse_symbol(s)
        if q.upper() not in USD_QUOTES or LEV_REGEX.search(s) or m.get("active") is False:return False
        return True
    except:return False

def choose_common_chain(e1,e2,c,excl,inc_all):
    try:
        c1=e1.currencies.get(c,{}) or {};c2=e2.currencies.get(c,{}) or {}
        n1=c1.get("networks",{}) or {};n2=c2.get("networks",{}) or {}
        com=set(n1.keys())&set(n2.keys())
        if not com:return "❌ No chain","❌","❌"
        pref=[n for n in LOW_FEE_CHAIN_PRIORITY if (inc_all or n not in excl)];best=None
        for p in pref:
            if p in com:best=p;break
        if not best:
            cand=sorted(list(com))[0]
            if not inc_all and cand in excl:return "❌ No chain","❌","❌"
            best=cand
        return best,"✅" if n1.get(best,{}).get("withdraw") else "❌","✅" if n2.get(best,{}).get("deposit") else "❌"
    except:return "❌ Unknown","❌","❌"

def run_scan():
    if not buy_exchanges or not sell_exchanges:
        st.warning("Please select at least one Buy and one Sell exchange.");return
    try:
        exs={}
        for eid in set(buy_exchanges+sell_exchanges):
            try:
                o={"enableRateLimit":True,"timeout":12000};o.update(EXTRA_OPTS.get(eid,{}))
                sid=safe_ccxt_id(eid)
                if not hasattr(ccxt,sid):
                    st.warning(f"⚠️ ccxt has no attribute '{eid}'. Skipping.");continue
                ex=getattr(ccxt,sid)(o)
                try:
                    mk=ex.load_markets()
                    if not mk:raise Exception("load_markets() returned None")
                except Exception as e:
                    st.warning(f"⚠️ {EXCHANGE_NAMES.get(eid,sid)} load_markets issue: {e}")
                    continue
                exs[eid]=ex
            except Exception as e:
                st.warning(f"⚠️ Could not instantiate {eid}: {e}")
                continue
        tks,results,keys={},[],[]
        for eid,ex in exs.items():
            try:tks[eid]=safe_fetch_tickers(ex,eid)
            except Exception as e:
                st.warning(f"⚠️ {EXCHANGE_NAMES.get(eid,eid)} fetch_tickers failed: {e}");tks[eid]={}
        for b_id in buy_exchanges:
            for s_id in sell_exchanges:
                if b_id==s_id:continue
                b_ex,s_ex=exs[b_id],exs[s_id];btks,stks=tks[b_id],tks[s_id]
                bmap,smap=build_symbol_map(b_ex),build_symbol_map(s_ex)
                common=list(set(bmap.keys())&set(smap.keys()))[:MAX_SYMBOLS_PER_PAIR]
                for nsym in common:
                    try:
                        bc,sc=bmap.get(nsym,[]),smap.get(nsym,[])
                        found=False;bm=sm=None;bt=st_=None
                        for bmc in bc:
                            for smc in sc:
                                bt,st_=btks.get(bmc),stks.get(smc)
                                if not bt or not st_:continue
                                if not is_ticker_fresh(bt) or not is_ticker_fresh(st_):continue
                                bm,sm=bmc,smc;found=True;break
                            if found:break
                        if not found:continue
                        sym=bm;bp=market_price_from_ticker(bt);sp=market_price_from_ticker(st_)
                        if not bp or not sp:continue
                        gap=abs(sp-bp)/bp
                        if gap>0.5:continue
                        bf=b_ex.markets.get(sym,{}).get("taker",0.001) or 0.001;sf=s_ex.markets.get(sm,{}).get("taker",0.001) or 0.001
                        spread=(sp-bp)/bp*100;profit=spread-(bf*100+sf*100)
                        if profit<min_profit or profit>max_profit:continue
                        bv=safe_usd_volume(b_id,sym,bt,bp,btks);sv=safe_usd_volume(s_id,sym,st_,sp,stks)
                        if bv<min_24h_vol_usd or sv<min_24h_vol_usd:continue
                        base,quote=parse_symbol(sym);chain,w,d=choose_common_chain(b_ex,s_ex,base,exclude_chains,include_all_chains)
                        if not include_all_chains and (chain in exclude_chains or str(chain).startswith("❌")):continue
                        if w!="✅" or d!="✅":continue
                        k=f"{nsym}|{b_id}>{s_id}";keys.append(k)
                        obs,exp=stability_and_expiry(k,profit,bt.get("timestamp") if bt else None,st_.get("timestamp") if st_ else None)
                        results.append({"#":None,"Pair":nsym,"Quote":quote,"Buy@":EXCHANGE_NAMES.get(b_id,b_id),"Buy Price":round(bp,10),"Sell@":EXCHANGE_NAMES.get(s_id,s_id),"Sell Price":round(sp,10),"Spread %":round(spread,4),"Profit % After Fees":round(profit,4),"Buy Vol (24h)":fmt_usd(bv),"Sell Vol (24h)":fmt_usd(sv),"Withdraw?":w,"Deposit?":d,"Blockchain":chain,"Stability":obs,"Est. Expiry":exp})
                    except:continue
        update_lifetime_for_disappeared(keys)
  if results:
    df=pd.DataFrame(results).sort_values(["Profit % After Fees","Spread %"],ascending=False).reset_index(drop=True)
    df["#"]=range(1,len(df)+1)
    def pill(v,ok=True):
        cls="pill-green" if ok else "pill-red"
        return f'<span class="pill {cls}">{v}</span>'
    def cpr(p):
        return f'<span class="good mono">{p:.4f}%</span>' if p>=0 else f'<span class="bad mono">{p:.4f}%</span>'
    def cs(s):
        return f'<span class="spread mono">{s:.4f}%</span>'
    heads=["#","Pair","Quote","Buy@","Buy Price","Sell@","Sell Price","Spread %","Profit % After Fees",
           "Buy Vol (24h)","Sell Vol (24h)","Withdraw?","Deposit?","Blockchain","Stability","Est. Expiry"]
    html='<div class="table-wrap"><table class="arb-table"><thead><tr>' + \
         "".join([f"<th>{h}</th>" for h in heads]) + "</tr></thead><tbody>"
    for _, r in df.iterrows():
        row  = "<tr>"
        row += f'<td class="num mono">{int(r["#"])}</td>'
        row += f'<td class="mono">{r["Pair"]}</td>'
        row += f'<td>{r["Quote"]}</td>'
        row += f'<td>{r["Buy@"]}</td>'
        row += f'<td class="num mono">{r["Buy Price"]}</td>'
        row += f'<td>{r["Sell@"]}</td>'
        row += f'<td class="num mono">{r["Sell Price"]}</td>'
        row += f'<td class="num">{cs(r["Spread %"])}</td>'
        row += f'<td class="num">{cpr(r["Profit % After Fees"])}</td>'
        row += f'<td class="num mono">{r["Buy Vol (24h)"]}</td>'
        row += f'<td class="num mono">{r["Sell Vol (24h)"]}</td>'
        row += f'<td>{pill("✅",True) if r["Withdraw?"]=="✅" else pill("❌",False)}</td>'
        row += f'<td>{pill("✅",True) if r["Deposit?"]=="✅" else pill("❌",False)}</td>'
        row += f'<td><span class="pill pill-blue">{r["Blockchain"]}</span></td>'
        row += f'<td class="small">{r["Stability"]}</td>'
        row += f'<td class="small">{r["Est. Expiry"]}</td>'
        row += "</tr>"
        html += row
    html += "</tbody></table></div>"
    st.subheader("✅ Profitable Arbitrage Opportunities")
    st.markdown(html, unsafe_allow_html=True)
    st.download_button("⬇️ Download CSV", df.to_csv(index=False), "arbitrage_opportunities.csv", "text/csv")
else:
    st.info("No opportunities matched your profit/volume/chain filters right now.")
except Exception as e:
    st.error(f"Error: {e}")
if scan_now or auto_refresh:
    with st.spinner("🔍 Scanning exchanges…"):run_scan()
    if auto_refresh:
        h=st.empty()
        for i in range(20,0,-1):
            h.write(f"⏳ Refreshing in {i}s…");time.sleep(1)
        st.experimental_rerun()
