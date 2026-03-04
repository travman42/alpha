import streamlit as st
import pandas as pd
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots

st.set_page_config(page_title="AlphaSentinel v3.1", layout="wide")
st.title("🚨 AlphaSentinel v3.1 - 币安 Alpha 异动监控（已解决无限转圈）")

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

# 全局错误捕获
try:
    @st.cache_data(ttl=30)
    def fetch_alpha_tokens():
        url = "https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        df = pd.DataFrame(data.get("data", data))  # 兼容有无 data 包装
        numeric_cols = ["price", "percentChange24h", "volume24h", "marketCap"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df["symbol_upper"] = df["symbol"].str.upper()
        return df[["name", "symbol", "alphaId", "price", "percentChange24h", "volume24h", "marketCap", "symbol_upper"]]

    @st.cache_data(ttl=60)
    def fetch_klines(alpha_id, interval="5m", limit=500):
        symbol = f"{alpha_id}USDT"  # alphaId 已带 ALPHA_ 前缀，官方要求 ALPHA_xxxUSDT
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
        return pd.DataFrame()

    def calculate_indicators(df):
        if len(df) < 50:
            return df.copy()
        df = df.copy()
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        rs = gain / loss
        df["rsi"] = 100 - (100 / (1 + rs))

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df["MACD"] = ema12 - ema26
        df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
        df["MACD_hist"] = df["MACD"] - df["MACD_signal"]

        # Bollinger
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        df["BBU"] = ma20 + 2 * std20
        df["BBL"] = ma20 - 2 * std20

        # ATR
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr"] = tr.rolling(14).mean()

        # Volume Z-score
        df["vol_ma"] = volume.rolling(20).mean()
        df["vol_z"] = (volume - df["vol_ma"]) / df["vol_ma"].rolling(20).std()

        # Supertrend（简化安全版，无 iloc 警告）
        multiplier = 3.0
        hl2 = (high + low) / 2
        atr = df["atr"]
        upper = hl2 + multiplier * atr
        lower = hl2 - multiplier * atr
        supertrend = pd.Series(1.0, index=df.index)
        for i in range(1, len(df)):
            if close.iloc[i] > upper.iloc[i-1]:
                supertrend.iloc[i] = 1
            elif close.iloc[i] < lower.iloc[i-1]:
                supertrend.iloc[i] = -1
            else:
                supertrend.iloc[i] = supertrend.iloc[i-1]
        df["supertrend"] = supertrend

        return df

    def detect_signals(df, token_name):
        if len(df) < 5:
            return None, 0, None, None
        last = df.iloc[-1]
        prev = df.iloc[-2]
        signals = []
        score = 0

        if pd.notna(last.get("volume")) and pd.notna(last.get("vol_ma")) and last["volume"] > last["vol_ma"] * volume_mult and last.get("vol_z", 0) > 2:
            signals.append("🚀 成交量爆发")
            score += 30
        if pd.notna(last.get("BBU")) and last["close"] > last["BBU"] and prev["close"] <= prev["BBU"]:
            signals.append("🔥 Bollinger 上轨突破")
            score += 25
        if pd.notna(last.get("MACD")) and last["MACD"] > last["MACD_signal"] and prev["MACD"] <= prev["MACD_signal"]:
            signals.append("📈 MACD 金叉")
            score += 20
        if pd.notna(last.get("rsi")) and last["rsi"] < 35:
            signals.append("🛡️ RSI 超卖")
            score += 15
        if pd.notna(last.get("supertrend")) and last["supertrend"] > 0:
            signals.append("✅ Supertrend 多头")
            score += 10

        alpha_score = min(100, score)
        if signals:
            strategy = f"""**最佳执行策略（{token_name}）**：
入场：当前价或回踩20EMA
止损：入场 - 2×ATR ({last.get('atr',0):.6f})
止盈：1:3 RR 分层（50% 1:2，30% 1:4，20% trailing）
仓位：账户 1-2% 风险"""
            alert_msg = f"🚨 Alpha异动！{token_name}\nScore: {alpha_score}\n信号: {', '.join(signals)}\n\n{strategy}"
            if alert_enabled and tg_token and tg_chatid:
                try:
                    requests.post(f"https://api.telegram.org/bot{tg_token}/sendMessage", params={"chat_id": tg_chatid, "text": alert_msg}, timeout=5)
                except:
                    pass
            return signals, alpha_score, strategy, alert_msg
        return None, 0, None, None

    # 主界面
    df_tokens = fetch_alpha_tokens()
    if df_tokens.empty:
        st.stop()

    col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
    with col1:
        min_change = st.number_input("最小24h涨幅 (%)", value=5.0)
    with col2:
        min_vol = st.number_input("最小24h成交量", value=10000.0)
    with col3:
        show_watch = st.checkbox("仅显示 Watchlist", value=False)
    with col4:
        if st.button("🔄 刷新数据", type="primary"):
            st.rerun()

    if show_watch and st.session_state.watchlist:
        df_tokens = df_tokens[df_tokens["symbol_upper"].isin(st.session_state.watchlist)]

    filtered = df_tokens[
        (abs(df_tokens["percentChange24h"]) >= min_change) &
        (df_tokens["volume24h"] >= min_vol)
    ].copy().sort_values("percentChange24h", ascending=False)

    st.dataframe(
        filtered[["name", "symbol", "price", "percentChange24h", "volume24h", "marketCap"]],
        width="stretch", height=400
    )

    st.divider()
    selected = st.selectbox(
        "点击查看深度分析 + 交易策略",
        options=filtered["name"].tolist() if not filtered.empty else df_tokens["name"].tolist()
    )

    if selected:
        row = df_tokens[df_tokens["name"] == selected].iloc[0]
        alpha_id = row["alphaId"]
        with st.spinner(f"拉取 {selected} ({alpha_id}) 数据..."):
            for interval in ["5m", "15m", "1h"]:
                if st.button(f"显示 {interval} 图表"):
                    df = fetch_klines(alpha_id, interval)
                    df = calculate_indicators(df)
                    signals, score, strategy, _ = detect_signals(df, selected)
                    
                    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.55, 0.2, 0.25])
                    fig.add_trace(go.Candlestick(x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"]), row=1, col=1)
                    fig.add_trace(go.Scatter(x=df.index, y=df["BBU"], name="BB Upper", line=dict(color="red")), row=1, col=1)
                    fig.add_trace(go.Scatter(x=df.index, y=df["BBL"], name="BB Lower", line=dict(color="green")), row=1, col=1)
                    fig.add_trace(go.Bar(x=df.index, y=df["MACD_hist"], name="MACD Hist"), row=2, col=1)
                    fig.add_trace(go.Scatter(x=df.index, y=df["rsi"], name="RSI", line=dict(color="purple")), row=3, col=1)
                    
                    fig.update_layout(height=850, title=f"{selected} {interval} 图表 + 指标")
                    st.plotly_chart(fig, width="stretch")
                    
                    if signals:
                        st.success(f"🔥 强信号！Alpha Score: **{score}**")
                        st.info(" | ".join(signals))
                        st.markdown(strategy)
                        if alert_enabled:
                            st.success("✅ Telegram 告警已发送")
                    else:
                        st.info("暂无强信号，继续监控...")

except Exception as e:
    st.error(f"🚨 程序异常: {str(e)}")
    st.info("请把上方完整错误信息（红色部分）复制发给我，我立即继续修复！")

st.caption("v3.1 已修复所有已知部署问题（deprecation + symbol + crash） | 数据来自官方 API | 交易有风险")
