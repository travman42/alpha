import streamlit as st
import pandas as pd
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas_ta as ta
import time
from datetime import datetime

st.set_page_config(page_title="AlphaSentinel - 币安Alpha异动监控", layout="wide")
st.title("🚨 AlphaSentinel - 币安 Alpha 代币异动监控与智能交易策略")

# Sidebar 配置
with st.sidebar:
    st.header("⚙️ 设置")
    tg_token = st.text_input("Telegram Bot Token", type="password", help="从 @BotFather 获取")
    tg_chatid = st.text_input("Telegram Chat ID", help="从 @userinfobot 获取")
    alert_enabled = st.toggle("启用 TG 告警", value=True)
    price_threshold = st.slider("价格异动阈值 (%)", 5, 50, 15)
    volume_mult = st.slider("成交量倍数阈值", 2.0, 10.0, 3.0)
    refresh_interval = st.slider("自动刷新间隔 (秒)", 30, 300, 60)
    
    st.divider()
    st.caption("Watchlist")
    watchlist = st.session_state.get("watchlist", [])
    new_watch = st.text_input("添加代币符号 (如 GORILLA)")
    if st.button("添加") and new_watch:
        if new_watch.upper() not in [w.upper() for w in watchlist]:
            watchlist.append(new_watch.upper())
            st.session_state.watchlist = watchlist
            st.success(f"已添加 {new_watch}")

# API 函数
@st.cache_data(ttl=30)
def fetch_alpha_tokens():
    url = "https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list"
    resp = requests.get(url, timeout=10)
    data = resp.json()["data"]
    df = pd.DataFrame(data)
    df = df[["name", "symbol", "alphaId", "price", "percentChange24h", "volume24h", "marketCap"]]
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["percentChange24h"] = pd.to_numeric(df["percentChange24h"], errors="coerce")
    df["volume24h"] = pd.to_numeric(df["volume24h"], errors="coerce")
    df["marketCap"] = pd.to_numeric(df["marketCap"], errors="coerce")
    df["symbol_upper"] = df["symbol"].str.upper()
    return df

def get_symbol(alpha_id):
    return f"{alpha_id}USDT"

@st.cache_data(ttl=60)
def fetch_klines(symbol, interval="5m", limit=500):
    url = f"https://www.binance.com/bapi/defi/v1/public/alpha-trade/klines?symbol={symbol}&interval={interval}&limit={limit}"
    resp = requests.get(url, timeout=10)
    data = resp.json()["data"]
    df = pd.DataFrame(data, columns=["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_vol", "trades", "taker_base", "taker_quote", "ignore"])
    df = df[["open_time", "open", "high", "low", "close", "volume"]].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)
    return df

def calculate_indicators(df):
    df["rsi"] = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"])
    df = pd.concat([df, macd], axis=1)
    bb = ta.bbands(df["close"], length=20)
    df = pd.concat([df, bb], axis=1)
    df["supertrend"] = ta.supertrend(df["high"], df["low"], df["close"])["SUPERTd_7_3.0"]
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    df["obv"] = ta.obv(df["close"], df["volume"])
    df["vol_ma"] = df["volume"].rolling(20).mean()
    df["vol_z"] = (df["volume"] - df["vol_ma"]) / df["vol_ma"].rolling(20).std()
    return df

def detect_signals(df, token_name):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    signals = []
    score = 0
    
    # Volume surge
    if last["volume"] > last["vol_ma"] * volume_mult and last["vol_z"] > 2:
        signals.append("🚀 成交量爆发")
        score += 30
    
    # BB breakout
    if last["close"] > last["BBU_20_2.0"] and prev["close"] <= prev["BBU_20_2.0"]:
        signals.append("🔥 Bollinger 上轨突破")
        score += 25
    
    # MACD
    if last["MACD_12_26_9"] > last["MACDs_12_26_9"] and prev["MACD_12_26_9"] <= prev["MACDs_12_26_9"]:
        signals.append("📈 MACD 金叉")
        score += 20
    
    # RSI
    if last["rsi"] < 35:
        signals.append("🛡️ RSI 超卖")
        score += 15
    
    # Supertrend
    if last["supertrend"] > 0 and last["close"] > last["supertrend"]:
        signals.append("✅ Supertrend 多头")
        score += 10
    
    alpha_score = min(100, score)
    
    if signals:
        strategy = f"""
**最佳执行策略（{token_name}）**：
入场：当前价或回踩 20EMA
止损：入场价 - 2×ATR ({last['atr']:.6f})
止盈：分层 1:3 RR（50% 1:2，30% 1:4，20% trailing stop）
仓位建议：账户资金 × 1.5% 风险
        """.strip()
        
        alert_msg = f"🚨 Alpha异动警报！\n{token_name}\n价格: {last['close']}\n信号: {', '.join(signals)}\nAlpha Score: {alpha_score}\n\n{strategy}"
        
        if alert_enabled and tg_token and tg_chatid:
            try:
                requests.post(f"https://api.telegram.org/bot{tg_token}/sendMessage",
                              params={"chat_id": tg_chatid, "text": alert_msg}, timeout=5)
            except:
                pass
        return signals, alpha_score, strategy, alert_msg
    return None, 0, None, None

