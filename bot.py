import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import json, os

BOT_TOKEN   = "8473490406:AAHJyhMUX1I9cgsfW9B5uUcl7dLzylj02mA"
CHAT_ID     = "6469967858"
MEMORY_FILE = "williams_memory.json"

ACCOUNT_BALANCE = 150
RISK_PERCENT    = 1

PAIRS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "USDJPY=X",
    "XAU/USD": "GC=F",
    "GBP/JPY": "GBPJPY=X",
    "USD/CAD": "USDCAD=X",
    "BTC/USD": "BTC-USD",
}

PIP_SIZE = {
    "EUR/USD": 0.0001,
    "GBP/USD": 0.0001,
    "USD/JPY": 0.01,
    "XAU/USD": 0.1,
    "GBP/JPY": 0.01,
    "USD/CAD": 0.0001,
    "BTC/USD": 1.0,
}

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_memory(data):
    with open(MEMORY_FILE, 'w') as f:
        json.dump(data, f)

def already_sent(name, direction, entry, memory):
    last = memory.get(name, {})
    if not last:
        return False
    return last.get("direction") == direction and abs(last.get("entry", 0) - entry) < 0.001 * entry

def get_session():
    now_utc = datetime.now(timezone.utc)
    morocco_hour = (now_utc.hour + 1) % 24
    london = 8 <= morocco_hour < 11
    ny     = 13 <= morocco_hour < 17
    if london and ny:
        return "🇬🇧🇺🇸 لندن + نيويورك", True, morocco_hour
    elif london:
        return "🇬🇧 جلسة لندن", True, morocco_hour
    elif ny:
        return "🇺🇸 جلسة نيويورك", True, morocco_hour
    else:
        return "⚠️ خارج الجلسات", False, morocco_hour

def get_live_news():
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        data = r.json()
        now = datetime.now(timezone.utc)
        warnings = []
        blocked = False
        for event in data:
            impact = event.get('impact', '')
            if impact not in ['High', 'Medium']:
                continue
            try:
                event_time = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
                diff = (event_time - now).total_seconds() / 60
                if -15 <= diff <= 45:
                    emoji = "🔴" if impact == 'High' else "🟡"
                    warnings.append(f"{emoji} {event.get('country','')} - {event.get('title','')} ({event_time.strftime('%H:%M')} GMT)")
                    if impact == 'High':
                        blocked = True
            except:
                continue
        return warnings, blocked
    except:
        return [], False

def calc_williams_r(df, period=14):
    highest_high = df['High'].rolling(period).max()
    lowest_low   = df['Low'].rolling(period).min()
    return -100 * (highest_high - df['Close']) / (highest_high - lowest_low + 1e-10)

def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))

def find_swing_levels(df, lookback=20):
    highs = df['High'].iloc[-lookback-1:-1]
    lows  = df['Low'].iloc[-lookback-1:-1]
    return float(highs.max()), float(lows.min())

def calc_lot_size(account, risk_pct, sl_pips, pip_value=10):
    risk_amount = account * (risk_pct / 100)
    lot_size    = risk_amount / (sl_pips * pip_value)
    return round(lot_size, 2)

