import os
import time
import math
import threading
from datetime import datetime, time as dt_time

import requests
import pytz
import numpy as np
import pandas as pd
import yfinance as yf
from flask import Flask


# ============================================================
# ADVANCED OPTIONS SELLING + MANDATORY HEDGING ENGINE
# PAPER TRADING ONLY
# ============================================================

IST = pytz.timezone("Asia/Kolkata")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

app = Flask(__name__)


# ============================================================
# GLOBAL SETTINGS
# ============================================================

STARTING_CAPITAL = 200000.0

CACHE_SECONDS = 60

STATUS_INTERVAL = 900

MAX_DAILY_LOSS = 5000.0

# PROFIT LOCK: once running P&L reaches this amount,
# the bot locks this minimum profit and closes all open
# positions if running P&L falls back to the lock level.
PROFIT_LOCK_TRIGGER = 5000.0
PROFIT_LOCK_AMOUNT = 5000.0

# TRAILING PROFIT LOCK
# Once profit reaches the trigger, the bot remembers the highest
# running P&L of the day and moves the protected floor upward.
# The floor never moves downward.
# 50% of every profit gained above the initial ₹5,000 trigger is
# protected. Example: peak ₹7,000 -> floor ₹6,000.
TRAILING_LOCK_SHARE = 0.50

MIN_CONFIDENCE = 68

MAX_VIX_FOR_NEW_TRADE = 30.0

MIN_VIX_FOR_CREDIT_SELLING = 10.0

NO_TRADE_COOLDOWN = 300

MARKET_START = dt_time(9, 15)

MARKET_END = dt_time(15, 30)

SQUARE_OFF_TIME = dt_time(15, 15)


# ============================================================
# INDEX CONFIG
# ============================================================

INDEX_CONFIG = {

    "NIFTY 50": {
        "ticker": "^NSEI",
        "vix": "^INDIAVIX",
        "step": 50,
        "hedge": 200,
        "lot_size": 65,
    },

    "BANKNIFTY": {
        "ticker": "^NSEBANK",
        "vix": "^INDIAVIX",
        "step": 100,
        "hedge": 400,
        "lot_size": 30,
    },

    "SENSEX": {
        "ticker": "^BSESN",
        "vix": "^INDIAVIX",
        "step": 100,
        "hedge": 500,
        "lot_size": 20,
    },
}


# ============================================================
# DATA CACHE
# ============================================================

MARKET_CACHE = {}

VIX_CACHE = {}

OPTION_CACHE = {}


# ============================================================
# THREAD LOCK
# ============================================================

STATE_LOCK = threading.RLock()


# ============================================================
# WEB SERVER
# ============================================================

@app.route("/")
def home():
    return (
        "Advanced Options Selling + "
        "Mandatory Hedging Engine is running."
    )


@app.route("/health")
def health():
    return {
        "status": "ok",
        "engine": "advanced-options-selling",
        "mode": "paper-trading",
        "time": datetime.now(IST).isoformat()
    }


def run_web_server():

    port = int(
        os.environ.get("PORT", 8080)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        print("Telegram token missing.")
        return False

    if not TELEGRAM_CHAT_ID:
        print("Telegram chat ID missing.")
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=8
        )

        if response.status_code != 200:

            print(
                "Telegram error:",
                response.status_code,
                response.text[:300]
            )

            return False

        return True

    except Exception as exc:

        print(
            "Telegram exception:",
            exc
        )

        return False


# ============================================================
# MARKET DATA
# ============================================================

def get_market_data(symbol):

    config = INDEX_CONFIG.get(symbol)

    if not config:
        return None

    now = time.time()

    cached = MARKET_CACHE.get(symbol)

    if cached:

        df, timestamp = cached

        if now - timestamp < CACHE_SECONDS:

            return df.copy()

    try:

        ticker = yf.Ticker(
            config["ticker"]
        )

        df = ticker.history(
            period="5d",
            interval="5m",
            auto_adjust=False
        )

        if (
            df is not None
            and not df.empty
            and len(df) >= 60
        ):

            df = df.copy()

            df = df.dropna(
                subset=[
                    "Open",
                    "High",
                    "Low",
                    "Close"
                ]
            )

            MARKET_CACHE[symbol] = (
                df,
                now
            )

            return df.copy()

    except Exception as exc:

        print(
            f"{symbol} market-data error:",
            exc
        )

    if cached:

        print(
            f"{symbol}: using stale cached data"
        )

        return cached[0].copy()

    return None


# ============================================================
# INDIA VIX
# ============================================================

