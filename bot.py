import os
import time
import requests
import threading
from datetime import datetime
import pytz
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
IST = pytz.timezone("Asia/Kolkata")

app = Flask(__name__)

@app.route('/')
def home():
    return "NSE Smart Hedging Bot Running 24/7!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def send_telegram_msg(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def get_market_data(symbol):
    ticker_map = {
        "NIFTY 50": "^NSEI",
        "BANKNIFTY": "^NSEBANK",
        "SENSEX": "^BSESN"
    }
    try:
        ticker = yf.Ticker(ticker_map[symbol])
        df = ticker.history(period="5d", interval="5m")
        if not df.empty and len(df) >= 20:
            return df
    except Exception as e:
        print(f"Data error for {symbol}: {e}")
    return None

def analyze_regime(df):
    close = df['Close']
    ema_9 = close.ewm(span=9, adjust=False).mean()
    ema_21 = close.ewm(span=21, adjust=False).mean()
    
    current_close = close.iloc[-1]
    curr_ema9 = ema_9.iloc[-1]
    curr_ema21 = ema_21.iloc[-1]
    
    recent_high = df['High'].iloc[-12:].max()
    recent_low = df['Low'].iloc[-12:].min()
    range_pct = (recent_high - recent_low) / current_close * 100

    if curr_ema9 > curr_ema21 and current_close > curr_ema9 and range_pct > 0.30:
        return "BULLISH_TREND"
    elif curr_ema9 < curr_ema21 and current_close < curr_ema9 and range_pct > 0.30:
        return "BEARISH_TREND"
    else:
        return "SIDEWAYS_RANGE"

def estimate_premium(spot, strike, opt_type):
    distance = abs(spot - strike)
    base_atm = spot * 0.007
    if opt_type == "CE":
        intrinsic = max(0, spot - strike)
    else:
        intrinsic = max(0, strike - spot)
    extrinsic = max(10.0, base_atm - (distance * 0.35))
    return round(intrinsic + extrinsic, 2)