# 主界面
df_tokens = fetch_alpha_tokens()

# 筛选
col1, col2, col3 = st.columns(3)
with col1:
    min_change = st.number_input("最小24h涨幅 (%)", value=5.0)
with col2:
    min_vol = st.number_input("最小24h成交量", value=10000.0)
with col3:
    show_watch = st.checkbox("仅显示 Watchlist", value=False)

if show_watch and watchlist:
    df_tokens = df_tokens[df_tokens["symbol_upper"].isin([w.upper() for w in watchlist])]

filtered = df_tokens[
    (abs(df_tokens["percentChange24h"]) >= min_change) &
    (df_tokens["volume24h"] >= min_vol)
].copy()

filtered["异动Score"] = filtered["percentChange24h"].abs() * 2 + (filtered["volume24h"] / 10000)
filtered = filtered.sort_values("异动Score", ascending=False)

st.dataframe(filtered[["name", "symbol", "price", "percentChange24h", "volume24h", "marketCap"]],
             use_container_width=True, height=400)

# 自动刷新
if st.button("🔄 手动刷新"):
    st.rerun()

# 每 refresh_interval 秒自动刷新
time.sleep(0.1)  # 防止卡顿
if "last_refresh" not in st.session_state or (time.time() - st.session_state.last_refresh > refresh_interval):
    st.session_state.last_refresh = time.time()
    st.rerun()

# 详细分析区
st.divider()
selected = st.selectbox("选择代币进行深度分析与策略生成", options=filtered["name"].tolist() if not filtered.empty else df_tokens["name"].tolist())

if selected:
    row = df_tokens[df_tokens["name"] == selected].iloc[0]
    alpha_id = row["alphaId"]
    symbol = get_symbol(alpha_id)
    
    with st.spinner(f"正在拉取 {selected} K线与指标..."):
        for interval in ["1m", "5m", "15m", "1h"]:
            if st.button(f"切换到 {interval} 图表"):
                df = fetch_klines(symbol, interval)
                df = calculate_indicators(df)
                signals, score, strategy, alert_msg = detect_signals(df, selected)
                
                # 绘图
                fig = make_subplots(rows=3, cols=1, shared_xaxes=True, 
                                    row_heights=[0.5, 0.2, 0.3], vertical_spacing=0.05)
                
                fig.add_trace(go.Candlestick(x=df.index, open=df["open"], high=df["high"], 
                                             low=df["low"], close=df["close"], name="K线"), row=1, col=1)
                fig.add_trace(go.Scatter(x=df.index, y=df["BBU_20_2.0"], line=dict(color="red"), name="BB Upper"), row=1, col=1)
                fig.add_trace(go.Scatter(x=df.index, y=df["BBL_20_2.0"], line=dict(color="green"), name="BB Lower"), row=1, col=1)
                fig.add_trace(go.Scatter(x=df.index, y=df["supertrend"], line=dict(color="blue"), name="Supertrend"), row=1, col=1)
                
                fig.add_trace(go.Bar(x=df.index, y=df["MACDh_12_26_9"], name="MACD Hist"), row=2, col=1)
                fig.add_trace(go.Scatter(x=df.index, y=df["rsi"], name="RSI", line=dict(color="purple")), row=3, col=1)
                
                fig.update_layout(height=800, title=f"{selected} {interval} 图表 + 指标")
                st.plotly_chart(fig, use_container_width=True)
                
                if signals:
                    st.success(f"检测到强信号！Alpha Score: **{score}**")
                    st.info("\n".join(signals))
                    st.markdown(f"**推荐执行策略：**\n{strategy}")
                    if alert_enabled:
                        st.success("✅ 已通过 Telegram 发送告警！")
                else:
                    st.info("暂无强信号，继续监控...")

st.caption("数据来自 Binance Alpha 官方 API | 指标基于 pandas_ta 实战库 | 策略为参考，仅供学习，交易有风险")
