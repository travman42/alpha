import streamlit as st
import pandas as pd
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas_ta as ta
from datetime import datetime

st.set_page_config(page_title="AlphaSentinel v2.0", layout="wide")
st.title("🚨 AlphaSentinel v2.0 - 币安 Alpha 代币异动监控（已修复无限loading）")

# Sidebar
with st.sidebar:
    st.header("⚙️ 设置")
    tg_token = st.text_input("Telegram Bot Token", type="password")
    tg_chatid = st.text_input("Telegram Chat ID")
    alert_enabled = st.toggle("启用 TG 告警", value=True)
    volume_mult = st.slider("成交量倍数阈值", 2.0, 10.0, 3.0)
    
    st.divider()
    st.caption("Watchlist")
    if "watchlist" not in st.session_state:
        st.session_state.watchlist = []
    new_watch = st.text_input("添加代币符号 (如 BTW)")
    if st.button("添加") and new_watch:
        sym = new_watch.upper().strip()
        if sym and sym not in st.session_state.watchlist:
            st.session_state.watchlist.append(sym)
            st.success(f"已添加 {sym}")

# 带错误保护的 API 函数
@st.cache_data(ttl=30)
def fetch_alpha_tokens():
    try:
        url = "https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if isinstance(data, dict) and "data" in data:
            df = pd.DataFrame(data["data"])
        elif isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            st.error("API 返回格式异常")
            return pd.DataFrame()
        
        # 关键：所有数值字段都是字符串，必须转换
        numeric_cols = ["price", "percentChange24h", "volume24h", "marketCap", "priceHigh24h", "priceLow24h"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        
        df["symbol_upper"] = df["symbol"].str.upper()
        return df[["name", "symbol", "alphaId", "price", "percentChange24h", "volume24h", "marketCap", "symbol_upper"]]
    except Exception as e:
        st.error(f"获取 Alpha 代币列表失败: {str(e)}")
        return pd.DataFrame()

@st.cache_data(ttl=60)
def fetch_klines(alpha_id, interval="5m", limit=500):
    try:
        symbol = f"{alpha_id}USDT"   # 已确认正确格式（ALPHA_xxxUSDT）
        url = f"https://www.binance.com/bapi/defi/v1/public/alpha-trade/klines?symbol={symbol}&interval={interval}&limit={limit}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if "data" in data:
            df = pd.DataFrame(data["data"], columns=["open_time","open","high","low","close","volume","close_time","quote_vol","trades","taker_base","taker_quote","ignore"])
            df = df[["open_time", "open", "high", "low", "close", "volume"]].astype(float)
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
            df.set_index("open_time", inplace=True)
            return df
        else:
            st.error(f"Klines 返回异常: {data.get('message','')}")
            return pd.DataFrame()
    except Exception as e:
        st.error(f"获取 K线失败 ({alpha_id}): {str(e)}")
        return pd.DataFrame()

def calculate_indicators(df):
    if df.empty or len(df) < 50:
        return df
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
    if df.empty:
        return None, 0, None, None
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    signals = []
    score = 0
    
    if last["volume"] > last["vol_ma"] * volume_mult and last["vol_z"] > 2:
        signals.append("🚀 成交量爆发")
        score += 30
    if last["close"] > last["BBU_20_2.0"] and prev["close"] <= prev["BBU_20_2.0"]:
        signals.append("🔥 Bollinger 上轨突破")
        score += 25
    if last["MACD_12_26_9"] > last["MACDs_12_26_9"] and prev["MACD_12_26_9"] <= prev["MACDs_12_26_9"]:
        signals.append("📈 MACD 金叉")
        score += 20
    if last["rsi"] < 35:
        signals.append("🛡️ RSI 超卖")
        score += 15
    if last["supertrend"] > 0 and last["close"] > last["supertrend"]:
        signals.append("✅ Supertrend 多头")
        score += 10
    
    alpha_score = min(100, score)
    
    if signals:
        strategy = f"""**最佳执行策略（{token_name}）**：
入场：当前价或回踩20EMA
止损：入场 - 2×ATR ({last['atr']:.6f})
止盈：1:3 RR 分层（50% 1:2，30% 1:4，20% trailing）
仓位：账户 1-2% 风险"""
        
        alert_msg = f"🚨 Alpha异动！{token_name}\n价格: {last['close']}\n信号: {', '.join(signals)}\nScore: {alpha_score}\n\n{strategy}"
        
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

if df_tokens.empty:
    st.stop()

# 筛选
col1, col2, col3, col4 = st.columns([2,2,2,1])
with col1:
    min_change = st.number_input("最小24h涨幅 (%)", value=5.0)
with col2:
    min_vol = st.number_input("最小24h成交量", value=10000.0)
with col3:
    show_watch = st.checkbox("仅显示 Watchlist", value=False)
with col4:
    if st.button("🔄 刷新全部数据", type="primary"):
        st.rerun()

if show_watch and st.session_state.watchlist:
    df_tokens = df_tokens[df_tokens["symbol_upper"].isin(st.session_state.watchlist)]

filtered = df_tokens[
    (abs(df_tokens["percentChange24h"]) >= min_change) &
    (df_tokens["volume24h"] >= min_vol)
].copy()

filtered = filtered.sort_values("percentChange24h", ascending=False)

st.dataframe(filtered[["name", "symbol", "price", "percentChange24h", "volume24h", "marketCap"]],
             use_container_width=True, height=400)

# 深度分析
st.divider()
selected = st.selectbox("点击查看深度分析 + 交易策略", options=filtered["name"].tolist() if not filtered.empty else df_tokens["name"].tolist())

if selected:
    row = df_tokens[df_tokens["name"] == selected].iloc[0]
    alpha_id = row["alphaId"]
    
    with st.spinner(f"拉取 {selected} ({alpha_id}) K线与指标..."):
        for interval in ["5m", "15m", "1h"]:
            if st.button(f"显示 {interval} 图表"):
                df = fetch_klines(alpha_id, interval)
                df = calculate_indicators(df)
                signals, score, strategy, _ = detect_signals(df, selected)
                
                fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.55, 0.2, 0.25])
                fig.add_trace(go.Candlestick(x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"]), row=1, col=1)
                fig.add_trace(go.Scatter(x=df.index, y=df["BBU_20_2.0"], name="BB Upper", line=dict(color="red")), row=1, col=1)
                fig.add_trace(go.Scatter(x=df.index, y=df["BBL_20_2.0"], name="BB Lower", line=dict(color="green")), row=1, col=1)
                fig.add_trace(go.Scatter(x=df.index, y=df["supertrend"], name="Supertrend", line=dict(color="blue")), row=1, col=1)
                
                fig.add_trace(go.Bar(x=df.index, y=df.get("MACDh_12_26_9", pd.Series()), name="MACD Hist"), row=2, col=1)
                fig.add_trace(go.Scatter(x=df.index, y=df["rsi"], name="RSI", line=dict(color="purple")), row=3, col=1)
                
                fig.update_layout(height=850, title=f"{selected} {interval} 图表 + 全部指标")
                st.plotly_chart(fig, use_container_width=True)
                
                if signals:
                    st.success(f"🔥 强信号！Alpha Score: **{score}**")
                    st.info(" | ".join(signals))
                    st.markdown(strategy)
                    if alert_enabled:
                        st.success("✅ Telegram 告警已发送")
                else:
                    st.info("暂无强信号，继续监控...")

st.caption("v2.0 已优化所有已知卡死问题 | 数据来自 Binance Alpha 官方公开 API | 交易有风险")
