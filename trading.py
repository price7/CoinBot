import pyupbit
import time
import requests
import json
import os
import datetime
import threading
from slack_sdk.web import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

with open("key_info.txt") as f:
    lines = f.readlines()
    acc_key = lines[0].strip()
    sec_key = lines[1].strip()
    slack_bot_token = lines[2].strip()      # xoxb- 토큰
    slack_app_token = lines[3].strip()      # xapp- 토큰 (Socket Mode 앱 토큰)

slack_channel = "C095PHAD4E8" # 채널 ID 값으로 읽어와야 함, 채널명: #코인봇-테스트
buy_log_file = "buy_log.json"

def post_message(token, channel, text):
    res = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": "Bearer " + token},
        data={"channel": channel, "text": text}
    )
    if res.status_code != 200 or not res.json().get("ok"):
        print("Slack message send failed:", res.text)

def send_msg(token, channel, text):
    print(text)
    post_message(token, channel, text)

def get_rsi(df, period=14):
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def load_buy_log():
    if os.path.exists(buy_log_file):
        with open(buy_log_file, "r") as f:
            return json.load(f)
    return {}

def save_buy_log(log):
    with open(buy_log_file, "w") as f:
        json.dump(log, f)

class CoinBot:
    def __init__(self, slack_bot_token, slack_app_token, slack_channel):
        self.upbit = pyupbit.Upbit(acc_key, sec_key)
        self.slack_channel = slack_channel
        self.buy_flag = load_buy_log()

        self.config = {
            "KRW-AERGO": {"rsi_limit": 99, "take_profit_ratio": 1.10, "stop_loss_ratio": 0.93, "risk_ratio": 1.0},
            # "KRW-SNT": {"rsi_limit": 70, "take_profit_ratio": 1.05, "stop_loss_ratio": 0.92, "risk_ratio": 0.5},
        }

        self.start_cash = self.get_krw_balance()
        self.current_cash = self.start_cash
        self.total_fee_paid = 0

        self.running = False
        self.total_fee_paid = 0

        # 슬랙 WebClient & SocketModeClient 초기화
        self.web_client = WebClient(token=slack_bot_token)
        self.socket_mode_client = SocketModeClient(app_token=slack_app_token, web_client=self.web_client)
        self.socket_mode_client.socket_mode_request_listeners.append(self.process_slack_events)

        self.sell_thread = None

        self.send("🚀 코인봇 시작")

    def send(self, text, channel=None):
        ch = channel if channel else self.slack_channel
        print(f"[Slack] {text}")
        try:
            self.web_client.chat_postMessage(channel=ch, text=text)
        except Exception as e:
            print(f"슬랙 메시지 전송 실패: {e}")

    def get_balance(self, ticker):
        try:
            bal = self.upbit.get_balance(ticker)
            return bal
        except Exception as e:
            self.send(f"🚨 잔고 조회 실패: {ticker} / {e}")
            return 0

    def get_krw_balance(self):
        try:
            return self.upbit.get_balance("KRW")
        except Exception as e:
            self.send(f"🚨 KRW 잔고 조회 실패: {e}")
            return 0

    def buy_coin(self, ticker, amount_krw):
        try:
            ret = self.upbit.buy_market_order(ticker, amount_krw)
            if ret:
                fee = amount_krw * 0.0005  # 매수 수수료 계산
                self.total_fee_paid += fee
                self.send(f"✅ [{ticker}] 매수 성공: {amount_krw:,.0f}원 (수수료 약 {fee:,.0f}원)")
                return True
            else:
                self.send(f"❌ [{ticker}] 매수 실패")
                return False
        except Exception as e:
            self.send(f"🚨 시스템 오류 발생 (매수): {e}")
            return False

    def sell_coin(self, ticker, coin_amount):
        try:
            ret = self.upbit.sell_market_order(ticker, coin_amount)
            if ret:
                self.send(f"✅ [{ticker}] 매도 성공: {coin_amount}개")
                return True
            else:
                self.send(f"❌ [{ticker}] 매도 실패")
                return False
        except Exception as e:
            self.send(f"🚨 시스템 오류 발생 (매도): {e}")
            return False

    def execute_buy(self):
        print("매수 조건 검사 중 ...... ", datetime.datetime.now())

        for ticker, param in self.config.items():
            df = pyupbit.get_ohlcv(ticker, interval="minute240", count=50)
            if df is None or df.empty:
                self.send(f"❌ [{ticker}] 4시간봉 데이터 없음")
                continue

            df['ma20'] = df['close'].rolling(window=20).mean()
            df['rsi'] = get_rsi(df)

            latest = df.iloc[-1]
            rsi_val = latest['rsi']
            ma20_val = latest['ma20']
            close_val = latest['close']

            # ⬇️ 총 자산 기준 목표 투자금 계산
            total_asset = self.get_total_asset()
            target_amount = total_asset * param["risk_ratio"]

            # ⬇️ 현재 투자된 금액 확인
            current_flag = self.buy_flag.get(ticker, {})
            current_invest = current_flag.get("amount_krw", 0)
            remain_amount = target_amount - current_invest

            print("총 자산 ...... ", total_asset)
            print("목표 비중 ...... ", target_amount)
            print("현재 투자된 금액 ...... ", current_invest)
            print("남은 투자 금액 ...... ", remain_amount)

            # ✅ 조건 만족 여부 판단 (처음 매수든 추가 매수든)
            rsi_check = rsi_val < param["rsi_limit"]
            price_check = close_val > ma20_val

            if not (rsi_check and price_check):
                reasons = []
                if not rsi_check:
                    reasons.append(f"RSI {rsi_val:.2f} >= {param['rsi_limit']}")
                if not price_check:
                    reasons.append(f"종가 {int(close_val):,} <= MA20 {int(ma20_val):,}")
                reason_text = " & ".join(reasons)
                self.send(f"[{ticker}] 매수 조건 미충족: ({reason_text})")
                continue

            # ✅ 목표 금액을 거의 다 썼다면 추가 매수 금지
            if current_invest >= target_amount * 0.98:
                self.send(f"[{ticker}] 이미 모두 매수된 상태입니다 (총 투자금: {current_invest:,.0f}원)")
                continue

            # ✅ 실제 KRW 잔고와 비교하여 사용할 수 있는 금액 결정
            krw_balance = self.get_krw_balance()
            buy_amount = min(remain_amount, krw_balance / 1.001)  # 수수료 0.05%를 감안한 값, 여유롭게 0.1%로 설정

            print("실제 투자 금액 ...... ", buy_amount)

            if buy_amount < 5000:
                self.send(f"[{ticker}] 잔고 부족으로 매수 불가 (가능 금액: {buy_amount:,.0f}원)")
                continue

            # ✅ 매수 실행
            success = self.buy_coin(ticker, buy_amount)
            if success:
                price = pyupbit.get_current_price(ticker)
                if price is None:
                    self.send(f"⚠️ [{ticker}] 현재가 조회 실패. 시가 기준 사용")
                    price = latest['open']

                now_str = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

                if ticker in self.buy_flag:
                    prev_amount = current_invest
                    prev_price = self.buy_flag[ticker]["buy_price"]

                    new_amount = buy_amount
                    new_price = price

                    # ⬇️ 평단가 갱신
                    avg_price = ((prev_price * prev_amount) + (new_price * new_amount)) / (prev_amount + new_amount)

                    self.buy_flag[ticker]["amount_krw"] = prev_amount + new_amount
                    self.buy_flag[ticker]["buy_price"] = avg_price
                    self.buy_flag[ticker]["buy_time"] = now_str
                else:
                    self.buy_flag[ticker] = {
                        "buy_price": price,
                        "buy_time": now_str,
                        "amount_krw": buy_amount
                    }

                save_buy_log(self.buy_flag)


    def get_total_asset(self):
        krw = self.get_krw_balance()
        total = krw

        for ticker in self.config.keys():
            try:
                amount = self.get_balance(ticker)
                price = pyupbit.get_current_price(ticker)

                if amount is not None and price is not None:
                    total += amount * price
                else:
                    self.send(f"⚠️ [{ticker}] 잔고 또는 현재가 조회 실패. 총 자산 계산에서 제외됨")
            except Exception as e:
                self.send(f"🚨 [{ticker}] 자산 계산 중 오류: {e}")

        return total

    def check_sell_condition(self, ticker, buy_price, take_profit_ratio, stop_loss_ratio):
        df_1m = pyupbit.get_ohlcv(ticker, interval="minute1", count=1)
        if df_1m is None or df_1m.empty:
            return False, None

        current_high = df_1m['high'].iloc[-1]
        current_low = df_1m['low'].iloc[-1]

        take_profit_price = buy_price * take_profit_ratio
        stop_loss_price = buy_price * stop_loss_ratio

        if current_high >= take_profit_price:
            return True, take_profit_price
        elif current_low <= stop_loss_price:
            return True, stop_loss_price
        else:
            return False, None

    def execute_sell(self):
        print("매도 조건 검사 중 ...... ", datetime.datetime.now())
        print("총 자산 ...... ", self.get_total_asset())

        for ticker, buy_info in list(self.buy_flag.items()):
            buy_price = buy_info['buy_price']
            amount_krw = buy_info.get("amount_krw", 0)
            param = self.config.get(ticker)
            if param is None:
                continue

            should_sell, price = self.check_sell_condition(
                ticker, buy_price,
                param["take_profit_ratio"],
                param["stop_loss_ratio"]
            )

            if should_sell:
                coin_amount = self.get_balance(ticker)
                if coin_amount == 0:
                    self.send(f"[{ticker}] 잔고 없음, 매수 상태 초기화")
                    self.buy_flag.pop(ticker)
                    save_buy_log(self.buy_flag)
                    continue

                success = self.sell_coin(ticker, coin_amount)
                if success:
                    price_df = pyupbit.get_current_price([ticker])

                    if isinstance(price_df, dict) and ticker in price_df:
                        current_price = price_df[ticker]
                    elif isinstance(price_df, float):
                        current_price = price_df
                    else:
                        self.send(f"⚠️ [{ticker}] 현재가 조회 실패 → 매도 체결가 정확도 낮음")
                        current_price = price  # fallback

                    sell_value = current_price * coin_amount
                    fee = sell_value * 0.0005
                    self.total_fee_paid += fee

                    # 개별 수익 계산 (amount_krw 기준)
                    profit_amount = sell_value - amount_krw
                    net_profit = profit_amount - fee
                    profit_percent = (profit_amount / amount_krw) * 100 if amount_krw > 0 else 0

                    result_type = "익절" if current_price >= buy_price else "손절"
                    target_label = "목표가" if result_type == "익절" else "손절가"
                    emoji = "📈" if result_type == "익절" else "📉"

                    profit_msg = f"""
    {emoji} [{ticker}] {result_type} 매도
    매도가: {current_price:,.0f}원
    {target_label}: {price:,.0f}원
    평균 매수가: {buy_price:,.0f}원
    총 투자금: {amount_krw:,.0f} 원
    매도금액: {sell_value:,.0f} 원
    수익률: {profit_percent:.2f}%
    수익액: {profit_amount:,.0f} 원
    수수료: {fee:,.0f} 원
    순수익: {net_profit:,.0f} 원
    """
                    self.send(profit_msg)
                    self.buy_flag.pop(ticker)
                    save_buy_log(self.buy_flag)

            else:
                # 매도 조건 미충족 → 현재 수익률 출력
                price_df = pyupbit.get_current_price([ticker])
                if isinstance(price_df, dict) and ticker in price_df:
                    current_price = price_df[ticker]
                elif isinstance(price_df, float):
                    current_price = price_df
                else:
                    current_price = buy_price  # fallback

                profit_percent = ((current_price - buy_price) / buy_price) * 100
                print(f"........ 매도 안 함 : {ticker}의 수익률 = {profit_percent:.2f}%")

    def wait_until_next_4h_candle(self):
        now = datetime.datetime.now()
        next_hour = (now.hour // 4 + 1) * 4
        if next_hour == 24:
            next_time = datetime.datetime(now.year, now.month, now.day) + datetime.timedelta(days=1)
        else:
            next_time = datetime.datetime(now.year, now.month, now.day, next_hour)
        next_time += datetime.timedelta(minutes=1)  # 1분 지연

        while self.running and datetime.datetime.now() < next_time:
            time.sleep(5)  # 5초씩 나눠서 자주 체크

    def sell_loop(self):
        while self.running:
            try:
                # 매도 대상이 있을 때만 실행
                if self.has_coin_to_sell():
                    self.execute_sell()
                    time.sleep(300)
                else:
                    # 보유 중인 코인이 없으면 10분 정도 기다렸다가 다시 확인
                    print(f"매도 할 코인이 없음")
                    time.sleep(600)
            except Exception as e:
                self.send(f"🚨 매도 중 오류 발생: {e}")
                time.sleep(60)

    def has_coin_to_sell(self):
        """보유 중인 매도할 코인이 있는지 확인"""
        balances = self.upbit.get_balances()
        for b in balances:
            if b['currency'] != 'KRW':
                volume = float(b['balance'])
                avg_buy_price = float(b.get('avg_buy_price', 0))
                if volume > 0 and avg_buy_price > 0:
                    return True
        return False

    def process_slack_events(self, client: SocketModeClient, req: SocketModeRequest):
        if req.type == "events_api":
            event = req.payload.get("event", {})

            # 이벤트 수락 응답 꼭 해줘야 함
            response = SocketModeResponse(envelope_id=req.envelope_id)
            client.send_socket_mode_response(response)

            # 메시지 이벤트 처리
            if event.get("type") == "message" and "text" in event:
                text = event["text"].strip()
                channel = event["channel"]

                if text == "시작" and not self.running:
                    self.running = True
                    self.send("✅ 매매 시작합니다!", channel)

                    # 매도 루프 별도 스레드 시작
                    sell_thread = threading.Thread(target=self.sell_loop, daemon=True)
                    self.sell_thread = sell_thread  # ⬅️ 추적용으로 저장
                    sell_thread.start()

                    # 매수 루프는 메인 루프에서 실행 (아래 참고)
                elif text == "종료" and self.running:
                    self.running = False
                    self.send("🛑 매매를 중단합니다.", channel)

    def run(self):
        # Socket Mode 연결 시작
        self.socket_mode_client.connect()
        self.send("🤖 슬랙 Socket Mode 연결 성공. 명령을 기다립니다...")

        while True:
            try:
                # 매수 루프
                if self.running:
                    self.wait_until_next_4h_candle()
                    if self.running:  # 다시 한 번 확인
                        self.execute_buy()
                        # time.sleep(60)
                else:
                    time.sleep(5)  # 매매 중단 상태에서는 짧게 쉬기
            except Exception as e:
                self.send(f"🚨 매수 중 오류 발생: {e}")

if __name__ == "__main__":
    bot = CoinBot(slack_bot_token, slack_app_token, slack_channel)
    bot.run()
