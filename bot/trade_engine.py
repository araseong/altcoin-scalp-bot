import logging
from decimal import Decimal
from statistics import mean, stdev
from typing import Optional

import pandas as pd
from binance.exceptions import BinanceAPIException

from .binance_client import BinanceFutures
from .indicators import add_indicators
from .strategy import (
    ema_vwap_di_signal,
    obv_atr_rising,
    exit_signal,
)

class TradeEngine:
    """알트 스캘핑 엔진"""

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
        self.tp_orders: dict[str, list[int]] = {}

        self._prec = self.c._prec
        self._lot_step = self._build_lot_step()
        self.tuning: dict = {}

        logging.info(
            "=== Engine init. leverage=%s pos_pct=%s sl_pct=%s ===",
            leverage,
            pos_pct,
            sl_pct,
        )

    # ──────────────────────────────────
    # 주 루프
    # ──────────────────────────────────
    def run_once(self):
        try:
            if self.open_symbol:
                self._monitor_position()
            else:
                self._scan_and_enter()
        except BinanceAPIException as e:
            logging.error("BinanceAPIException: %s", e)

    # ──────────────────────────────────
    # 진입 스캔
    # ──────────────────────────────────
    def _scan_and_enter(self):
        look = int(self.tuning.get("lookback_obv_atr", 5))

        for sym in self.c.top_alt_movers(limit=60):
            # 변동성 필터
            if not self._spread_ok(sym, max_spread=0.0002):
                continue

            df = self._load_klines(sym)
            if not self._vol_ok(df):
                continue

            cond1 = ema_vwap_di_signal(df)
            cond2 = obv_atr_rising(df, lookback=look)

            if cond1 and cond2:
                price = df.close.iloc[-1]
                qty   = self._position_size(price, sym)
                if qty <= 0:
                    continue

                self.c.set_leverage(sym, self.leverage)
                self.c.open_long(sym, qty)

                # SL
                sl_price = round(price * (1 - self.sl_pct), self._prec[sym])
                sl_ord   = self.c.stop_market(sym, "SELL", qty, sl_price)

                # TP 0.6·1.2 ATR 분할
                atr = df.atr.iloc[-1]
                tp1_price = round(price + 0.6 * atr, self._prec[sym])
                tp2_price = round(price + 1.2 * atr, self._prec[sym])

                tp1 = self.c.client.futures_create_order(
                    symbol=sym, side="SELL", type="LIMIT", quantity=qty * 0.3,
                    price=tp1_price, timeInForce="GTC"
                )
                tp2 = self.c.client.futures_create_order(
                    symbol=sym, side="SELL", type="LIMIT", quantity=qty * 0.3,
                    price=tp2_price, timeInForce="GTC"
                )

                self.tp_orders[sym] = [tp1["orderId"], tp2["orderId"]]
                self.open_symbol, self.stop_order_id = sym, sl_ord["orderId"]

                logging.info(
                    "OPEN  %s qty=%.3f @ %.6f | SL=%.6f TP=%.6f/%.6f",
                    sym, qty, price, sl_price, tp1_price, tp2_price
                )
                break  # 동시 1포지션

    # ──────────────────────────────────
    # 포지션 모니터
    # ──────────────────────────────────
    def _monitor_position(self):
        df = self._load_klines(self.open_symbol)
        if exit_signal(df):
            self._close_position("OBV & +DI fall")

    def _close_position(self, reason: str):
        # TP·SL 취소
        for oid in self.tp_orders.get(self.open_symbol, []):
            try: self.c.cancel_order(self.open_symbol, oid)
            except Exception: pass

        try: self.c.cancel_order(self.open_symbol, self.stop_order_id)
        except Exception: pass

        # 시장가 청산
        self.c.close_position(self.open_symbol)

        # PNL 정보
        pos_info  = self.c.client.futures_position_information(
            symbol=self.open_symbol)[0]
        pnl  = float(pos_info["unRealizedProfit"])
        exit_price = float(pos_info["markPrice"])

        logging.info(
            "CLOSE %s pnl=%.4f usdt exit=%.6f  %s",
            self.open_symbol, pnl, exit_price, reason
        )

        self.tp_orders.pop(self.open_symbol, None)
        self.open_symbol, self.stop_order_id = None, None

    # ──────────────────────────────────
    # 헬퍼들
    # ──────────────────────────────────
    def _spread_ok(self, symbol: str, max_spread=0.0002):
        ob = self.c.client.futures_order_book(symbol=symbol, limit=5)
        bid = float(ob["bids"][0][0]); ask = float(ob["asks"][0][0])
        return (ask - bid) / bid < max_spread

    def _vol_ok(self, df: pd.DataFrame):
        pct = df.close.pct_change().tail(30).abs()
        vol_ratio = (stdev := pct.std(), mean := pct.mean())[0] / (mean or 1e-8)
        min_vol = float(self.tuning.get("min_vol_ratio", 2.0))
        return vol_ratio >= min_vol

    def _load_klines(self, symbol: str) -> pd.DataFrame:
        raw = self.c.klines(symbol, self.interval, 200)
        cols = [
            "open_time","open","high","low","close","volume","close_time",
            "quote","count","taker_buy_vol","taker_buy_quote","ignore"
        ]
        df = pd.DataFrame(raw, columns=cols)
        df[["open","high","low","close","volume"]] = df[
            ["open","high","low","close","volume"]].astype(float)
        return add_indicators(df, self.atr_window)

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
        bal = self.c.balance_usdt()
        tgt = bal * self.pos_pct * self.leverage
        step = self._lot_step[symbol]

        qty = self._round_qty(symbol, tgt / price)
        while qty * price < tgt * 0.97:
            qty += float(step)
        return max(qty, float(step))
