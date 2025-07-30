import logging
from decimal import Decimal
from statistics import mean, stdev
from typing import Optional, List, Dict

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
    """
    • 24 h 변동률 상위 60개 알트 스캔
    • EMA+VWAP+DI 전환 ∩ OBV·ATR 상승 → Long
    • SL: 진입가‑2 %, TP: +0.6·1.2 ATR 분할
    • 손절: OBV & +DI 동시 하락
    • 동시 1포지션
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
        self.tp_orders: Dict[str, List[int]] = {}

        self._prec = self.c._prec
        self._lot_step = self._build_lot_step()
        self.tuning: dict = {}

        logging.info(
            "=== Engine init. leverage=%s pos_pct=%s sl_pct=%s ===",
            leverage, pos_pct, sl_pct
        )

    # ────────────────────────── 루프 1회
    def run_once(self):
        try:
            if self.open_symbol:
                self._monitor_position()
            else:
                self._scan_and_enter()
        except BinanceAPIException as e:
            logging.error("BinanceAPIException: %s", e)

    # ────────────────────────── 진입 스캔
    def _scan_and_enter(self):
        look = int(self.tuning.get("lookback_obv_atr", 5))
        movers = self.c.top_alt_movers(limit=60)
        if not movers:
            logging.debug("top_alt_movers empty")
            return

        for sym in movers:
            if not self._spread_ok(sym, 0.0002):
                continue

            df = self._load_klines(sym)
            if df.empty or len(df) < 60:
                logging.debug("skip %s: empty klines", sym)
                continue

            if not self._vol_ok(df):
                continue

            if not (ema_vwap_di_signal(df) and obv_atr_rising(df, look)):
                continue

            price = df.close.iloc[-1]
            qty = self._position_size(price, sym)
            if qty <= 0:
                continue

            self.c.set_leverage(sym, self.leverage)
            self.c.open_long(sym, qty)

            sl_price = round(price * (1 - self.sl_pct), self._prec[sym])
            sl = self.c.stop_market(sym, "SELL", qty, sl_price)

            atr = df.atr.iloc[-1]
            tp1_price = round(price + 0.6 * atr, self._prec[sym])
            tp2_price = round(price + 1.2 * atr, self._prec[sym])

            tp1 = self.c.client.futures_create_order(
                symbol=sym, side="SELL", type="LIMIT",
                quantity=qty * 0.30, price=tp1_price, timeInForce="GTC"
            )
            tp2 = self.c.client.futures_create_order(
                symbol=sym, side="SELL", type="LIMIT",
                quantity=qty * 0.30, price=tp2_price, timeInForce="GTC"
            )

            self.open_symbol = sym
            self.stop_order_id = sl["orderId"]
            self.tp_orders[sym] = [tp1["orderId"], tp2["orderId"]]

            logging.info(
                "OPEN  %s qty=%.3f @ %.6f | SL=%.6f TP=%.6f/%.6f",
                sym, qty, price, sl_price, tp1_price, tp2_price
            )
            break   # 동시 1포지션

    # ────────────────────────── 포지션 관리
    def _monitor_position(self):
        df = self._load_klines(self.open_symbol)
        if not df.empty and exit_signal(df):
            self._close_position("OBV & +DI fall")

    def _close_position(self, reason: str):
        for oid in self.tp_orders.get(self.open_symbol, []):
            try:
                self.c.cancel_order(self.open_symbol, oid)
            except Exception:
                pass
        try:
            self.c.cancel_order(self.open_symbol, self.stop_order_id)
        except Exception:
            pass

        self.c.close_position(self.open_symbol)

        info = self.c.client.futures_position_information(
            symbol=self.open_symbol)[0]
        pnl = float(info["unRealizedProfit"])
        exit_price = float(info["markPrice"])

        logging.info(
            "CLOSE %s pnl=%.4f usdt exit=%.6f  %s",
            self.open_symbol, pnl, exit_price, reason
        )

        self.tp_orders.pop(self.open_symbol, None)
        self.open_symbol, self.stop_order_id = None, None

    # ────────────────────────── 헬퍼
    def _spread_ok(self, symbol: str, max_spread=0.0002) -> bool:
        ob = self.c.client.futures_order_book(symbol=symbol, limit=5)
        if not ob["bids"] or not ob["asks"]:
            logging.debug("skip %s: empty order book", symbol)
            return False
        bid = float(ob["bids"][0][0])
        ask = float(ob["asks"][0][0])
        return (ask - bid) / bid < max_spread

    def _vol_ok(self, df: pd.DataFrame) -> bool:
        pct = df.close.pct_change().tail(30).abs()
        mu = pct.mean()
        sigma = pct.std()
        vol_ratio = sigma / (mu or 1e-8)
        min_vol = float(self.tuning.get("min_vol_ratio", 2.0))
        if vol_ratio < min_vol:
            logging.debug("skip low vol_ratio %.2f", vol_ratio)
            return False
        return True

    def _load_klines(self, symbol: str) -> pd.DataFrame:
        try:
            raw = self.c.klines(symbol, self.interval, 200)
        except Exception as e:
            logging.debug("klines error %s: %s", symbol, e)
            return pd.DataFrame()
        if not raw:
            return pd.DataFrame()

        cols = ["open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote", "count",
                "taker_buy_vol", "taker_buy_quote", "ignore"]
        df = pd.DataFrame(raw, columns=cols)
        df[["open", "high", "low", "close", "volume"]] = df[
            ["open", "high", "low", "close", "volume"]].astype(float)
        return add_indicators(df, self.atr_window)

    def _build_lot_step(self):
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
        tgt = bal * self.pos_pct * self.leverage
        step = self._lot_step[symbol]

        qty = self._round_qty(symbol, tgt / price)
        while qty * price < tgt * 0.97:
            qty += float(step)
        return max(qty, float(step))