def get_india_vix():

    now = time.time()

    cached = VIX_CACHE.get("INDIA_VIX")

    if cached:

        value, timestamp = cached

        if now - timestamp < CACHE_SECONDS:

            return value

    try:

        ticker = yf.Ticker(
            "^INDIAVIX"
        )

        df = ticker.history(
            period="5d",
            interval="5m",
            auto_adjust=False
        )

        if (
            df is not None
            and not df.empty
        ):

            value = float(
                df["Close"].iloc[-1]
            )

            VIX_CACHE["INDIA_VIX"] = (
                value,
                now
            )

            return value

    except Exception as exc:

        print(
            "India VIX error:",
            exc
        )

    if cached:
        return cached[0]

    return None


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def calculate_indicators(df):

    data = df.copy()

    close = data["Close"]

    high = data["High"]

    low = data["Low"]

    volume = (
        data["Volume"]
        if "Volume" in data.columns
        else pd.Series(
            1,
            index=data.index
        )
    )

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    data["EMA9"] = close.ewm(
        span=9,
        adjust=False
    ).mean()

    data["EMA21"] = close.ewm(
        span=21,
        adjust=False
    ).mean()

    data["EMA50"] = close.ewm(
        span=50,
        adjust=False
    ).mean()

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    delta = close.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

    data["RSI"] = (
        100
        - (100 / (1 + rs))
    )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    ema12 = close.ewm(
        span=12,
        adjust=False
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False
    ).mean()

    data["MACD"] = (
        ema12 - ema26
    )

    data["MACD_SIGNAL"] = (
        data["MACD"]
        .ewm(
            span=9,
            adjust=False
        )
        .mean()
    )

    data["MACD_HIST"] = (
        data["MACD"]
        - data["MACD_SIGNAL"]
    )

    # --------------------------------------------------------
    # ATR
    # --------------------------------------------------------

    previous_close = close.shift(1)

    tr1 = high - low

    tr2 = (
        high
        - previous_close
    ).abs()

    tr3 = (
        low
        - previous_close
    ).abs()

    true_range = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)

    data["ATR"] = (
        true_range
        .ewm(
            span=14,
            adjust=False
        )
        .mean()
    )

    # --------------------------------------------------------
    # ADX
    # --------------------------------------------------------

    up_move = high.diff()

    down_move = -low.diff()

    plus_dm = np.where(
        (up_move > down_move)
        & (up_move > 0),
        up_move,
        0
    )

    minus_dm = np.where(
        (down_move > up_move)
        & (down_move > 0),
        down_move,
        0
    )

    atr14 = (
        true_range
        .ewm(
            span=14,
            adjust=False
        )
        .mean()
    )

    plus_di = (
        100
        * pd.Series(
            plus_dm,
            index=data.index
        ).ewm(
            span=14,
            adjust=False
        ).mean()
        / atr14.replace(
            0,
            np.nan
        )
    )

    minus_di = (
        100
        * pd.Series(
            minus_dm,
            index=data.index
        ).ewm(
            span=14,
            adjust=False
        ).mean()
        / atr14.replace(
            0,
            np.nan
        )
    )

    dx = (
        100
        * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(
            0,
            np.nan
        )
    )

    data["ADX"] = (
        dx.ewm(
            span=14,
            adjust=False
        ).mean()
    )

    data["PLUS_DI"] = plus_di

    data["MINUS_DI"] = minus_di

    # --------------------------------------------------------
    # VWAP
    # --------------------------------------------------------

    typical_price = (
        high
        + low
        + close
    ) / 3

    cumulative_volume = (
        volume.cumsum()
    )

    cumulative_value = (
        typical_price
        * volume
    ).cumsum()

    data["VWAP"] = (
        cumulative_value
        / cumulative_volume.replace(
            0,
            np.nan
        )
    )

    # --------------------------------------------------------
    # Bollinger Bands
    # --------------------------------------------------------

    bb_mid = (
        close
        .rolling(20)
        .mean()
    )

    bb_std = (
        close
        .rolling(20)
        .std()
    )

    data["BB_MID"] = bb_mid

    data["BB_UPPER"] = (
        bb_mid
        + 2 * bb_std
    )

    data["BB_LOWER"] = (
        bb_mid
        - 2 * bb_std
    )

    data["BB_WIDTH"] = (
        (
            data["BB_UPPER"]
            - data["BB_LOWER"]
        )
        / bb_mid
        * 100
    )

    # --------------------------------------------------------
    # Returns / momentum
    # --------------------------------------------------------

    data["RETURN_5"] = (
        close.pct_change(5)
        * 100
    )

    data["RETURN_15"] = (
        close.pct_change(15)
        * 100
    )

    data["RETURN_30"] = (
        close.pct_change(30)
        * 100
    )

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    data["VOL_AVG"] = (
        volume
        .rolling(20)
        .mean()
    )

    data["VOLUME_RATIO"] = (
        volume
        / data["VOL_AVG"].replace(
            0,
            np.nan
        )
    )

    # --------------------------------------------------------
    # Recent range
    # --------------------------------------------------------

    data["RECENT_HIGH"] = (
        high
        .rolling(20)
        .max()
    )

    data["RECENT_LOW"] = (
        low
        .rolling(20)
        .min()
    )

    return data


# ============================================================
# MARKET REGIME + CONFIDENCE
# ============================================================

def analyze_market(symbol, df):

    data = calculate_indicators(
        df
    )

    row = data.iloc[-1]

    spot = float(
        row["Close"]
    )

    score = 0

    reasons = []

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    if (
        row["EMA9"]
        > row["EMA21"]
        > row["EMA50"]
    ):

        score += 20

        reasons.append(
            "EMA bullish alignment"
        )

    elif (
        row["EMA9"]
        < row["EMA21"]
        < row["EMA50"]
    ):

        score -= 20

        reasons.append(
            "EMA bearish alignment"
        )

    else:

        reasons.append(
            "EMA mixed"
        )

    # --------------------------------------------------------
    # VWAP
    # --------------------------------------------------------

    if spot > row["VWAP"]:

        score += 10

        reasons.append(
            "Above VWAP"
        )

    elif spot < row["VWAP"]:

        score -= 10

        reasons.append(
            "Below VWAP"
        )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi = float(
        row["RSI"]
    )

    if 52 <= rsi <= 68:

        score += 10

        reasons.append(
            "Bullish RSI zone"
        )

    elif 32 <= rsi <= 48:

        score -= 10

        reasons.append(
            "Bearish RSI zone"
        )

    elif rsi > 75:

        reasons.append(
            "RSI overbought"
        )

    elif rsi < 25:

        reasons.append(
            "RSI oversold"
        )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    if (
        row["MACD"]
        > row["MACD_SIGNAL"]
        and row["MACD_HIST"] > 0
    ):

        score += 10

        reasons.append(
            "MACD bullish"
        )

    elif (
        row["MACD"]
        < row["MACD_SIGNAL"]
        and row["MACD_HIST"] < 0
    ):

        score -= 10

        reasons.append(
            "MACD bearish"
        )

    # --------------------------------------------------------
    # ADX
    # --------------------------------------------------------

    adx = float(
        row["ADX"]
    )

    if adx >= 25:

        if score > 0:

            score += 10

            reasons.append(
                "Strong bullish trend"
            )

        elif score < 0:

            score -= 10

            reasons.append(
                "Strong bearish trend"
            )

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    if row["RETURN_15"] > 0.20:

        score += 10

        reasons.append(
            "Positive momentum"
        )

    elif row["RETURN_15"] < -0.20:

        score -= 10

        reasons.append(
            "Negative momentum"
        )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if (
        pd.notna(row["VOLUME_RATIO"])
        and row["VOLUME_RATIO"] >= 1.20
    ):

        if score > 0:

            score += 5

            reasons.append(
                "Volume confirmation"
            )

        elif score < 0:

            score -= 5

            reasons.append(
                "Bearish volume confirmation"
            )

    # --------------------------------------------------------
    # VIX
    # --------------------------------------------------------

    vix = get_india_vix()

    if vix is not None:

        if (
            MIN_VIX_FOR_CREDIT_SELLING
            <= vix
            <= MAX_VIX_FOR_NEW_TRADE
        ):

            reasons.append(
                f"India VIX {vix:.2f}: acceptable"
            )

        elif vix > MAX_VIX_FOR_NEW_TRADE:

            reasons.append(
                f"India VIX {vix:.2f}: high risk"
            )

        else:

            reasons.append(
                f"India VIX {vix:.2f}: low premium"
            )

    # --------------------------------------------------------
    # CLASSIFICATION
    # --------------------------------------------------------

    if score >= 35:

        regime = "STRONG_BULLISH"

    elif score >= 15:

        regime = "MILD_BULLISH"

    elif score <= -35:

        regime = "STRONG_BEARISH"

    elif score <= -15:

        regime = "MILD_BEARISH"

    else:

        regime = "NEUTRAL_RANGE"

    confidence = min(
        95,
        50 + abs(score)
    )

    return {
        "spot": spot,
        "score": score,
        "confidence": confidence,
        "regime": regime,
        "vix": vix,
        "adx": adx,
        "rsi": rsi,
        "atr": float(row["ATR"]),
        "vwap": float(row["VWAP"]),
        "bb_width": float(
            row["BB_WIDTH"]
        ),
        "reasons": reasons,
    }


