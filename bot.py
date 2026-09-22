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
    return "NSE Multi-Strategy Smart Hedging Engine Running!"

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
        print(f"Telegram Notification Error: {e}")

def get_market_data(symbol):
    ticker_map = {
        "NIFTY 50": "^NSEI",
        "BANKNIFTY": "^NSEBANK",
        "SENSEX": "^BSESN"
    }
    try:
        ticker = yf.Ticker(ticker_map[symbol])
        df = ticker.history(period="5d", interval="5m")
        if not df.empty and len(df) >= 30:
            return df
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
    return None

def analyze_regime(df):
    """
    EMAs & Price action used to classify regime:
    - BULLISH (Directional Up)
    - BEARISH (Directional Down)
    - SIDEWAYS (Non-Directional Rangebound)
    """
    close = df['Close']
    ema_9 = close.ewm(span=9, adjust=False).mean()
    ema_21 = close.ewm(span=21, adjust=False).mean()
    
    current_close = close.iloc[-1]
    curr_ema9 = ema_9.iloc[-1]
    curr_ema21 = ema_21.iloc[-1]
    
    # 5-period range ratio to measure consolidation
    recent_high = df['High'].iloc[-12:].max()
    recent_low = df['Low'].iloc[-12:].min()
    range_pct = (recent_high - recent_low) / current_close * 100

    # Directional Conditions
    if curr_ema9 > curr_ema21 and current_close > curr_ema9 and range_pct > 0.35:
        return "BULLISH_TREND"
    elif curr_ema9 < curr_ema21 and current_close < curr_ema9 and range_pct > 0.35:
        return "BEARISH_TREND"
    else:
        return "SIDEWAYS_RANGE"

class SmartHedgingBot:
    def __init__(self):
        self.indices = {
            "NIFTY 50": {"step": 50, "hedge": 200, "active": False, "strategy": None, "entry_spot": 0},
            "BANKNIFTY": {"step": 100, "hedge": 400, "active": False, "strategy": None, "entry_spot": 0},
            "SENSEX": {"step": 100, "hedge": 500, "active": False, "strategy": None, "entry_spot": 0}
        }
        self.last_pnl_hour = -1

    def is_market_open(self):
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return False
        current_time = now.time()
        start = datetime.strptime("09:15", "%H:%M").time()
        end = datetime.strptime("15:30", "%H:%M").time()
        return start <= current_time <= end

    def execute_smart_strategy(self, symbol):
        df = get_market_data(symbol)
        if df is None:
            return

        spot = round(float(df['Close'].iloc[-1]), 2)
        regime = analyze_regime(df)
        step = self.indices[symbol]["step"]
        hedge = self.indices[symbol]["hedge"]
        atm_strike = round(spot / step) * step

        self.indices[symbol]["active"] = True
        self.indices[symbol]["entry_spot"] = spot
        self.indices[symbol]["strategy"] = regime

        if regime == "BULLISH_TREND":
            strat_name = "Bull Put Credit Spread (Directional Up)"
            legs = (
                f"• SELL Main: `{atm_strike} PE`\n"
                f"• BUY Hedge: `{atm_strike - hedge} PE` (Margin & Capital Shield)"
            )
        elif regime == "BEARISH_TREND":
            strat_name = "Bear Call Credit Spread (Directional Down)"
            legs = (
                f"• SELL Main: `{atm_strike} CE`\n"
                f"• BUY Hedge: `{atm_strike + hedge} CE` (Margin & Capital Shield)"
            )
        else:
            strat_name = "Hedged Iron Condor (Non-Directional Rangebound)"
            legs = (
                f"• SELL: `{atm_strike + step} CE` & `{atm_strike - step} PE`\n"
                f"• BUY Wings: `{atm_strike + step + hedge} CE` & `{atm_strike - step - hedge} PE`"
            )

        msg = (
            f"🧠 *[AI AUTO-STRATEGY EXECUTED]*\n\n"
            f"🎯 *Index:* `{symbol}`\n"
            f"📈 *Live Spot Price:* `₹{spot}`\n"
            f"📊 *Market Regime:* `{regime}`\n"
            f"🛡️ *Selected Strategy:* *{strat_name}*\n\n"
            f"📋 *Order Legs:*\n{legs}\n\n"
            f"⏱️ *Time:* `{datetime.now(IST).strftime('%I:%M:%S %p')}`\n"
            f"✅ *Mode:* Dynamic Risk Defined Hedging"
        )
        send_telegram_msg(msg)

    def send_hourly_mtm(self):
        msg = "📊 *[HOURLY MTM & REGIME UPDATE]*\n\n"
        has_trades = False
        for sym, d in self.indices.items():
            if d["active"]:
                has_trades = True
                df = get_market_data(sym)
                curr_spot = round(float(df['Close'].iloc[-1]), 2) if df is not None else d['entry_spot']
                diff = round(curr_spot - d["entry_spot"], 2)
                msg += f"• `{sym}` ({d['strategy']})\n  Entry: {d['entry_spot']} | Current: {curr_spot} | Movement: {diff} pts\n\n"

        if has_trades:
            send_telegram_msg(msg)

    def run_loop(self):
        time.sleep(3)
        send_telegram_msg("🚀 *NSE Dynamic Directional & Non-Directional Engine Activated!*")

        while True:
            now = datetime.now(IST)

            if self.is_market_open():
                # Scan & Enter after opening volatility (09:20 AM)
                if now.hour == 9 and now.minute == 20:
                    for sym in self.indices:
                        if not self.indices[sym]["active"]:
                            self.execute_smart_strategy(sym)

                # Hourly Monitoring
                if now.minute == 0 and self.last_pnl_hour != now.hour:
                    self.send_hourly_mtm()
                    self.last_pnl_hour = now.hour

                # Intraday Square-off at 03:15 PM
                if now.hour == 15 and now.minute == 15:
                    for sym in self.indices:
                        if self.indices[sym]["active"]:
                            self.indices[sym]["active"] = False
                            send_telegram_msg(f"🛑 *[INTRADAY SQUARE-OFF]* Positions closed for `{sym}` at 03:15 PM.")

            time.sleep(30)

if __name__ == "__main__":
    bot = SmartHedgingBot()
    server_thread = threading.Thread(target=run_web_server, daemon=True)
    server_thread.start()
    bot.run_loop()
