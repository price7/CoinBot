import pyupbit
import numpy as np
import time

class CustomBackTest:
    def __init__(self, df, start_cash=1_000_000, risk_ratio=0.5, rsi_limit=45, take_profit_ratio=1.10, stop_loss_ratio=0.95, fee=0.0005):
        self.df = df.copy()
        self.start_cash = start_cash
        self.current_cash = start_cash
        self.highest_cash = start_cash
        self.lowest_cash = start_cash

        self.trade_count = 0
        self.win_count = 0
        self.accumulated_ror = 1
        self.mdd = 0
        self.fee = fee

        self.risk_ratio = risk_ratio          # 한 거래에 투자하는 자금 비율 (예: 0.5 = 50%)
        self.rsi_limit = rsi_limit            # RSI 매수 임계치
        self.take_profit_ratio = take_profit_ratio  # 익절 목표 배수 (예: 1.10 = 10% 수익)
        self.stop_loss_ratio = stop_loss_ratio        # 손절 목표 배수 (예: 0.95 = 5% 손실)

        self.total_fee_paid = 0               # 누적 수수료 총액

    def calculate_indicators(self):
        df = self.df
        df['ma20'] = df['close'].rolling(window=20).mean()

        delta = df['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        rs = avg_gain / avg_loss
        df['rsi'] = 100 - (100 / (1 + rs))

        self.df = df

    def execute(self, name="코인"):
        self.calculate_indicators()
        df = self.df

        for i in range(20, len(df)):
            row = df.iloc[i]

            # 이전 ma20 값 (추세 필터링용)
            prev_ma20 = df.iloc[i-1]['ma20']

            buy_signal = (
                row['close'] > row['ma20'] and
                row['rsi'] < self.rsi_limit
            )

            if buy_signal:
                self.trade_count += 1

                entry = row['open']
                high = row['high']
                low = row['low']

                take_profit = entry * self.take_profit_ratio
                stop_loss = entry * self.stop_loss_ratio

                if high >= take_profit:
                    exit_price = take_profit
                elif low <= stop_loss:
                    exit_price = stop_loss
                else:
                    exit_price = row['close']

                # 매수 수수료 + 매도 수수료 계산 (거래금액 대비)
                trade_amount = self.current_cash * self.risk_ratio
                buy_fee = trade_amount * self.fee
                sell_fee = trade_amount * (exit_price / entry) * self.fee
                self.total_fee_paid += (buy_fee + sell_fee)

                ror = exit_price / entry
                self.win_count += 1 if ror > 1 else 0
            else:
                ror = 1

            # 수익 반영 (수수료 제외 후 계산)
            profit = self.current_cash * self.risk_ratio * (ror - 1)
            self.current_cash = self.current_cash * (1 - self.risk_ratio) + self.current_cash * self.risk_ratio + profit

            self.accumulated_ror = self.current_cash / self.start_cash
            self.highest_cash = max(self.highest_cash, self.current_cash)
            self.lowest_cash = min(self.lowest_cash, self.current_cash)
            dd = (self.highest_cash - self.current_cash) / self.highest_cash * 100
            self.mdd = max(self.mdd, dd)

        self.result(name)

    def result(self, name="코인"):
        profit_amount = self.current_cash - self.start_cash
        drawdown_amount = self.highest_cash - self.current_cash
        print("=" * 60)
        print(f"📊 [{name}] 4시간 봉, 3개월 치, 백테스트 결과")
        print("-" * 60)
        print(f"총 거래 횟수     : {self.trade_count}")
        print(f"승리 횟수        : {self.win_count}")
        print(f"승률             : {self.win_count / self.trade_count * 100:.2f}%" if self.trade_count else "승률: 0%")
        print(f"누적 수익률      : {self.accumulated_ror:.4f}")
        print(f"현재 잔액        : {self.current_cash:,.0f} 원")
        print(f"최고 잔액        : {self.highest_cash:,.0f} 원")
        print(f"최저 잔액        : {self.lowest_cash:,.0f} 원")
        print(f"수익액           : {profit_amount:,.0f} 원")
        print(f"거래 수수료 총액 : {self.total_fee_paid:,.0f} 원")
        print(f"순수익 (수수료 제외) : {profit_amount - self.total_fee_paid:,.0f} 원")
        print(f"낙폭(현재-최고)   : {drawdown_amount:,.0f} 원")
        print(f"최대 낙폭 (MDD)  : {self.mdd:.2f}%")
        print("=" * 60)


# 총 투자금 1,000,000 원을 각 코인별 비중에 맞게 분배
total_investment = 1_000_000
allocation = {
    "KRW-BTC": 0.0,  # 40%
    "KRW-BLAST": 0.5,  # 40%
    "KRW-ARK": 0.5  # 20%
}

tickers = ["KRW-BTC", "KRW-BLAST", "KRW-ARK"]

for ticker in tickers:
    print(f"=== {ticker} 데이터 로딩 중 ... ===")
    df = pyupbit.get_ohlcv(ticker, interval="minute240", count=1080)  # 4시간봉, 3개월치
    if df is None or df.empty:
        print(f"[{ticker}] 데이터가 없습니다.")
        continue

    invested_cash = total_investment * allocation[ticker]

    if invested_cash <= 0:
        print(f"[{ticker}] 투자 금액이 0 이하이므로 백테스트를 건너뜁니다.")
        continue

    # 코인별 전략 파라미터 세팅
    if ticker == "KRW-BTC":
        backtest = CustomBackTest(
            df,
            start_cash=invested_cash,
            risk_ratio=1.0,
            rsi_limit=42,    
            take_profit_ratio=1.15,
            stop_loss_ratio=0.97 
        )

    elif ticker == "KRW-BLAST":
        backtest = CustomBackTest(
            df,
            start_cash=invested_cash,
            risk_ratio=1.0,
            rsi_limit=60,      
            take_profit_ratio=1.15,  
            stop_loss_ratio=0.92   
        )

    elif ticker == "KRW-ARK":
        backtest = CustomBackTest(
            df,
            start_cash=invested_cash,
            risk_ratio=1.0,
            rsi_limit=60,           
            take_profit_ratio=1.15,
            stop_loss_ratio=0.92       
        )

    backtest.execute(name=ticker)
    time.sleep(1)
