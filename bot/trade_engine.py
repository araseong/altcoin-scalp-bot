# bot/trade_engine.py
# ────────────────────────────────────────────────────────────────────
"""
TradeEngine
===========

• 24 h 변동률 상위 N 개 알트코인을 스캔
• 진입 조건
    1. ttm_entry_signal(df)   ←  (볼린저·켈트너 스퀴즈 해제 + VAH 돌파 + RSI 모멘텀)
• 진입 시
    · 레버리지 : config [trade].leverage
    · 포지션 : 잔고 × position_pct × leverage  (LOT_SIZE 반올림)
    · Stop‑Loss : 진입가 * (1 – sl_pct)  (시장 STOP)
• 포지션 관리는 단일 심볼 only
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from statistics import mean, stdev
from typing import Dict, List, Optional

import pandas as pd

from .binance_client import BinanceFutures
from .strategy       import ttm_entry_signal, exit_signal

# ────────────────────────────────────────────────────────────────────


class TradeEngine:
    def __init__(
        self,
        client: BinanceFutures,
        interval: str,
        leverage: int,
        pos_pct: float,
        sl_pct: float,
    ) -> None:
        self.c = client
        self.interval = interval
        self.leverage = leverage
        self.pos_pct = pos_pct
        self.sl_pct = sl_pct

        self.open_symbol: Optional[str] = None
        self.stop_order_id: Optional[int] = None

        # 심볼별 소수점·LOT_SIZE 캐시
        self._prec: dict[str, int] = self.c._prec
        self._lot_step: dict[str, Decimal] = self._build_lot_step()

        logging.info(
            "=== Engine init. leverage=%s pos_pct=%s sl_pct=%s ===",
            leverage,
            pos_pct,
            sl_pct,
        )

    # ────────────────────────── 메인 루프 1회
    def run_once(self) -> None:
        if self.open_symbol:
            self._monitor_position()
        else:
            self._scan_and_enter()

    # ────────────────────────── 진입 스캔
    def _scan_and_enter(self) -> None:
        movers = self.c.top_alt_movers(limit=60)
        if not movers:
            return

        for sym in movers:
            df = self._load_klines(sym)
            if df.empty:
                continue

            if not ttm_entry_signal(df):          # ★ 새 전략 함수
                continue

            price = df.close.iloc[-1]
            qty   = self._position_size(price, sym)
            if qty <= 0:
                continue

            # 레버리지·주문
            self.c.set_leverage(sym, self.leverage)
            self.c.open_long(sym, qty)

            sl_price = round(price * (1 - self.sl_pct), self._prec[sym])
            sl = self.c.stop_market(sym, "SELL", qty, sl_price)

            self.open_symbol = sym
            self.stop_order_id = sl["orderId"]

            logging.info(
                "OPEN %s qty=%.3f @ %.6f | SL=%.6f", sym, qty, price, sl_price
            )
            break  # 단일 포지션만 허용

    # ────────────────────────── 포지션 모니터
    def _monitor_position(self) -> None:
        df = self._load_klines(self.open_symbol)
        if df.empty:
            return

        if exit_signal(df):
            self._close_position("Exit‑signal")

    def _close_position(self, reason: str) -> None:
        try:
            self.c.cancel_order(self.open_symbol, self.stop_order_id)
        except Exception:
            pass

        self.c.close_position(self.open_symbol)

        info = self.c.client.futures_position_information(symbol=self.open_symbol)[0]
        pnl = float(info["unRealizedProfit"])
        exit_price = float(info["markPrice"])

        logging.info(
            "CLOSE %s pnl=%.4f USDT @ %.6f  %s", self.open_symbol, pnl, exit_price, reason
        )

        self.open_symbol = None
        self.stop_order_id = None

    # ────────────────────────── 보조 함수들
    def _load_klines(self, symbol: str) -> pd.DataFrame:
        """200 개 캔들 + indicator 포함 DataFrame 반환"""
        try:
            raw = self.c.klines(symbol, self.interval, 200)
        except Exception as e:
            logging.debug("klines error %s: %s", symbol, e)
            return pd.DataFrame()

        if not raw:
            return pd.DataFrame()

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

        # indicator 는 strategy.ttm_entry_signal 내부에서 add_indicators 호출
        return df

    def _build_lot_step(self) -> Dict[str, Decimal]:
        step = {}
        info = self.c.client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] in self.c.alt_symbols:
                f = next(f for f in s["filters"] if f["filterType"] == "LOT_SIZE")
                step[s["symbol"]] = Decimal(f["stepSize"])
        return step

    def _round_qty(self, symbol: str, qty: float) -> float:
        step = self._lot_step[symbol]
        return float((Decimal(qty) // step) * step)

    def _position_size(self, price: float, symbol: str) -> float:
        bal = self.c.balance_usdt()
        target_notional = bal * self.pos_pct * self.leverage
        step = self._lot_step[symbol]

        qty = self._round_qty(symbol, target_notional / price)
        while qty * price < target_notional * 0.97:
            qty += float(step)
        return max(qty, float(step))