def analyze_pair(name, ticker):
    try:
        df = yf.download(ticker, period="10d", interval="15m", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty or len(df) < 210:
            return None
        df = df.copy()
        df['WR']     = calc_williams_r(df)
        df['RSI']    = calc_rsi(df['Close'])
        df['EMA200'] = df['Close'].ewm(span=200).mean()
        last   = df.iloc[-1]
        prev   = df.iloc[-2]
        close  = float(last['Close'])
        high   = float(last['High'])
        low    = float(last['Low'])
        wr     = float(last['WR'])
        wr_prev= float(prev['WR'])
        rsi    = float(last['RSI'])
        ema200 = float(last['EMA200'])
        volume = float(last['Volume'])
        avg_vol= float(df['Volume'].iloc[-21:-1].mean())
        pip    = PIP_SIZE.get(name, 0.0001)
        retest_zone  = 10 * pip
        swing_high, swing_low = find_swing_levels(df)
        buy_retest   = abs(close - swing_low) <= retest_zone
        sell_retest  = abs(close - swing_high) <= retest_zone
        wr_buy_cross  = wr_prev < -80 and wr > -80
        wr_sell_cross = wr_prev > -20 and wr < -20
        buy_conditions = {
            "Williams %R خرج من -80 (Oversold)": wr_buy_cross,
            "السعر فوق EMA 200": close > ema200,
            "Retest الـ Swing Low": buy_retest,
            "RSI مو فوق 70": rsi < 70,
            "فوليوم عالي": volume > avg_vol,
        }
        sell_conditions = {
            "Williams %R خرج من -20 (Overbought)": wr_sell_cross,
            "السعر تحت EMA 200": close < ema200,
            "Retest الـ Swing High": sell_retest,
            "RSI مو تحت 30": rsi > 30,
            "فوليوم عالي": volume > avg_vol,
        }
        buy_score  = sum(buy_conditions.values())
        sell_score = sum(sell_conditions.values())
        if buy_score == 5 and buy_score > sell_score:
            sl      = round(swing_low - 15 * pip, 5)
            sl_pips = max(1, round((close - sl) / pip))
            risk    = close - sl
            tp1     = round(close + risk, 5)
            tp2     = round(close + 2 * risk, 5)
            lot     = calc_lot_size(ACCOUNT_BALANCE, RISK_PERCENT, sl_pips)
            return ("BUY", close, sl, tp1, tp2, rsi, wr, swing_high, swing_low, ema200, "💎 مثالية 5/5", buy_score, buy_conditions, sl_pips, lot)
        elif sell_score == 5 and sell_score > buy_score:
            sl      = round(swing_high + 15 * pip, 5)
            sl_pips = max(1, round((sl - close) / pip))
            risk    = sl - close
            tp1     = round(close - risk, 5)
            tp2     = round(close - 2 * risk, 5)
            lot     = calc_lot_size(ACCOUNT_BALANCE, RISK_PERCENT, sl_pips)
            return ("SELL", close, sl, tp1, tp2, rsi, wr, swing_high, swing_low, ema200, "💎 مثالية 5/5", sell_score, sell_conditions, sl_pips, lot)
        return None, buy_score, sell_score, buy_conditions, sell_conditions
    except Exception as e:
        print(f"❌ خطأ في {name}: {e}")
        return None

def build_message(name, direction, entry, sl, tp1, tp2, rsi, wr,
                  swing_high, swing_low, ema200, strength, score,
                  conditions, sl_pips, lot, session_name, news_warnings):
    emoji     = "🟢" if direction == "BUY" else "🔴"
    trend     = "📈 صاعد" if direction == "BUY" else "📉 هابط"
    now       = datetime.now(timezone.utc)
    news_sec  = ("\n⚠️ أخبار:\n" + "\n".join(news_warnings) + "\n") if news_warnings else ""
    risk_note = "🚨 أخبار مهمة!" if news_warnings else "✅ لا أخبار الآن"
    cond_text = "\n".join([f"  {'✅' if v else '❌'} {k}" for k, v in conditions.items()])
    risk_usd  = round(ACCOUNT_BALANCE * RISK_PERCENT / 100, 2)
    reward_usd= round(risk_usd * 2, 2)
    decimals  = 2 if name in ["XAU/USD", "BTC/USD"] else 5
    fmt       = f"{{:.{decimals}f}}"
    return f"""{emoji} إشارة لاري ويليامز - {strength}

📊 الزوج: {name}
{emoji} الاتجاه: {direction}
{trend}
{news_sec}
📋 الشروط ({score}/5):
{cond_text}

━━━━━━━━━━━━━
📍 الدخول: {fmt.format(entry)}
🛑 Stop Loss: {fmt.format(sl)} ({sl_pips} pips)
🎯 TP1: {fmt.format(tp1)} (1:1)
🎯 TP2: {fmt.format(tp2)} (1:2)
━━━━━━━━━━━━━
💰 إدارة الرأسمال:
  • الرأسمال: ${ACCOUNT_BALANCE}
  • المخاطرة: {RISK_PERCENT}% = ${risk_usd}
  • الهدف: ${reward_usd}
  • حجم الصفقة: {lot} Lot
━━━━━━━━━━━━━
📉 Williams %R: {wr:.1f}
📊 RSI: {rsi:.1f}
📈 EMA200: {fmt.format(ema200)}
🔵 Swing High: {fmt.format(swing_high)}
🔴 Swing Low: {fmt.format(swing_low)}
━━━━━━━━━━━━━
📰 {risk_note}
⏰ {now.strftime('%H:%M')} GMT
🏛️ {session_name}
📐 M15 تحليل | M5 دخول"""

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r   = requests.post(url, json={"chat_id": CHAT_ID, "text": message})
    return r.json().get('ok', False)

def run_bot():
    now = datetime.now(timezone.utc)
    session_name, in_session, morocco_hour = get_session()
    news_warnings, blocked = get_live_news()
    memory = load_memory()
    print(f"\n⏰ {morocco_hour:02d}:xx (المغرب) | 🏛️ {session_name}")
    if blocked:
        msg = "🚨 أخبار عالية!\n\n" + "\n".join(news_warnings) + "\n\n🚫 لا تدخل أي صفقة!"
        send_telegram(msg)
        print("🚫 موقوف - أخبار عالية")
        return
    if not in_session:
        print("⚠️ خارج الجلسات")
        return
    signals_found = 0
    for name, ticker in PAIRS.items():
        print(f"🔍 {name}...")
        result = analyze_pair(name, ticker)
        if isinstance(result, tuple) and len(result) == 5 and result[0] is None:
            _, buy_score, sell_score, _, _ = result
            print(f"  ⏳ {max(buy_score, sell_score)}/5 شروط فقط")
            continue
        if result and isinstance(result, tuple) and len(result) == 15:
            direction, entry, sl, tp1, tp2, rsi, wr, swing_high, swing_low, ema200, strength, score, conditions, sl_pips, lot = result
            if already_sent(name, direction, entry, memory):
                print(f"  ⏭️ أُرسلت سابقاً")
                continue
            msg = build_message(name, direction, entry, sl, tp1, tp2, rsi, wr,
                               swing_high, swing_low, ema200, strength, score,
                               conditions, sl_pips, lot, session_name, news_warnings)
            if send_telegram(msg):
                print(f"  ✅ {strength} - {direction} أُرسلت!")
                memory[name] = {"direction": direction, "entry": entry, "time": now.strftime('%Y-%m-%d %H:%M')}
                save_memory(memory)
                signals_found += 1
        else:
            print(f"  ⏸️ لا إشارة")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if signals_found == 0:
        print("📭 لا توجد إشارات الآن")
    else:
        print(f"📨 {signals_found} إشارة أُرسلت!")

run_bot()
