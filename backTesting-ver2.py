import pyupbit
import pandas as pd
import numpy as np
import time

def get_rsi(df, period=14):
    delta = df['close'].diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(window=period).mean()
    avg_loss = pd.Series(loss).rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def get_top_volume_tickers(ratio=0.1):
    # 모든 KRW 마켓 시세 받아오기 (업비트 ticker API)
    tickers = pyupbit.get_tickers(fiat="KRW")
    volumes = []

    for ticker in tickers:
        try:
            df = pyupbit.get_ohlcv(ticker, interval="day", count=1)
            if df is None or len(df) == 0:
                continue
            volume = df['volume'].iloc[-1]
            volumes.append((ticker, volume))
            time.sleep(0.05)  # API 부하 방지
        except Exception:
            continue

    # 거래량 기준 내림차순 정렬
    volumes.sort(key=lambda x: x[1], reverse=True)

    # 상위 ratio 만큼만 반환
    top_count = int(len(volumes) * ratio)
    top_tickers = [v[0] for v in volumes[:top_count]]

    return top_tickers

def main():
    top_tickers = get_top_volume_tickers(ratio=0.1)  # 상위 10%
    print(f"🔍 거래량 상위 10% 코인 {len(top_tickers)}개 필터링 중...\n")

    for ticker in top_tickers:
        try:
            df = pyupbit.get_ohlcv(ticker, interval="minute60", count=50)
            if df is None or len(df) < 20:
                continue

            df['ma20'] = df['close'].rolling(window=20).mean()
            df['rsi'] = get_rsi(df)

            current_price = df['close'].iloc[-1]
            ma20 = df['ma20'].iloc[-1]
            rsi = df['rsi'].iloc[-1]

            lower_bound = ma20 * 0.95
            upper_bound = ma20 * 1.05

            if lower_bound <= current_price <= upper_bound:
                print(f"📈 {ticker} | 현재가: {current_price:.2f} | MA20: {ma20:.2f} (±5%) | RSI: {rsi:.2f}")

            time.sleep(0.1)

        except Exception:
            continue

if __name__ == "__main__":
    main()
