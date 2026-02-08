# ------------------- arbitrage_scanner.py -------------------
import time, re, ccxt, json, os, sys
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import threading
import logging
from tenacity import retry, stop_after_attempt, wait_exponential
import asyncio
import aiohttp
import concurrent.futures
from typing import Dict, List, Optional, Tuple
import warnings
warnings.filterwarnings('ignore')

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ------------------- Configuration -------------------
SETTINGS_FILE = "settings.json"
RESULTS_CACHE_FILE = "results_cache.json"
CACHE_DURATION = 15  # seconds

TOP_20_CCXT_EXCHANGES = [
    "binance", "okx", "coinbase", "kraken", "bybit", "kucoin",
    "mexc", "bitfinex", "bitget", "gateio", "crypto_com",
    "upbit", "whitebit", "poloniex", "bingx", "lbank",
    "bitstamp", "gemini", "bitrue", "xt",
]

EXCHANGE_NAMES = {
    "binance": "Binance", "okx": "OKX", "coinbase": "Coinbase",
    "kraken": "Kraken", "bybit": "Bybit", "kucoin": "KuCoin",
    "mexc": "MEXC", "bitfinex": "Bitfinex", "bitget": "Bitget",
    "gateio": "Gate.io", "crypto_com": "Crypto.com", "upbit": "Upbit",
    "whitebit": "WhiteBIT", "poloniex": "Poloniex", "bingx": "BingX",
    "lbank": "LBank", "bitstamp": "Bitstamp", "gemini": "Gemini",
    "bitrue": "Bitrue", "xt": "XT.com",
}

EXCHANGE_CONFIGS = {
    "binance": {"enableRateLimit": True, "timeout": 10000, "options": {"defaultType": "spot"}},
    "okx": {"enableRateLimit": True, "timeout": 10000, "options": {"defaultType": "spot"}},
    "bybit": {"enableRateLimit": True, "timeout": 10000, "options": {"defaultType": "spot"}},
    "mexc": {"enableRateLimit": True, "timeout": 10000, "options": {"defaultType": "spot"}},
    "bitrue": {"enableRateLimit": True, "timeout": 10000, "options": {"defaultType": "spot"}},
    "xt": {"enableRateLimit": True, "timeout": 10000, "options": {"defaultType": "spot"}},
    "bingx": {"enableRateLimit": True, "timeout": 10000, "options": {"defaultType": "spot"}},
    "kucoin": {"enableRateLimit": True, "timeout": 10000},
    "gateio": {"enableRateLimit": True, "timeout": 10000},
    "bitget": {"enableRateLimit": True, "timeout": 10000, "options": {"defaultType": "spot", "version": "v2"}},
    "coinbase": {"enableRateLimit": True, "timeout": 10000},
    "kraken": {"enableRateLimit": True, "timeout": 10000},
    "bitfinex": {"enableRateLimit": True, "timeout": 10000},
    "crypto_com": {"enableRateLimit": True, "timeout": 10000},
    "upbit": {"enableRateLimit": True, "timeout": 10000},
    "whitebit": {"enableRateLimit": True, "timeout": 10000},
    "poloniex": {"enableRateLimit": True, "timeout": 10000},
    "lbank": {"enableRateLimit": True, "timeout": 10000},
    "bitstamp": {"enableRateLimit": True, "timeout": 10000},
    "gemini": {"enableRateLimit": True, "timeout": 10000},
}

USD_QUOTES = {"USDT", "USD", "USDC", "BUSD"}
LEV_PATTERNS = [r"\b\d+[LS]\b", r"\bUP\b", r"\bDOWN\b", r"\bBULL\b", r"\bBEAR\b"]
LEV_REGEX = re.compile("|".join(LEV_PATTERNS), re.IGNORECASE)

# Global cache for results
scan_cache = {
    "results": [],
    "timestamp": None,
    "settings_hash": None
}

opportunity_tracker = {}
exchange_status = {}

# ------------------- Helper Functions -------------------
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)