class RealisticPaperTradingBot:
    def __init__(self):
        self.virtual_capital = 200000.0
        self.starting_capital = 200000.0
        self.indices = {
            "NIFTY 50": {"step": 50, "hedge": 200, "lot_size": 25, "active": False, "trade": None},
            "BANKNIFTY": {"step": 100, "hedge": 400, "lot_size": 15, "active": False, "trade": None},
            "SENSEX": {"step": 100, "hedge": 500, "lot_size": 10, "active": False, "trade": None}
        }
        self.last_update_id = 0

    def is_market_open(self):
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return False
        current_time = now.time()
        start = datetime.strptime("09:15", "%H:%M").time()
        end = datetime.strptime("15:30", "%H:%M").time()
        return start <= current_time <= end

    def execute_live_order(self, symbol):
        df = get_market_data(symbol)
        if df is None:
            return

        spot = round(float(df['Close'].iloc[-1]), 2)
        regime = analyze_regime(df)
        step = self.indices[symbol]["step"]
        hedge = self.indices[symbol]["hedge"]
        lot = self.indices[symbol]["lot_size"]
        atm_strike = round(spot / step) * step

        trade_info = {
            "strategy": regime,
            "entry_spot": spot,
            "lot_size": lot,
            "legs": []
        }

        if regime == "BULLISH_TREND":
            sell_strike = atm_strike
            buy_strike = atm_strike - hedge
            sell_prem = estimate_premium(spot, sell_strike, "PE")
            buy_prem = estimate_premium(spot, buy_strike, "PE")
            
            trade_info["legs"].append({"action": "SELL", "strike": sell_strike, "type": "PE", "entry_prem": sell_prem})
            trade_info["legs"].append({"action": "BUY", "strike": buy_strike, "type": "PE", "entry_prem": buy_prem})
            strat_name = "Bull Put Credit Spread"

        elif regime == "BEARISH_TREND":
            sell_strike = atm_strike
            buy_strike = atm_strike + hedge
            sell_prem = estimate_premium(spot, sell_strike, "CE")
            buy_prem = estimate_premium(spot, buy_strike, "CE")
            
            trade_info["legs"].append({"action": "SELL", "strike": sell_strike, "type": "CE", "entry_prem": sell_prem})
            trade_info["legs"].append({"action": "BUY", "strike": buy_strike, "type": "CE", "entry_prem": buy_prem})
            strat_name = "Bear Call Credit Spread"

        else:
            sell_ce = atm_strike + step
            buy_ce = atm_strike + step + hedge
            sell_pe = atm_strike - step
            buy_pe = atm_strike - step - hedge

            trade_info["legs"].append({"action": "SELL", "strike": sell_ce, "type": "CE", "entry_prem": estimate_premium(spot, sell_ce, "CE")})
            trade_info["legs"].append({"action": "BUY", "strike": buy_ce, "type": "CE", "entry_prem": estimate_premium(spot, buy_ce, "CE")})
            trade_info["legs"].append({"action": "SELL", "strike": sell_pe, "type": "PE", "entry_prem": estimate_premium(spot, sell_pe, "PE")})
            trade_info["legs"].append({"action": "BUY", "strike": buy_pe, "type": "PE", "entry_prem": estimate_premium(spot, buy_pe, "PE")})
            strat_name = "Hedged Iron Condor"

        self.indices[symbol]["active"] = True
        self.indices[symbol]["trade"] = trade_info

        legs_text = ""
        for leg in trade_info["legs"]:
            legs_text += f"• {leg['action']} `{leg['strike']} {leg['type']}` @ ₹{leg['entry_prem']}\n"

        msg = (
            f"⚡ *[LIVE ORDER EXECUTED]*\n\n"
            f"🎯 *Index:* `{symbol}` (Qty: {lot})\n"
            f"📈 *Live Spot:* `₹{spot}`\n"
            f"📊 *Market Trend:* `{regime}`\n"
            f"🛡️ *Strategy:* *{strat_name}*\n\n"
            f"📋 *Order Legs & Premiums:*\n{legs_text}\n"
            f"💼 *Account Capital:* `₹{self.virtual_capital:,.2f}`\n"
            f"⏱️ *Time:* `{datetime.now(IST).strftime('%I:%M:%S %p')}`"
        )
        send_telegram_msg(msg)

    def calculate_trade_pnl(self, symbol, current_spot):
        trade = self.indices[symbol]["trade"]
        if not trade:
            return 0.0
        
        total_pnl = 0.0
        lot = trade["lot_size"]

        for leg in trade["legs"]:
            curr_prem = estimate_premium(current_spot, leg["strike"], leg["type"])
            if leg["action"] == "SELL":
                pnl = (leg["entry_prem"] - curr_prem) * lot
            else:
                pnl = (curr_prem - leg["entry_prem"]) * lot
            total_pnl += pnl

        return round(total_pnl, 2)

    def get_status_summary(self):
        has_trades = False
        net_running_pnl = 0.0
        msg = "📊 *[LIVE STATUS & P&L REPORT]*\n\n"

        for sym, d in self.indices.items():
            if d["active"] and d["trade"] is not None:
                has_trades = True
                df = get_market_data(sym)
                curr_spot = round(float(df['Close'].iloc[-1]), 2) if df is not None else d["trade"]["entry_spot"]
                pnl = self.calculate_trade_pnl(sym, curr_spot)
                net_running_pnl += pnl
                status_emoji = "🟢" if pnl >= 0 else "🔴"
                msg += (
                    f"• `{sym}` ({d['trade']['strategy']})\n"
                    f"  Spot: ₹{curr_spot} | P&L: {status_emoji} *₹{pnl:+,.2f}*\n\n"
                )

        if not has_trades:
            return "ℹ️ Atyare koi active open position nathi. Market open ma bot trade execute karshe."

        net_status = "🟢 PROFIT" if net_running_pnl >= 0 else "🔴 LOSS"
        msg += (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💵 *Net Running P&L:* {net_status} `₹{net_running_pnl:+,.2f}`\n"
            f"💼 *Virtual Balance:* `₹{(self.virtual_capital + net_running_pnl):,.2f}`"
        )
        return msg

    def close_all_trades(self):
        day_pnl = 0.0
        msg = "🛑 *[INTRADAY SQUARE-OFF (03:15 PM)]*\n\n"
        has_trades = False

        for sym, d in self.indices.items():
            if d["active"] and d["trade"] is not None:
                has_trades = True
                df = get_market_data(sym)
                curr_spot = round(float(df['Close'].iloc[-1]), 2) if df is not None else d["trade"]["entry_spot"]
                pnl = self.calculate_trade_pnl(sym, curr_spot)
                day_pnl += pnl
                d["active"] = False
                d["trade"] = None
                status_emoji = "🟢" if pnl >= 0 else "🔴"
                msg += f"• `{sym}` Closed | P&L: {status_emoji} *₹{pnl:+,.2f}*\n"

        if has_trades:
            self.virtual_capital += day_pnl
            net_status = "🟢 NET PROFIT" if day_pnl >= 0 else "🔴 NET LOSS"
            msg += (
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🏁 *Day Summary:* {net_status} `₹{day_pnl:+,.2f}`\n"
                f"💰 *Updated Total Capital:* `₹{self.virtual_capital:,.2f}`\n"
                f"📈 *ROI:* `{(self.virtual_capital - self.starting_capital) / self.starting_capital * 100:+.2f}%`"
            )
            send_telegram_msg(msg)

    def check_telegram_commands(self):
        if not TELEGRAM_BOT_TOKEN:
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        params = {"offset": self.last_update_id + 1, "timeout": 1}
        try:
            res = requests.get(url, params=params, timeout=5).json()
            if "result" in res:
                for update in res["result"]:
                    self.last_update_id = update["update_id"]
                    if "message" in update and "text" in update["message"]:
                        text = update["message"]["text"].strip().lower()
                        chat_id = str(update["message"]["chat"]["id"])
                        if chat_id == str(TELEGRAM_CHAT_ID):
                            if text in ["/status", "status"]:
                                send_telegram_msg(self.get_status_summary())
        except Exception:
            pass

    def run_loop(self):
        time.sleep(3)
        send_telegram_msg("🚀 *NSE Dynamic Hedging Bot Active with 15-Min Live P&L Updates!*")

        last_pnl_check = time.time()
        squared_off_today = False

        while True:
            now = datetime.now(IST)

            # Interactive /status listener check
            self.check_telegram_commands()

            if self.is_market_open():
                # Entry scan
                for sym in self.indices:
                    if not self.indices[sym]["active"]:
                        self.execute_live_order(sym)
                        time.sleep(1)

                # Automatic Status update every 15 minutes (900 seconds)
                if time.time() - last_pnl_check >= 900:
                    send_telegram_msg(self.get_status_summary())
                    last_pnl_check = time.time()

                # 03:15 PM Square-off
                if now.hour == 15 and now.minute == 15 and not squared_off_today:
                    self.close_all_trades()
                    squared_off_today = True

            # Reset flag after market closes
            if now.hour == 16:
                squared_off_today = False

            time.sleep(5)

if __name__ == "__main__":
    bot = RealisticPaperTradingBot()
    server_thread = threading.Thread(target=run_web_server, daemon=True)
    server_thread.start()
    bot.run_loop()