# ============================================================
# NSE OPTION CHAIN
#
# This is OPTIONAL.
# If NSE blocks the request or data is unavailable,
# the engine DOES NOT invent option premiums.
# ============================================================

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/153.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,text/plain,*/*"
    ),
    "Accept-Language": (
        "en-US,en;q=0.9"
    ),
    "Referer": (
        "https://www.nseindia.com/"
    ),
}


def get_nse_option_chain(symbol):

    # SENSEX is not on NSE.
    if symbol == "SENSEX":
        return None

    nse_symbol = {
        "NIFTY 50": "NIFTY",
        "BANKNIFTY": "BANKNIFTY",
    }.get(symbol)

    if not nse_symbol:
        return None

    now = time.time()

    cached = OPTION_CACHE.get(symbol)

    if cached:

        chain, timestamp = cached

        if now - timestamp < 30:

            return chain

    session = requests.Session()

    try:

        session.headers.update(
            NSE_HEADERS
        )

        # Warm-up NSE session.
        session.get(
            "https://www.nseindia.com/",
            timeout=8
        )

        url = (
            "https://www.nseindia.com/"
            "api/option-chain-indices"
        )

        response = session.get(
            url,
            params={
                "symbol": nse_symbol
            },
            timeout=10
        )

        if response.status_code != 200:

            print(
                f"{symbol}: NSE option chain "
                f"HTTP {response.status_code}"
            )

            return None

        payload = response.json()

        records = payload.get(
            "records",
            {}
        )

        data = records.get(
            "data",
            []
        )

        if not data:

            return None

        OPTION_CACHE[symbol] = (
            payload,
            now
        )

        return payload

    except Exception as exc:

        print(
            f"{symbol}: option-chain error:",
            exc
        )

        return None


# ============================================================
# OPTION CHAIN ANALYSIS
# ============================================================

def analyze_option_chain(
    symbol,
    spot
):

    payload = get_nse_option_chain(
        symbol
    )

    if not payload:

        return {
            "available": False,
            "pcr": None,
            "call_oi": None,
            "put_oi": None,
            "call_change_oi": None,
            "put_change_oi": None,
        }

    try:

        records = payload[
            "records"
        ]

        rows = records[
            "data"
        ]

        expiry_list = records.get(
            "expiryDates",
            []
        )

        if not expiry_list:

            return {
                "available": False
            }

        nearest_expiry = expiry_list[0]

        filtered = [
            row
            for row in rows
            if row.get("expiryDate")
            == nearest_expiry
        ]

        # Keep a reasonable zone around spot.
        filtered = [
            row
            for row in filtered
            if abs(
                float(
                    row["strikePrice"]
                )
                - spot
            )
            <= max(
                1000,
                spot * 0.05
            )
        ]

        call_oi = 0.0

        put_oi = 0.0

        call_change = 0.0

        put_change = 0.0

        for row in filtered:

            ce = row.get(
                "CE"
            )

            pe = row.get(
                "PE"
            )

            if ce:

                call_oi += float(
                    ce.get(
                        "openInterest",
                        0
                    )
                )

                call_change += float(
                    ce.get(
                        "changeinOpenInterest",
                        0
                    )
                )

            if pe:

                put_oi += float(
                    pe.get(
                        "openInterest",
                        0
                    )
                )

                put_change += float(
                    pe.get(
                        "changeinOpenInterest",
                        0
                    )
                )

        pcr = (
            put_oi / call_oi
            if call_oi > 0
            else None
        )

        return {
            "available": True,
            "expiry": nearest_expiry,
            "pcr": pcr,
            "call_oi": call_oi,
            "put_oi": put_oi,
            "call_change_oi": call_change,
            "put_change_oi": put_change,
        }

    except Exception as exc:

        print(
            f"{symbol}: option-chain parse error:",
            exc
        )

        return {
            "available": False
        }


# ============================================================
# STRATEGY SELECTOR
# ============================================================

def choose_strategy(
    analysis,
    option_data
):

    regime = analysis["regime"]

    confidence = analysis["confidence"]

    vix = analysis["vix"]

    # --------------------------------------------------------
    # HARD SAFETY FILTERS
    # --------------------------------------------------------

    if confidence < MIN_CONFIDENCE:

        return (
            "NO_TRADE",
            "Confidence below threshold"
        )

    if vix is not None:

        if vix > MAX_VIX_FOR_NEW_TRADE:

            return (
                "NO_TRADE",
                "India VIX too high"
            )

        if vix < MIN_VIX_FOR_CREDIT_SELLING:

            return (
                "NO_TRADE",
                "Premium environment too weak"
            )

    # --------------------------------------------------------
    # OPTION CHAIN SENTIMENT
    # --------------------------------------------------------

    pcr = option_data.get(
        "pcr"
    )

    # --------------------------------------------------------
    # STRONG BULLISH
    # --------------------------------------------------------

    if regime == "STRONG_BULLISH":

        if (
            pcr is not None
            and pcr < 0.65
        ):

            return (
                "NO_TRADE",
                "Price bullish but option sentiment weak"
            )

        return (
            "BULL_PUT_SPREAD",
            "Bullish trend supports put credit spread"
        )

    # --------------------------------------------------------
    # MILD BULLISH
    # --------------------------------------------------------

    if regime == "MILD_BULLISH":

        return (
            "BULL_PUT_SPREAD",
            "Moderate bullish bias"
        )

    # --------------------------------------------------------
    # STRONG BEARISH
    # --------------------------------------------------------

    if regime == "STRONG_BEARISH":

        if (
            pcr is not None
            and pcr > 1.50
        ):

            return (
                "NO_TRADE",
                "Bearish price action but put-heavy sentiment"
            )

        return (
            "BEAR_CALL_SPREAD",
            "Bearish trend supports call credit spread"
        )

    # --------------------------------------------------------
    # MILD BEARISH
    # --------------------------------------------------------

    if regime == "MILD_BEARISH":

        return (
            "BEAR_CALL_SPREAD",
            "Moderate bearish bias"
        )

    # --------------------------------------------------------
    # RANGE
    # --------------------------------------------------------

    if regime == "NEUTRAL_RANGE":

        if (
            vix is not None
            and vix >= 13
        ):

            return (
                "IRON_CONDOR",
                "Range regime with usable volatility"
            )

        return (
            "NO_TRADE",
            "Range but insufficient premium"
        )

    return (
        "NO_TRADE",
        "No valid strategy"
    )


# ============================================================
# PAPER OPTION PREMIUM
#
# IMPORTANT:
# This is ONLY a fallback simulation model.
# It is NOT claimed to be live market premium.
# The engine marks the source as MODELLED.
# ============================================================

def model_option_premium(
    spot,
    strike,
    option_type,
    vix,
    days_to_expiry=3
):

    if spot <= 0:
        return None

    if vix is None:
        vix = 15.0

    t = max(
        days_to_expiry / 365,
        1 / 365
    )

    sigma = max(
        vix / 100,
        0.08
    )

    r = 0.06

    try:

        d1 = (
            math.log(
                spot / strike
            )
            + (
                r
                + sigma ** 2 / 2
            ) * t
        ) / (
            sigma
            * math.sqrt(t)
        )

        d2 = (
            d1
            - sigma
            * math.sqrt(t)
        )

        # Normal CDF without scipy.
        def norm_cdf(x):

            return (
                0.5
                * (
                    1
                    + math.erf(
                        x / math.sqrt(2)
                    )
                )
            )

        if option_type == "CE":

            premium = (
                spot
                * norm_cdf(d1)
                - strike
                * math.exp(-r * t)
                * norm_cdf(d2)
            )

        else:

            premium = (
                strike
                * math.exp(-r * t)
                * norm_cdf(-d2)
                - spot
                * norm_cdf(-d1)
            )

        return round(
            max(
                premium,
                0.05
            ),
            2
        )

    except Exception:

        return None


# ============================================================
# BUILD HEDGED STRATEGY
# ============================================================

def build_strategy(
    symbol,
    analysis,
    strategy
):

    spot = analysis["spot"]

    vix = analysis["vix"]

    config = INDEX_CONFIG[symbol]

    step = config["step"]

    hedge = config["hedge"]

    lot = config["lot_size"]

    atm = (
        round(
            spot / step
        )
        * step
    )

    legs = []

    # --------------------------------------------------------
    # BULL PUT CREDIT SPREAD
    # --------------------------------------------------------

    if strategy == "BULL_PUT_SPREAD":

        sell_strike = (
            atm
            - step
        )

        buy_strike = (
            sell_strike
            - hedge
        )

        sell_premium = model_option_premium(
            spot,
            sell_strike,
            "PE",
            vix
        )

        buy_premium = model_option_premium(
            spot,
            buy_strike,
            "PE",
            vix
        )

        if (
            sell_premium is None
            or buy_premium is None
        ):

            return None

        legs = [

            {
                "action": "SELL",
                "type": "PE",
                "strike": sell_strike,
                "premium": sell_premium
            },

            {
                "action": "BUY",
                "type": "PE",
                "strike": buy_strike,
                "premium": buy_premium
            }
        ]

        name = (
            "Bull Put Credit Spread"
        )

    # --------------------------------------------------------
    # BEAR CALL CREDIT SPREAD
    # --------------------------------------------------------

    elif strategy == "BEAR_CALL_SPREAD":

        sell_strike = (
            atm
            + step
        )

        buy_strike = (
            sell_strike
            + hedge
        )

        sell_premium = model_option_premium(
            spot,
            sell_strike,
            "CE",
            vix
        )

        buy_premium = model_option_premium(
            spot,
            buy_strike,
            "CE",
            vix
        )

        if (
            sell_premium is None
            or buy_premium is None
        ):

            return None

        legs = [

            {
                "action": "SELL",
                "type": "CE",
                "strike": sell_strike,
                "premium": sell_premium
            },

            {
                "action": "BUY",
                "type": "CE",
                "strike": buy_strike,
                "premium": buy_premium
            }
        ]

        name = (
            "Bear Call Credit Spread"
        )

    # --------------------------------------------------------
    # IRON CONDOR
    # --------------------------------------------------------

    elif strategy == "IRON_CONDOR":

        sell_ce = (
            atm
            + step
        )

        buy_ce = (
            sell_ce
            + hedge
        )

        sell_pe = (
            atm
            - step
        )

        buy_pe = (
            sell_pe
            - hedge
        )

        sell_ce_premium = model_option_premium(
            spot,
            sell_ce,
            "CE",
            vix
        )

        buy_ce_premium = model_option_premium(
            spot,
            buy_ce,
            "CE",
            vix
        )

        sell_pe_premium = model_option_premium(
            spot,
            sell_pe,
            "PE",
            vix
        )

        buy_pe_premium = model_option_premium(
            spot,
            buy_pe,
            "PE",
            vix
        )

        if any(
            x is None
            for x in [
                sell_ce_premium,
                buy_ce_premium,
                sell_pe_premium,
                buy_pe_premium
            ]
        ):

            return None

        legs = [

            {
                "action": "SELL",
                "type": "CE",
                "strike": sell_ce,
                "premium": sell_ce_premium
            },

            {
                "action": "BUY",
                "type": "CE",
                "strike": buy_ce,
                "premium": buy_ce_premium
            },

            {
                "action": "SELL",
                "type": "PE",
                "strike": sell_pe,
                "premium": sell_pe_premium
            },

            {
                "action": "BUY",
                "type": "PE",
                "strike": buy_pe,
                "premium": buy_pe_premium
            }
        ]

        name = (
            "Hedged Iron Condor"
        )

    else:

        return None

    # --------------------------------------------------------
    # CREDIT / MAX RISK
    # --------------------------------------------------------

    credit = 0.0

    for leg in legs:

        if leg["action"] == "SELL":

            credit += leg["premium"]

        else:

            credit -= leg["premium"]

    if strategy == "IRON_CONDOR":

        wing_width = hedge

        max_loss_points = (
            wing_width
            - credit
        )

    else:

        wing_width = hedge

        max_loss_points = (
            wing_width
            - credit
        )

    max_loss = (
        max_loss_points
        * lot
    )

    max_credit = (
        credit
        * lot
    )

    return {

        "strategy": strategy,

        "name": name,

        "spot": spot,

        "atm": atm,

        "lot_size": lot,

        "legs": legs,

        "credit_points": round(
            credit,
            2
        ),

        "max_profit": round(
            max_credit,
            2
        ),

        "max_loss": round(
            max_loss,
            2
        ),

        "risk_reward": (
            round(
                max_credit / max_loss,
                2
            )
            if max_loss > 0
            else 0
        ),

        "premium_source": "MODELLED"
    }


# ============================================================
# P&L
# ============================================================

def calculate_strategy_pnl(
    trade,
    current_spot,
    vix
):

    total = 0.0

    lot = trade["lot_size"]

    for leg in trade["legs"]:

        current_premium = model_option_premium(
            current_spot,
            leg["strike"],
            leg["type"],
            vix
        )

        if current_premium is None:
            continue

        entry = leg["premium"]

        if leg["action"] == "SELL":

            pnl = (
                entry
                - current_premium
            ) * lot

        else:

            pnl = (
                current_premium
                - entry
            ) * lot

        total += pnl

    return round(
        total,
        2
    )


# ============================================================
# BOT
# ============================================================

class AdvancedHedgingBot:

    def __init__(self):

        self.virtual_capital = (
            STARTING_CAPITAL
        )

        self.starting_capital = (
            STARTING_CAPITAL
        )

        self.daily_realized_pnl = 0.0

        self.consecutive_losses = 0

        # Profit-lock state for the current trading day.
        self.profit_lock_active = False
        self.profit_lock_level = PROFIT_LOCK_AMOUNT
        self.profit_lock_triggered_at = 0.0
        self.profit_peak = 0.0
        self.profit_lock_alert_sent = False

        self.last_update_id = 0

        self.last_trade_time = {}

        self.last_status_time = time.time()

        self.squared_off_today = False

        self.trade_date = (
            datetime.now(IST).date()
        )

        self.positions = {}

        for symbol in INDEX_CONFIG:

            self.positions[symbol] = {
                "active": False,
                "trade": None
            }


    # ========================================================
    # MARKET OPEN
    # ========================================================

    def is_market_open(self):

        now = datetime.now(IST)

        if now.weekday() >= 5:

            return False

        current = now.time()

        return (
            MARKET_START
            <= current
            <= MARKET_END
        )


    # ========================================================
    # DAILY RESET
    # ========================================================

    def reset_new_day(self):

        today = (
            datetime.now(IST).date()
        )

        if today == self.trade_date:

            return

        with STATE_LOCK:

            self.trade_date = today

            self.daily_realized_pnl = 0.0

            self.consecutive_losses = 0

            self.profit_lock_active = False
            self.profit_lock_level = PROFIT_LOCK_AMOUNT
            self.profit_lock_triggered_at = 0.0
            self.profit_peak = 0.0
            self.profit_lock_alert_sent = False

            self.squared_off_today = False

            for symbol in self.positions:

                self.positions[symbol] = {
                    "active": False,
                    "trade": None
                }

        send_telegram(
            "🌅 *NEW TRADING DAY*\n\n"
            f"📅 `{today.strftime('%d-%m-%Y')}`\n"
            "🛡️ Hedging engine ready.\n"
            "📊 Paper trading mode."
        )


    # ========================================================
    # RISK CHECK
    # ========================================================

    def risk_allows_new_trade(
        self,
        symbol
    ):

        if self.profit_lock_active:

            return False, (
                "Profit lock active for today"
            )

        if (
            self.daily_realized_pnl
            <= -MAX_DAILY_LOSS
        ):

            return False, (
                "Daily loss limit reached"
            )

        if self.consecutive_losses >= 3:

            return False, (
                "Three consecutive losses"
            )

        last = self.last_trade_time.get(
            symbol
        )

        if last:

            if (
                time.time()
                - last
                < NO_TRADE_COOLDOWN
            ):

                return False, (
                    "Cooldown active"
                )

        return True, "OK"


    # ========================================================
    # EXECUTE PAPER STRATEGY
    # ========================================================

    def evaluate_symbol(
        self,
        symbol
    ):

        if self.positions[symbol]["active"]:

            return

        allowed, reason = (
            self.risk_allows_new_trade(
                symbol
            )
        )

        if not allowed:

            print(
                f"{symbol}: NO TRADE - {reason}"
            )

            return

        df = get_market_data(
            symbol
        )

        if df is None:

            print(
                f"{symbol}: no market data"
            )

            return

        analysis = analyze_market(
            symbol,
            df
        )

        option_data = (
            analyze_option_chain(
                symbol,
                analysis["spot"]
            )
        )

        strategy, strategy_reason = (
            choose_strategy(
                analysis,
                option_data
            )
        )

        # ----------------------------------------------------
        # NO TRADE
        # ----------------------------------------------------

        if strategy == "NO_TRADE":

            print(
                f"{symbol}: NO TRADE - "
                f"{strategy_reason}"
            )

            return

        trade = build_strategy(
            symbol,
            analysis,
            strategy
        )

        if trade is None:

            print(
                f"{symbol}: strategy build failed"
            )

            return

        # ----------------------------------------------------
        # SAFETY: CREDIT MUST BE POSITIVE
        # ----------------------------------------------------

        if trade["credit_points"] <= 0:

            print(
                f"{symbol}: NO TRADE - "
                "negative credit"
            )

            return

        # ----------------------------------------------------
        # SAFETY: MAX LOSS
        # ----------------------------------------------------

        if (
            trade["max_loss"]
            > self.virtual_capital
            * 0.10
        ):

            print(
                f"{symbol}: NO TRADE - "
                "risk too large"
            )

            return

        # ----------------------------------------------------
        # SAVE
        # ----------------------------------------------------

        trade["entry_time"] = (
            datetime.now(IST)
        )

        trade["regime"] = (
            analysis["regime"]
        )

        trade["confidence"] = (
            analysis["confidence"]
        )

        trade["vix"] = (
            analysis["vix"]
        )

        trade["rsi"] = (
            analysis["rsi"]
        )

        trade["adx"] = (
            analysis["adx"]
        )

        trade["pcr"] = (
            option_data.get("pcr")
        )

        with STATE_LOCK:

            self.positions[symbol] = {
                "active": True,
                "trade": trade
            }

            self.last_trade_time[symbol] = (
                time.time()
            )

        # ----------------------------------------------------
        # TELEGRAM MESSAGE
        # ----------------------------------------------------

        legs_text = ""

        for leg in trade["legs"]:

            legs_text += (
                f"• {leg['action']} "
                f"`{leg['strike']} "
                f"{leg['type']}` "
                f"@ ₹{leg['premium']:.2f}\n"
            )

        pcr_text = (
            f"{trade['pcr']:.2f}"
            if trade["pcr"] is not None
            else "N/A"
        )

        vix_text = (
            f"{trade['vix']:.2f}"
            if trade["vix"] is not None
            else "N/A"
        )

        message = (
            "🛡️ *[HEDGED OPTION-SELLING SIGNAL]*\n\n"
            f"🎯 *Index:* `{symbol}`\n"
            f"📈 *Spot:* `₹{trade['spot']:.2f}`\n"
            f"📊 *Regime:* `{trade['regime']}`\n"
            f"🧠 *Confidence:* `{trade['confidence']}%`\n"
            f"🌡️ *India VIX:* `{vix_text}`\n"
            f"📉 *RSI:* `{trade['rsi']:.1f}`\n"
            f"💪 *ADX:* `{trade['adx']:.1f}`\n"
            f"📊 *PCR:* `{pcr_text}`\n\n"
            f"🛡️ *Strategy:* "
            f"*{trade['name']}*\n\n"
            f"📋 *Hedged Legs:*\n"
            f"{legs_text}\n"
            f"💰 *Credit:* "
            f"`₹{trade['max_profit']:,.2f}`\n"
            f"⚠️ *Max Model Risk:* "
            f"`₹{trade['max_loss']:,.2f}`\n"
            f"📐 *R:R:* "
            f"`{trade['risk_reward']}`\n\n"
            "⚠️ *Mode:* PAPER TRADING\n"
            "⚠️ Premium source: MODELLED\n"
            f"⏱️ `{datetime.now(IST).strftime('%I:%M:%S %p')}`"
        )

        send_telegram(
            message
        )


    # ========================================================
    # PROFIT LOCK / TRAILING PROFIT PROTECTION
    # ========================================================

    def get_running_pnl(self):

        total = 0.0

        for symbol in INDEX_CONFIG:

            position = self.positions[symbol]

            if not position["active"]:
                continue

            trade = position["trade"]

            df = get_market_data(symbol)

            if df is not None:
                spot = float(df["Close"].iloc[-1])
            else:
                spot = trade["spot"]

            total += calculate_strategy_pnl(
                trade,
                spot,
                get_india_vix()
            )

        return round(total, 2)


    def check_profit_lock(self):

        # Already locked/closed for the day.
        if self.profit_lock_active:
            return True

        running_pnl = self.get_running_pnl()

        # No active position = nothing to lock.
        if running_pnl == 0.0:
            return False

        # Activate the lock when total open profit reaches ₹5,000.
        if running_pnl >= PROFIT_LOCK_TRIGGER:

            self.profit_lock_active = True
            self.profit_lock_level = PROFIT_LOCK_AMOUNT
            self.profit_lock_triggered_at = running_pnl
            self.profit_peak = running_pnl
            self.profit_lock_exit_sent = False

            if not self.profit_lock_alert_sent:

                send_telegram(
                    "🔒 *PROFIT LOCK ACTIVATED*\n\n"
                    f"💰 Running Profit: `₹{running_pnl:+,.2f}`\n"
                    f"🛡️ Locked Minimum: `₹{PROFIT_LOCK_AMOUNT:,.2f}`\n"
                    "🚫 New trades blocked for today.\n"
                    "📉 If profit falls to the lock level, "
                    "all open hedged positions will be closed."
                )

                self.profit_lock_alert_sent = True

            return False

        return False


    def enforce_profit_lock(self):

        if not self.profit_lock_active:
            return False

        # After the profit-lock exit has fired, do not repeat it.
        if self.profit_lock_exit_sent:
            return False

        # If all positions are already closed, there is nothing to close.
        active_positions = any(
            self.positions[symbol]["active"]
            for symbol in INDEX_CONFIG
        )

        if not active_positions:
            return False

        running_pnl = self.get_running_pnl()

        # TRAILING PROFIT LOCK: floor only moves upward.
        if running_pnl > self.profit_peak:

            self.profit_peak = running_pnl

            additional_profit = max(
                0.0,
                self.profit_peak - PROFIT_LOCK_TRIGGER
            )

            new_floor = (
                PROFIT_LOCK_AMOUNT
                + additional_profit * TRAILING_LOCK_SHARE
            )

            if new_floor > self.profit_lock_level:

                old_floor = self.profit_lock_level
                self.profit_lock_level = round(new_floor, 2)

                send_telegram(
                    "📈 *TRAILING PROFIT LOCK MOVED UP*\n\n"
                    f"🏆 Peak Running Profit: `₹{self.profit_peak:,.2f}`\n"
                    f"🔒 Old Floor: `₹{old_floor:,.2f}`\n"
                    f"🛡️ New Protected Floor: `₹{self.profit_lock_level:,.2f}`"
                )

        # Close all open hedged positions exactly ONCE.
        if running_pnl <= self.profit_lock_level:

            # Set BEFORE sending/closing so the next loop cannot repeat.
            self.profit_lock_exit_sent = True

            send_telegram(
                "🛑 *TRAILING PROFIT LOCK HIT*\n\n"
                f"📉 Current Running P&L: `₹{running_pnl:+,.2f}`\n"
                f"🏆 Peak Profit: `₹{self.profit_peak:,.2f}`\n"
                f"🔒 Protected Profit Floor: `₹{self.profit_lock_level:,.2f}`\n"
                "⚡ Closing all open hedged positions now."
            )

            self.close_all_positions(
                reason="PROFIT LOCK"
            )

            return True

        return False


    # ========================================================
    # STATUS
    # ========================================================

    def get_status(self):

        lines = [
            "📊 *ADVANCED HEDGING STATUS*",
            ""
        ]

        active_count = 0

        running_pnl = 0.0

        for symbol in INDEX_CONFIG:

            position = (
                self.positions[symbol]
            )

            if not position["active"]:

                lines.append(
                    f"• `{symbol}`: NO POSITION"
                )

                continue

            active_count += 1

            trade = position["trade"]

            df = get_market_data(
                symbol
            )

            if df is not None:

                current_spot = float(
                    df["Close"].iloc[-1]
                )

            else:

                current_spot = (
                    trade["spot"]
                )

            vix = get_india_vix()

            pnl = calculate_strategy_pnl(
                trade,
                current_spot,
                vix
            )

            running_pnl += pnl

            emoji = (
                "🟢"
                if pnl >= 0
                else "🔴"
            )

            lines.append(
                f"• `{symbol}`\n"
                f"  Spot: ₹{current_spot:,.2f}\n"
                f"  Strategy: "
                f"`{trade['name']}`\n"
                f"  Confidence: "
                f"`{trade['confidence']}%`\n"
                f"  P&L: {emoji} "
                f"*₹{pnl:+,.2f}*"
            )

            lines.append("")

        if active_count == 0:

            lines.append(
                "ℹ️ No active hedged positions."
            )

        lock_status = (
            f"🔒 ACTIVE — Floor ₹{self.profit_lock_level:,.2f}"
            if self.profit_lock_active
            else f"OFF — Trigger ₹{PROFIT_LOCK_TRIGGER:,.2f}"
        )

        lines.extend([
            "━━━━━━━━━━━━━━━━━━",
            f"💵 *Running P&L:* "
            f"`₹{running_pnl:+,.2f}`",
            f"💰 *Virtual Capital:* "
            f"`₹{self.virtual_capital + running_pnl:,.2f}`",
            f"📉 *Today's Realized P&L:* "
            f"`₹{self.daily_realized_pnl:+,.2f}`",
            f"🛡️ *Daily Loss Limit:* "
            f"`₹{MAX_DAILY_LOSS:,.2f}`",
            f"🔒 *Profit Lock:* `{lock_status}`",
            "⚠️ *Paper Trading Only*"
        ])

        return "\n".join(
            lines
        )


    # ========================================================
    # SIGNAL DETAILS
    # ========================================================

    def get_signal_report(
        self,
        symbol
    ):

        if symbol not in INDEX_CONFIG:

            return (
                "Unknown symbol.\n"
                "Use: NIFTY, BANKNIFTY or SENSEX"
            )

        df = get_market_data(
            symbol
        )

        if df is None:

            return (
                f"❌ `{symbol}` market data unavailable."
            )

        analysis = analyze_market(
            symbol,
            df
        )

        option_data = (
            analyze_option_chain(
                symbol,
                analysis["spot"]
            )
        )

        strategy, reason = (
            choose_strategy(
                analysis,
                option_data
            )
        )

        vix_text = (
            f"{analysis['vix']:.2f}"
            if analysis["vix"] is not None
            else "N/A"
        )

        pcr_text = (
            f"{option_data['pcr']:.2f}"
            if option_data.get("pcr") is not None
            else "N/A"
        )

        reasons = "\n".join(
            f"• {x}"
            for x in analysis["reasons"][:10]
        )

        return (
            "🔎 *SIGNAL ANALYSIS*\n\n"
            f"🎯 Index: `{symbol}`\n"
            f"📈 Spot: `₹{analysis['spot']:,.2f}`\n"
            f"📊 Regime: "
            f"`{analysis['regime']}`\n"
            f"🧠 Confidence: "
            f"`{analysis['confidence']}%`\n"
            f"🌡️ India VIX: `{vix_text}`\n"
            f"📉 RSI: `{analysis['rsi']:.1f}`\n"
            f"💪 ADX: `{analysis['adx']:.1f}`\n"
            f"📊 PCR: `{pcr_text}`\n\n"
            f"🛡️ *Suggested Strategy:* "
            f"`{strategy}`\n"
            f"ℹ️ Reason: {reason}\n\n"
            "*Signal Factors:*\n"
            f"{reasons}\n\n"
            "⚠️ Paper-trading analysis."
        )


    # ========================================================
    # RISK REPORT
    # ========================================================

    def get_risk_report(self):

        current_running = 0.0

        for symbol in INDEX_CONFIG:

            position = (
                self.positions[symbol]
            )

            if not position["active"]:

                continue

            trade = position["trade"]

            df = get_market_data(
                symbol
            )

            if df is None:

                continue

            spot = float(
                df["Close"].iloc[-1]
            )

            current_running += (
                calculate_strategy_pnl(
                    trade,
                    spot,
                    get_india_vix()
                )
            )

        remaining_loss = max(
            0,
            MAX_DAILY_LOSS
            + self.daily_realized_pnl
        )

        return (
            "🛡️ *RISK REPORT*\n\n"
            f"💰 Capital: "
            f"`₹{self.virtual_capital:,.2f}`\n"
            f"📊 Running P&L: "
            f"`₹{current_running:+,.2f}`\n"
            f"📉 Realized Today: "
            f"`₹{self.daily_realized_pnl:+,.2f}`\n"
            f"🚨 Daily Loss Limit: "
            f"`₹{MAX_DAILY_LOSS:,.2f}`\n"
            f"🟢 Remaining Loss Capacity: "
            f"`₹{remaining_loss:,.2f}`\n"
            f"🔴 Consecutive Losses: "
            f"`{self.consecutive_losses}`\n\n"
            "🛡️ Mandatory hedging: ON\n"
            "🚫 Naked selling: OFF\n"
            "⚠️ Paper trading: ON"
        )


    # ========================================================
    # CLOSE ALL
    # ========================================================

    def close_all_positions(self, reason="INTRADAY SQUARE-OFF"):

        total_day_pnl = 0.0

        report = [
            f"🛑 *{reason}*",
            ""
        ]

        closed_any = False

        for symbol in INDEX_CONFIG:

            position = (
                self.positions[symbol]
            )

            if not position["active"]:

                continue

            closed_any = True

            trade = position["trade"]

            df = get_market_data(
                symbol
            )

            if df is not None:

                spot = float(
                    df["Close"].iloc[-1]
                )

            else:

                spot = trade["spot"]

            pnl = calculate_strategy_pnl(
                trade,
                spot,
                get_india_vix()
            )

            total_day_pnl += pnl

            emoji = (
                "🟢"
                if pnl >= 0
                else "🔴"
            )

            report.append(
                f"• `{symbol}` "
                f"{emoji} "
                f"`₹{pnl:+,.2f}`"
            )

            if pnl < 0:

                self.consecutive_losses += 1

            else:

                self.consecutive_losses = 0

            with STATE_LOCK:

                self.positions[symbol] = {
                    "active": False,
                    "trade": None
                }

        if not closed_any:

            return

        self.virtual_capital += (
            total_day_pnl
        )

        self.daily_realized_pnl += (
            total_day_pnl
        )

        # Profit-lock closure means trading is finished for the day.
        if reason == "PROFIT LOCK":
            self.profit_lock_active = True

        roi = (
            (
                self.virtual_capital
                - self.starting_capital
            )
            / self.starting_capital
            * 100
        )

        status = (
            "🟢 PROFIT"
            if total_day_pnl >= 0
            else "🔴 LOSS"
        )

        report.extend([
            "",
            "━━━━━━━━━━━━━━━━━━",
            f"🏁 Day P&L: "
            f"{status} "
            f"`₹{total_day_pnl:+,.2f}`",
            f"💰 Capital: "
            f"`₹{self.virtual_capital:,.2f}`",
            f"📈 Overall ROI: "
            f"`{roi:+.2f}%`",
            "⚠️ Paper trading."
        ])

        send_telegram(
            "\n".join(report)
        )


    # ========================================================
    # TELEGRAM COMMAND LISTENER
    # ========================================================

    def telegram_listener(self):

        print(
            "Telegram listener started."
        )

        while True:

            if not TELEGRAM_BOT_TOKEN:

                time.sleep(5)

                continue

            try:

                url = (
                    "https://api.telegram.org/"
                    f"bot{TELEGRAM_BOT_TOKEN}/getUpdates"
                )

                response = requests.get(
                    url,
                    params={
                        "offset":
                            self.last_update_id + 1,
                        "timeout": 2
                    },
                    timeout=5
                )

                data = response.json()

                for update in data.get(
                    "result",
                    []
                ):

                    self.last_update_id = (
                        update["update_id"]
                    )

                    message = update.get(
                        "message"
                    )

                    if not message:

                        continue

                    text = (
                        message.get(
                            "text",
                            ""
                        )
                        .strip()
                        .lower()
                    )

                    chat_id = str(
                        message["chat"]["id"]
                    )

                    if (
                        TELEGRAM_CHAT_ID
                        and chat_id
                        != str(
                            TELEGRAM_CHAT_ID
                        )
                    ):

                        continue

                    # ----------------------------------------
                    # STATUS
                    # ----------------------------------------

                    if text in [
                        "/status",
                        "status"
                    ]:

                        send_telegram(
                            self.get_status()
                        )

                    # ----------------------------------------
                    # RISK
                    # ----------------------------------------

                    elif text in [
                        "/risk",
                        "risk"
                    ]:

                        send_telegram(
                            self.get_risk_report()
                        )

                    # ----------------------------------------
                    # SIGNAL
                    # ----------------------------------------

                    elif text.startswith(
                        "/signal"
                    ):

                        parts = text.split()

                        if len(parts) >= 2:

                            requested = parts[1]

                            symbol_map = {
                                "nifty":
                                    "NIFTY 50",

                                "banknifty":
                                    "BANKNIFTY",

                                "sensex":
                                    "SENSEX"
                            }

                            symbol = (
                                symbol_map.get(
                                    requested
                                )
                            )

                            if symbol:

                                send_telegram(
                                    self.get_signal_report(
                                        symbol
                                    )
                                )

                            else:

                                send_telegram(
                                    "Use:\n"
                                    "`/signal nifty`\n"
                                    "`/signal banknifty`\n"
                                    "`/signal sensex`"
                                )

                        else:

                            send_telegram(
                                "Use:\n"
                                "`/signal nifty`\n"
                                "`/signal banknifty`\n"
                                "`/signal sensex`"
                            )

                    # ----------------------------------------
                    # START
                    # ----------------------------------------

                    elif text in [
                        "/start",
                        "start",
                        "/help",
                        "help"
                    ]:

                        send_telegram(
                            "🤖 *ADVANCED HEDGING ENGINE*\n\n"
                            "Commands:\n"
                            "• `/status`\n"
                            "• `/risk`\n"
                            "• `/signal nifty`\n"
                            "• `/signal banknifty`\n"
                            "• `/signal sensex`\n\n"
                            "🛡️ Mandatory hedging ON\n"
                            "🚫 Naked selling OFF\n"
                            "📊 Paper trading ON"
                        )

            except Exception as exc:

                print(
                    "Telegram listener error:",
                    exc
                )

            time.sleep(1)


    # ========================================================
    # MAIN LOOP
    # ========================================================

    def run(self):

        time.sleep(3)

        send_telegram(
            "🚀 *ADVANCED OPTIONS SELLING ENGINE LIVE*\n\n"
            "🛡️ Mandatory Hedging: ON\n"
            "🚫 Naked Selling: OFF\n"
            "🧠 Multi-Factor Analysis: ON\n"
            "🌡️ India VIX Filter: ON\n"
            "📊 Option-Chain Sentiment: ON when available\n"
            "🧯 Risk Protection: ON\n"
            f"🔒 Profit Lock: ₹{PROFIT_LOCK_TRIGGER:,.0f} → ₹{PROFIT_LOCK_AMOUNT:,.0f}\n"
            "📡 Telegram Listener: ON\n"
            "📝 Paper Trading: ON\n\n"
            "Use `/status` anytime."
        )

        # ----------------------------------------------------
        # TELEGRAM THREAD
        # ----------------------------------------------------

        listener = threading.Thread(
            target=self.telegram_listener,
            daemon=True
        )

        listener.start()

        while True:

            try:

                self.reset_new_day()

                now = datetime.now(IST)

                # ------------------------------------------------
                # MARKET
                # ------------------------------------------------

                if self.is_market_open():

                    # --------------------------------------------
                    # PROFIT LOCK CHECK
                    # --------------------------------------------

                    lock_hit = self.enforce_profit_lock()

                    if not self.profit_lock_active:
                        self.check_profit_lock()

                    # If the lock just activated, do not open
                    # another position in the same loop.
                    if self.profit_lock_active and not lock_hit:
                        print(
                            "Profit lock active - new trades blocked."
                        )

                    # --------------------------------------------
                    # DAILY LOSS STOP
                    # --------------------------------------------

                    if (
                        self.daily_realized_pnl
                        <= -MAX_DAILY_LOSS
                    ):

                        print(
                            "Daily loss limit reached."
                        )

                    elif not self.profit_lock_active:

                        # ----------------------------------------
                        # SYMBOL SCAN
                        # ----------------------------------------

                        for symbol in INDEX_CONFIG:

                            try:

                                self.evaluate_symbol(
                                    symbol
                                )

                            except Exception as exc:

                                print(
                                    f"{symbol} evaluation error:",
                                    exc
                                )

                            time.sleep(2)

                    # --------------------------------------------
                    # STATUS
                    # --------------------------------------------

                    if (
                        time.time()
                        - self.last_status_time
                        >= STATUS_INTERVAL
                    ):

                        send_telegram(
                            self.get_status()
                        )

                        self.last_status_time = (
                            time.time()
                        )

                    # --------------------------------------------
                    # 03:15 SQUARE-OFF
                    # --------------------------------------------

                    if (
                        now.hour == 15
                        and now.minute == 15
                        and not self.squared_off_today
                    ):

                        self.close_all_positions()

                        self.squared_off_today = True

                # ------------------------------------------------
                # AFTER MARKET
                # ------------------------------------------------

                if now.hour >= 16:

                    self.squared_off_today = False

                time.sleep(5)

            except Exception as exc:

                print(
                    "MAIN LOOP ERROR:",
                    exc
                )

                time.sleep(5)


# ============================================================
# START APPLICATION
# ============================================================

if __name__ == "__main__":
    bot = AdvancedHedgingBot()

    server_thread = threading.Thread(
        target=run_web_server,
        daemon=True
    )

    server_thread.start()

    bot.run()