def get_settings_hash(settings):
    """Generate hash for settings to detect changes"""
    import hashlib
    settings_str = json.dumps(settings, sort_keys=True)
    return hashlib.md5(settings_str.encode()).hexdigest()

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def init_exchange_with_retry(ex_id):
    """Initialize exchange with retry logic"""
    config = EXCHANGE_CONFIGS.get(ex_id, {"enableRateLimit": True, "timeout": 10000})
    
    try:
        exchange_class = getattr(ccxt, ex_id)
        ex = exchange_class(config)
        
        # Load markets
        logger.info(f"Loading markets for {ex_id}")
        ex.load_markets()
        
        # Test connectivity
        try:
            ticker = ex.fetch_ticker("BTC/USDT")
            exchange_status[ex_id] = {
                "status": "connected",
                "markets": len(ex.markets),
                "last_price": ticker.get('last') if ticker else None,
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            exchange_status[ex_id] = {
                "status": "connected_no_ticker",
                "markets": len(ex.markets),
                "error": str(e)[:100],
                "timestamp": datetime.now().isoformat()
            }
        
        return ex
    except Exception as e:
        exchange_status[ex_id] = {
            "status": "failed",
            "error": str(e)[:100],
            "timestamp": datetime.now().isoformat()
        }
        logger.error(f"Failed to initialize {ex_id}: {e}")
        return None

def get_exchange_tickers(ex, ex_id):
    """Get tickers with fallback strategies"""
    tickers = {}
    
    try:
        # Try bulk fetch first
        bulk_tickers = ex.fetch_tickers()
        if bulk_tickers and len(bulk_tickers) > 10:
            logger.info(f"{ex_id}: Got {len(bulk_tickers)} tickers via fetch_tickers()")
            return bulk_tickers
    except Exception as e:
        logger.warning(f"{ex_id}: Bulk ticker fetch failed: {str(e)[:50]}")
    
    # Fallback: Fetch top symbols individually
    top_symbols = [
        "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
        "ADA/USDT", "AVAX/USDT", "DOGE/USDT", "DOT/USDT", "MATIC/USDT",
        "LINK/USDT", "TRX/USDT", "UNI/USDT", "TON/USDT", "SHIB/USDT"
    ]
    
    available_symbols = [s for s in top_symbols if s in ex.markets]
    logger.info(f"{ex_id}: Fetching {len(available_symbols)} individual tickers")
    
    for symbol in available_symbols:
        try:
            ticker = ex.fetch_ticker(symbol)
            if ticker and ticker.get('last') is not None:
                tickers[symbol] = ticker
            time.sleep(0.05)  # Rate limiting
        except Exception as e:
            continue
    
    return tickers

def parse_symbol(symbol: str):
    """Safely parse symbol into base and quote"""
    try:
        if ":" in symbol:
            symbol = symbol.split(":")[0]
        if "/" in symbol:
            parts = symbol.split("/")
            return parts[0].upper(), parts[1].upper()
        return symbol.upper(), ""
    except:
        return "", ""

def is_ticker_fresh(ticker, max_age_sec=300):
    """Check if ticker data is fresh"""
    try:
        ts = ticker.get("timestamp")
        if ts is None:
            return True
        now = int(time.time() * 1000)
        return (now - int(ts)) <= max_age_sec * 1000
    except:
        return True

def market_price_from_ticker(ticker):
    """Extract market price from ticker"""
    if not ticker:
        return None
    
    # Try last price first
    last = ticker.get("last")
    if last is not None:
        try:
            return float(last)
        except:
            pass
    
    # Fallback to bid/ask midpoint
    bid = ticker.get("bid")
    ask = ticker.get("ask")
    if bid is not None and ask is not None:
        try:
            return (float(bid) + float(ask)) / 2.0
        except:
            return None
    
    # Try close price
    close = ticker.get("close")
    if close is not None:
        try:
            return float(close)
        except:
            return None
    
    return None

def calculate_usd_volume(ticker, symbol, price, exchange_id):
    """Calculate USD volume from ticker data"""
    try:
        base, quote = parse_symbol(symbol)
        
        # Try quote volume first if quote is USD stable
        if quote in USD_QUOTES:
            quote_vol = ticker.get("quoteVolume")
            if quote_vol:
                return float(quote_vol)
        
        # Try base volume * price
        base_vol = ticker.get("baseVolume")
        if base_vol and price:
            return float(base_vol) * float(price)
        
        # Check info object
        info = ticker.get("info", {})
        volume_fields = ["volume", "vol", "vol24h", "quoteVolume", "baseVolume", 
                        "quote_volume", "base_volume", "turnover", "value"]
        
        for field in volume_fields:
            if field in info:
                try:
                    vol = float(info[field])
                    if quote in USD_QUOTES:
                        return vol
                    elif price:
                        return vol * price
                except:
                    continue
        
        return 0.0
    except Exception as e:
        logger.debug(f"Volume calc error for {symbol} on {exchange_id}: {e}")
        return 0.0

def symbol_is_valid(symbol, exchange):
    """Check if symbol is valid for arbitrage"""
    try:
        market = exchange.markets.get(symbol)
        if not market:
            return False
        
        # Spot only
        if not market.get("spot", True):
            return False
        
        # Active markets only
        if market.get("active") is False:
            return False
        
        # Check for USD quotes
        base, quote = parse_symbol(symbol)
        if quote not in USD_QUOTES:
            return False
        
        # Exclude leveraged/ETP tokens
        if LEV_REGEX.search(symbol):
            return False
        
        return True
    except:
        return False

def choose_common_chain(buy_ex, sell_ex, coin, exclude_chains, include_all_chains):
    """Find common blockchain for withdrawal/deposit"""
    try:
        c1 = buy_ex.currencies.get(coin, {}) or {}
        c2 = sell_ex.currencies.get(coin, {}) or {}
        nets1 = c1.get("networks", {}) or {}
        nets2 = c2.get("networks", {}) or {}
        
        common = set(nets1.keys()) & set(nets2.keys())
        if not common:
            return "❌ No chain", "❌", "❌"
        
        # Priority chains (excluding ETH if requested)
        LOW_FEE_CHAIN_PRIORITY = ["TRC20", "BEP20", "BSC", "SOL", "MATIC", "ARB", "OP", "Polygon", "TON", "AVAX"]
        
        # Build preferred list honoring exclusions
        preferred = [n for n in LOW_FEE_CHAIN_PRIORITY if (include_all_chains or n not in exclude_chains)]
        
        # Try to find a preferred chain
        best = None
        for pref in preferred:
            if pref in common:
                best = pref
                break
        
        # If no preferred chain found, use first common (if allowed)
        if not best:
            candidate = sorted(list(common))[0]
            if not include_all_chains and candidate in exclude_chains:
                return "❌ No chain", "❌", "❌"
            best = candidate
        
        # Check if withdrawal/deposit enabled
        w_ok = "✅" if nets1.get(best, {}).get("withdraw") else "❌"
        d_ok = "✅" if nets2.get(best, {}).get("deposit") else "❌"
        
        return best, w_ok, d_ok
    except:
        return "❌ Unknown", "❌", "❌"

def fmt_usd(x):
    """Format USD value"""
    try:
        x = float(x or 0)
        if x >= 1e9:
            return f"${x/1e9:.2f}B"
        if x >= 1e6:
            return f"${x/1e6:.2f}M"
        if x >= 1e3:
            return f"${x/1e3:.0f}K"
        return f"${x:,.0f}"
    except:
        return "$0"

def secs_to_label(secs):
    """Convert seconds to human readable label"""
    if secs < 60:
        return f"{int(secs)}s"
    elif secs < 3600:
        return f"{secs/60:.1f}m"
    else:
        return f"{secs/3600:.1f}h"

def update_opportunity_tracking(results):
    """Update opportunity lifetime tracking"""
    current_time = time.time()
    current_keys = [op["key"] for op in results]
    
    # Update existing opportunities
    for result in results:
        key = result["key"]
        profit = result["profit_after"]
        
        if key not in opportunity_tracker:
            opportunity_tracker[key] = {
                "first_seen": current_time,
                "last_seen": current_time,
                "max_profit": profit,
                "min_profit": profit,
                "history": [(current_time, profit)]
            }
        else:
            tracker = opportunity_tracker[key]
            tracker["last_seen"] = current_time
            tracker["max_profit"] = max(tracker["max_profit"], profit)
            tracker["min_profit"] = min(tracker["min_profit"], profit)
            tracker["history"].append((current_time, profit))
            
            # Keep only last 100 entries
            if len(tracker["history"]) > 100:
                tracker["history"] = tracker["history"][-100:]
    
    # Remove old opportunities (not seen for 10 minutes)
    keys_to_remove = []
    for key, tracker in opportunity_tracker.items():
        if key not in current_keys and (current_time - tracker["last_seen"]) > 600:
            keys_to_remove.append(key)
    
    for key in keys_to_remove:
        del opportunity_tracker[key]

def calculate_stability(key, current_profit):
    """Calculate opportunity stability"""
    if key not in opportunity_tracker:
        return "⏳ New", "~"
    
    tracker = opportunity_tracker[key]
    duration = tracker["last_seen"] - tracker["first_seen"]
    
    # Calculate profit stability
    if len(tracker["history"]) > 1:
        profits = [p for _, p in tracker["history"]]
        profit_std = np.std(profits) if len(profits) > 1 else 0
        if profit_std < 0.1:
            stability = "📈 Stable"
        elif profit_std < 0.5:
            stability = "📊 Moderate"
        else:
            stability = "📉 Volatile"
    else:
        stability = "⏳ New"
    
    # Estimate expiry based on historical patterns
    if duration > 60:  # At least 1 minute of data
        avg_duration = duration  # Simple estimate
        remaining = max(0, avg_duration * 0.8 - (current_time - tracker["first_seen"]))
        expiry = f"~{secs_to_label(remaining)}"
    else:
        expiry = "~"
    
    observed = f"{stability} ({secs_to_label(duration)})"
    return observed, expiry

# ------------------- Core Scanning Function -------------------
def run_scan_async(settings):
    """Run scan with current settings"""
    try:
        buy_exchanges = settings.get("buy_exchanges", [])
        sell_exchanges = settings.get("sell_exchanges", [])
        min_profit = settings.get("min_profit", 1.0)
        max_profit = settings.get("max_profit", 20.0)
        min_24h_vol_usd = settings.get("min_24h_vol_usd", 100000.0)
        exclude_chains = settings.get("exclude_chains", ["ETH"])
        include_all_chains = settings.get("include_all_chains", False)
        
        if not buy_exchanges or not sell_exchanges:
            return []
        
        # Initialize exchanges
        ex_objs = {}
        all_exchanges = set(buy_exchanges + sell_exchanges)
        
        logger.info(f"Initializing {len(all_exchanges)} exchanges...")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_ex = {executor.submit(init_exchange_with_retry, ex_id): ex_id for ex_id in all_exchanges}
            for future in concurrent.futures.as_completed(future_to_ex):
                ex_id = future_to_ex[future]
                try:
                    ex = future.result()
                    if ex:
                        ex_objs[ex_id] = ex
                except Exception as e:
                    logger.error(f"Error initializing {ex_id}: {e}")
        
        if not ex_objs:
            return []
        
        # Get tickers for each exchange
        bulk_tickers = {}
        for ex_id, ex in ex_objs.items():
            try:
                tickers = get_exchange_tickers(ex, ex_id)
                bulk_tickers[ex_id] = tickers
                logger.info(f"{ex_id}: Got {len(tickers)} tickers")
            except Exception as e:
                logger.error(f"Error getting tickers for {ex_id}: {e}")
                bulk_tickers[ex_id] = {}
        
        results = []
        
        # Compare across exchange pairs
        for buy_id in buy_exchanges:
            for sell_id in sell_exchanges:
                if buy_id == sell_id:
                    continue
                
                if buy_id not in ex_objs or sell_id not in ex_objs:
                    continue
                
                buy_ex = ex_objs[buy_id]
                sell_ex = ex_objs[sell_id]
                buy_tickers = bulk_tickers.get(buy_id, {})
                sell_tickers = bulk_tickers.get(sell_id, {})
                
                # Find common symbols
                buy_symbols = set(buy_tickers.keys())
                sell_symbols = set(sell_tickers.keys())
                common_symbols = buy_symbols & sell_symbols
                
                # Filter valid symbols
                valid_symbols = []
                for sym in common_symbols:
                    if symbol_is_valid(sym, buy_ex) and symbol_is_valid(sym, sell_ex):
                        valid_symbols.append(sym)
                
                logger.info(f"{buy_id}->{sell_id}: Checking {len(valid_symbols)} common symbols")
                
                # Check each symbol
                for sym in valid_symbols[:500]:  # Limit for performance
                    buy_ticker = buy_tickers.get(sym)
                    sell_ticker = sell_tickers.get(sym)
                    
                    if not buy_ticker or not sell_ticker:
                        continue
                    
                    if not is_ticker_fresh(buy_ticker) or not is_ticker_fresh(sell_ticker):
                        continue
                    
                    buy_price = market_price_from_ticker(buy_ticker)
                    sell_price = market_price_from_ticker(sell_ticker)
                    
                    if not buy_price or not sell_price or buy_price <= 0:
                        continue
                    
                    # Check for absurd price differences
                    price_diff = abs(sell_price - buy_price) / buy_price
                    if price_diff > 0.5:  # More than 50% difference is likely error
                        continue
                    
                    # Get fees
                    buy_fee = buy_ex.markets.get(sym, {}).get("taker", 0.001) or 0.001
                    sell_fee = sell_ex.markets.get(sym, {}).get("taker", 0.001) or 0.001
                    
                    # Calculate spread and profit
                    spread = (sell_price - buy_price) / buy_price * 100.0
                    profit_after = spread - (buy_fee * 100.0 + sell_fee * 100.0)
                    
                    if profit_after < min_profit or profit_after > max_profit:
                        continue
                    
                    # Check volumes
                    buy_vol_usd = calculate_usd_volume(buy_ticker, sym, buy_price, buy_id)
                    sell_vol_usd = calculate_usd_volume(sell_ticker, sym, sell_price, sell_id)
                    
                    if buy_vol_usd < min_24h_vol_usd or sell_vol_usd < min_24h_vol_usd:
                        continue
                    
                    # Check blockchain compatibility
                    base, quote = parse_symbol(sym)
                    chain, w_ok, d_ok = choose_common_chain(
                        buy_ex, sell_ex, base, exclude_chains, include_all_chains
                    )
                    
                    # Skip if no valid chain or withdrawal/deposit not enabled
                    if chain.startswith("❌") or w_ok != "✅" or d_ok != "✅":
                        continue
                    
                    # Skip if chain excluded (unless include_all_chains is True)
                    if not include_all_chains and chain in exclude_chains:
                        continue
                    
                    # Create result
                    key = f"{sym}|{buy_id}>{sell_id}"
                    observed, expiry = calculate_stability(key, profit_after)
                    
                    results.append({
                        "key": key,
                        "pair": sym,
                        "quote": quote,
                        "buy_exchange": EXCHANGE_NAMES.get(buy_id, buy_id),
                        "buy_exchange_id": buy_id,
                        "buy_price": round(buy_price, 8),
                        "sell_exchange": EXCHANGE_NAMES.get(sell_id, sell_id),
                        "sell_exchange_id": sell_id,
                        "sell_price": round(sell_price, 8),
                        "spread_pct": round(spread, 4),
                        "profit_after": round(profit_after, 4),
                        "buy_volume": fmt_usd(buy_vol_usd),
                        "buy_volume_raw": buy_vol_usd,
                        "sell_volume": fmt_usd(sell_vol_usd),
                        "sell_volume_raw": sell_vol_usd,
                        "withdraw_ok": w_ok,
                        "deposit_ok": d_ok,
                        "blockchain": chain,
                        "stability": observed,
                        "expiry": expiry,
                        "timestamp": datetime.now().isoformat()
                    })
        
        # Update opportunity tracking
        update_opportunity_tracking(results)
        
        # Sort by profit
        results.sort(key=lambda x: x["profit_after"], reverse=True)
        
        # Add index numbers
        for i, result in enumerate(results, 1):
            result["index"] = i
        
        logger.info(f"Scan complete: Found {len(results)} opportunities")
        return results
        
    except Exception as e:
        logger.error(f"Error in scan: {e}", exc_info=True)
        return []

# ------------------- Flask Routes -------------------
@app.route('/')
def index():
    """Serve the main page"""
    return render_template('index.html')

@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Get current settings"""
    settings = load_settings()
    return jsonify({
        "settings": settings,
        "exchanges": TOP_20_CCXT_EXCHANGES,
        "exchange_names": EXCHANGE_NAMES
    })

@app.route('/api/settings', methods=['POST'])
def update_settings():
    """Update settings"""
    try:
        data = request.json
        save_settings(data)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/scan', methods=['POST'])
def scan():
    """Run scan with current settings"""
    try:
        data = request.json
        settings = data.get("settings", {})
        
        # Check cache first
        settings_hash = get_settings_hash(settings)
        current_time = time.time()
        
        if (scan_cache["timestamp"] and 
            scan_cache["settings_hash"] == settings_hash and
            (current_time - scan_cache["timestamp"]) < CACHE_DURATION):
            logger.info("Returning cached results")
            return jsonify({
                "success": True,
                "results": scan_cache["results"],
                "cached": True,
                "timestamp": scan_cache["timestamp"]
            })
        
        # Run new scan
        logger.info("Running new scan...")
        results = run_scan_async(settings)
        
        # Update cache
        scan_cache["results"] = results
        scan_cache["timestamp"] = current_time
        scan_cache["settings_hash"] = settings_hash
        
        return jsonify({
            "success": True,
            "results": results,
            "cached": False,
            "timestamp": current_time,
            "exchange_status": exchange_status
        })
        
    except Exception as e:
        logger.error(f"Scan error: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/status')
def status():
    """Get system status"""
    return jsonify({
        "exchanges": exchange_status,
        "cache_timestamp": scan_cache["timestamp"],
        "opportunities_tracked": len(opportunity_tracker)
    })

@app.route('/api/export/csv')
def export_csv():
    """Export results as CSV"""
    try:
        results = scan_cache.get("results", [])
        if not results:
            return jsonify({"error": "No data to export"}), 400
        
        df = pd.DataFrame(results)
        csv_data = df.to_csv(index=False)
        
        # Create response with CSV file
        from io import StringIO
        output = StringIO()
        output.write(csv_data)
        
        return send_file(
            StringIO(csv_data),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'arbitrage_opportunities_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/test/exchange/<exchange_id>')
def test_exchange(exchange_id):
    """Test exchange connectivity"""
    try:
        if exchange_id not in TOP_CCXT_EXCHANGES:
            return jsonify({"error": "Invalid exchange"}), 400
        
        ex = init_exchange_with_retry(exchange_id)
        if ex:
            return jsonify({
                "success": True,
                "exchange": exchange_id,
                "name": EXCHANGE_NAMES.get(exchange_id),
                "markets": len(ex.markets),
                "status": "connected"
            })
        else:
            return jsonify({
                "success": False,
                "exchange": exchange_id,
                "status": "failed"
            })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ------------------- Main -------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
