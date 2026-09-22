import os
import time
import requests
import threading
from datetime import datetime
import pytz
from flask import Flask
import yfinance as yf

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
IST = pytz.timezone("Asia/Kolkata")

app = Flask(__name__)

@app.route('/')
def home():
    return "NSE Live Paper Bot Running!"

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
        print(f"Error: {e}")

def get_live_nse_price(symbol):
    ticker_map = {
        "NIFTY 50": "^NSEI",
        "BANKNIFTY": "^NSEBANK",
        "SENSEX": "^BSESN"
    }
    try:
        ticker = yf.Ticker(ticker_map[symbol])
        data = ticker.history(period="1d", interval="1m")
        if not data.empty:
            return round(float(data['Close'].iloc[-1]), 2)
    except Exception as e:
        print(f"Data error for {symbol}: {e}")
    return None

class LivePaperBot:
    def __init__(self):
        self.indices = {
            "NIFTY 50": {"step": 50, "hedge": 200, "active": False, "entry_spot": 0},
            "BANKNIFTY": {"step": 100, "hedge": 400, "active": False, "entry_spot": 0},
            "SENSEX": {"step": 100, "hedge": 500, "active": False, "entry_spot": 0}
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

    def execute_live_paper_trade(self, symbol):
        spot = get_live_nse_price(symbol)
        if not spot:
            return

        step = self.indices[symbol]["step"]
        hedge = self.indices[symbol]["hedge"]
        atm_strike = round(spot / step) * step

        self.indices[symbol]["active"] = True
        self.indices[symbol]["entry_spot"] = spot

        msg = (
            f"⚡ *[LIVE PAPER TRADE EXECUTED]*\n\n"
            f"🎯 *Index:* `{symbol}`\n"
            f"📈 *Live Spot Price:* `₹{spot}`\n"
            f"🛡️ *Hedged Strategy:* Bull Put Spread\n"
            f"• *SELL Main:* {atm_strike} PE\n"
            f"• *BUY Hedge:* {atm_strike - hedge} PE\n"
            f"⏱️ *Exchange Time:* {datetime.now(IST).strftime('%I:%M:%S %p')}\n"
            f"✅ *Verification:* Tame tamara broker terminal sathe spot price check kari shako cho."
        )
        send_telegram_msg(msg)

    def send_live_mtm(self):
        msg = "📊 *[LIVE HOURLY PnL & STATUS]*\n\n"
        has_trades = False
        for sym, d in self.indices.items():
            if d["active"]:
                has_trades = True
                curr_spot = get_live_nse_price(sym)
                diff = round(curr_spot - d["entry_spot"], 2) if curr_spot else 0
                msg += f"• `{sym}`: Entry @ {d['entry_spot']} | Current @ {curr_spot} (Diff: {diff} pts)\n"

        if has_trades:
            send_telegram_msg(msg)

    def run_loop(self):
        time.sleep(3)
        send_telegram_msg("🤖 *Live NSE Market Data Paper Engine Started!*")

        while True:
            now = datetime.now(IST)

            if self.is_market_open():
                # Morning Entry (09:20 AM)
                if now.hour == 9 and now.minute == 20:
                    for sym in self.indices:
                        if not self.indices[sym]["active"]:
                            self.execute_live_paper_trade(sym)
                            break

                # Hourly Live Tracking
                if now.minute == 0 and self.last_pnl_hour != now.hour:
                    self.send_live_mtm()
                    self.last_pnl_hour = now.hour

                # 03:15 PM Square-off
                if now.hour == 15 and now.minute == 15:
                    for sym in self.indices:
                        if self.indices[sym]["active"]:
                            self.indices[sym]["active"] = False
                            send_telegram_msg(f"🛑 *[INTRADAY CLOSE]* `{sym}` closed at 03:15 PM.")

            time.sleep(30)

if __name__ == "__main__":
    bot = LivePaperBot()
    server_thread = threading.Thread(target=run_web_server, daemon=True)
    server_thread.start()
    bot.run_loop()
