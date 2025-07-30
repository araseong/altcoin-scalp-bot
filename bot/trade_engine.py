# bot/trade_engine.py
import logging
import math
from decimal import Decimal
from statistics import mean, stdev
from typing import Optional

import pandas as pd
from binance.exceptions import BinanceAPIException

from .binance_client import BinanceFutures
from .indicators import add_indicators
from .strategy import entry_signal, exit_signal


class TradeEngine:
    """
    - 자본금 × 30 % × 10배 레버리지 목표로 실제 노미널을 맞춰 줌
    - 24 h 변동률 상위 60개 종목 중 변동성(σ/μ) 비율이 min_vol_ratio 이상인 알트만 스캔
    - 동시 1포지션 제한
    """

    def __init__(
        self,
        client: BinanceFutures,
        interval: str,
        leverage: int,
        pos_pct: float,
        sl_pct: float,
        atr_window: int = 14,
    ):
        self.c = client
        self.interval = interval
        self.leverage = leverage
        self.pos_pct = pos_pct
        self.sl_pct = sl_pct
        self.atr_window = atr_window

        self.open_symbol: Optional[str] = None
        self.stop_order_id: Optional[int] = None

        # price tick 자리수, lotSize step 캐시
        self._prec = self.c._prec
        self._lot_step = self._build_lot_step()

        # tuning 파라미터 dict 를 외부(main.py) 에서 주입
        self.tuning: dict = {}

        logging.info(
            "=== Engine init. leverage=%s pos_pct=%s sl_pct=%s ===",
            leverage,
            pos_pct,
            sl_pct,
        )

    # ────────────────────────────────────────────────────────
    # PUBLIC 메서드
    # ────────────────────────────────────────────────────────
    def run_once(self):
        try:
            if self.open_symbol:
                self._monitor_position()
            else:
                self._scan_and_enter()
        except BinanceAPIException as api_err:
            logging.error("BinanceAPIException: %s", api_err)

    # ────────────────────────────────────────────────────────
    # 엔트리 스캔
    # ────────────────────────────────────────────────────────
    def _scan_and_enter(self):
        for sym in self.c.top_alt_movers(limit=60):  # 24 h 변동률 상위 60개
            # ① 최근 30분 데이터로 변동성(σ/μ) 계산
            df = self._load_klines(sym)
            closes = df.close.tail(30).tolist()
            if len(closes) < 30:
                continue
            pct_moves = [abs(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes))]
            vol_ratio = (stdev(pct_moves) / mean(pct_moves)) if mean(pct_moves) else 0

            # ② 변동성 필터
            min_vol = float(self.tuning.get("min_vol_ratio", 3))
            if vol_ratio < min_vol:
                logging.debug("Skip %s low vol_ratio %.2f", sym, vol_ratio)
                continue

            # ③ 지표 조건
            if entry_signal(df):
                price = df.close.iloc[-1]
                qty = self._position_size(price, sym)
                if qty <= 0:
                    continue

                self.c.set_leverage(sym, self.leverage)
                self.c.open_long(sym, qty)

                sl_price = round(price * (1 - self.sl_pct), self._prec[sym])
                sl_ord = self.c.stop_market(sym, "SELL", qty, sl_price)

                self.open_symbol, self.stop_order_id = sym, sl_ord["orderId"]
                logging.info(
                    "OPEN  %s qty=%s @ %.6f | SL=%.6f",
                    sym,
                    qty,
                    price,
                    sl_price,
                )
                break  # 동시 1포지션

    # ────────────────────────────────────────────────────────
    # 포지션 모니터
    # ────────────────────────────────────────────────────────
    def _monitor_position(self):
        df = self._load_klines(self.open_symbol)
        if exit_signal(df):
            self._close_position("TP (ATR down)")

    def _close_position(self, reason: str):
        try:
            self.c.cancel_order(self.open_symbol, self.stop_order_id)
        except Exception:
            pass
        self.c.close_position(self.open_symbol)
        logging.info("CLOSE %s  %s", self.open_symbol, reason)
        self.open_symbol, self.stop_order_id = None, None

    # ────────────────────────────────────────────────────────
    # 헬퍼
    # ────────────────────────────────────────────────────────
    def _load_klines(self, symbol: str) -> pd.DataFrame:
        raw = self.c.klines(symbol, self.interval, 200)
        cols = [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote",
            "count",
            "taker_buy_vol",
            "taker_buy_quote",
            "ignore",
        ]
        df = pd.DataFrame(raw, columns=cols)
        df[["open", "high", "low", "close", "volume"]] = df[
            ["open", "high", "low", "close", "volume"]
        ].astype(float)
        df = add_indicators(df, self.atr_window)
        return df

    # ───────────────────────────────
    # LOT_SIZE & 수량 계산 관련
    # ───────────────────────────────
    def _build_lot_step(self):
        step = {}
        info = self.c.client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] in self.c.alt_symbols:
                filt = next(f for f in s["filters"] if f["filterType"] == "LOT_SIZE")
                step[s["symbol"]] = Decimal(filt["stepSize"])
        return step

    def _round_qty(self, symbol: str, qty: float) -> float:
        step = self._lot_step[symbol]
        return float((Decimal(qty) // step) * step)

    def _position_size(self, price: float, symbol: str) -> float:
        """
        자본 × 30 % × 레버리지 10배 목표 노미널을 실제 체결 qty 로 맞춰 준다.
        stepSize 절삭 후 부족분이 있으면 목표의 97 % 이상이 될 때까지
        step 단위로 수량을 늘린다.
        """
        balance = self.c.balance_usdt()
        tgt_notional = balance * self.pos_pct * self.leverage
        step = self._lot_step[symbol]

        qty = self._round_qty(symbol, tgt_notional / price)
        while qty * price < tgt_notional * 0.97:  # 최대 3 % 오차 허용
            qty += float(step)

        min_qty = float(step)
        return max(qty, min_qty)
