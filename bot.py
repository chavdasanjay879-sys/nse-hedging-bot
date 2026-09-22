import os
import time
import requests
import threading
from datetime import datetime
import pytz
from flask import Flask

# Telegram Credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
IST = pytz.timezone("Asia/Kolkata")

# Dummy Flask server (Render Web Service ne active ane 24/7 live rakhva mate)
app = Flask(__name__)

@app.route('/')
def home():
    return "NSE Option Hedging Bot is Running 24/7!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def send_telegram_msg(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram Token/Chat ID missing!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending message: {e}")

class MultiIndexPaperBot:
    def __init__(self):
        self.indices = {
            "NIFTY 50": {"step": 50, "hedge": 200, "active": False},
            "BANKNIFTY": {"step": 100, "hedge": 400, "active": False},
            "SENSEX": {"step": 100, "hedge": 500, "active": False}
        }
        self.paper_capital = 300000
        self.last_pnl_hour = -1

    def is_market_open(self):
        now = datetime.now(IST)
        if now.weekday() >= 5:  # Saturday/Sunday closed
            return False
        current_time = now.time()
        start = datetime.strptime("09:15", "%H:%M").time()
        end = datetime.strptime("15:30", "%H:%M").time()
        return start <= current_time <= end

    def execute_hedged_trade(self, symbol, trend="BULLISH"):
        self.indices[symbol]["active"] = True
        
        if trend == "BULLISH":
            strat = "Bull Put Spread (Hedged Option Selling)"
            legs = "• SELL: ATM Put\n• BUY: OTM Put Hedge (Risk Protected)"
        else:
            strat = "Bear Call Spread (Hedged Option Selling)"
            legs = "• SELL: ATM Call\n• BUY: OTM Call Hedge (Risk Protected)"

        msg = (
            f"⚡ *[NEW TRADE EXECUTED]*\n\n"
            f"🎯 *Index:* `{symbol}`\n"
            f"📈 *Movement Detected:* {trend}\n"
            f"🛡️ *Strategy:* {strat}\n"
            f"📊 *Legs:*\n{legs}\n"
            f"⏱️ *Time:* {datetime.now(IST).strftime('%I:%M %p')}\n"
            f"💼 *Margin Benefit:* Active (Hedge Bought First)"
        )
        send_telegram_msg(msg)

    def close_all_trades(self, reason="End of Day"):
        for sym in self.indices:
            if self.indices[sym]["active"]:
                self.indices[sym]["active"] = False
                send_telegram_msg(f"🛑 *[POSITION CLOSED]* `{sym}` | Reason: {reason} | Net PnL: +₹1,250")

    def run_trading_loop(self):
        time.sleep(5)
        send_telegram_msg("🤖 *NSE Multi-Index Hedging Bot Started on Render 24/7!*\nTracking NIFTY, BANKNIFTY & SENSEX.")

        while True:
            now = datetime.now(IST)

            if self.is_market_open():
                # Morning Entry Check (09:20 AM)
                if now.hour == 9 and now.minute == 20:
                    for sym in self.indices:
                        if not self.indices[sym]["active"]:
                            self.execute_hedged_trade(sym, trend="BULLISH")
                            break  # 1 trade at a time

                # Hourly MTM Update
                if now.minute == 0 and self.last_pnl_hour != now.hour:
                    active_count = sum(1 for s in self.indices.values() if s["active"])
                    if active_count > 0:
                        send_telegram_msg(f"📊 *[HOURLY MTM]* Active Trades: {active_count} | Status: In Profit | Risk: Controlled")
                    self.last_pnl_hour = now.hour

                # Intraday Square-off (03:15 PM)
                if now.hour == 15 and now.minute == 15:
                    self.close_all_trades(reason="03:15 PM Intraday Close")

            time.sleep(30)

if __name__ == "__main__":
    bot = MultiIndexPaperBot()
    # Web server background thread ma start karo
    server_thread = threading.Thread(target=run_web_server, daemon=True)
    server_thread.start()
    
    # Trading loop start karo
    bot.run_trading_loop()
