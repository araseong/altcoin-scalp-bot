import logging
from decimal import Decimal
from typing import Optional, List, Dict

import pandas as pd
from binance.exceptions import BinanceAPIException

from .binance_client import BinanceFutures
from .indicators import add_indicators
from .strategy import (
    ema_vwap_di_signal,
    obv_acdist_trend,
    exit_signal,
)


class TradeEngine:
    """EMA+VWAP+DI & OBV/AccDist 동조 추세로만 Long 진입"""

    def __init__(self, client: BinanceFutures, interval: str,
                 leverage: int, pos_pct: float, sl_pct: float):
        self.c           = client
        self.interval    = interval
        self.leverage    = leverage
        self.pos_pct     = pos_pct      # 자본의 30 %
        self.sl_pct      = sl_pct       # 2 %
        self.open_symbol = None
        self.stop_order_id = None
        self.tp_orders: Dict[str, List[int]] = {}

        self._prec      = self.c._prec
        self._lot_step  = self._build_lot_step()
        self.tuning: dict = {}

        logging.info("=== Engine init. leverage=%s pos_pct=%s sl_pct=%s ===",
                     leverage, pos_pct, sl_pct)

    # ────────────────────────── 루프
    def run_once(self):
        try:
            if self.open_symbol:
                self._monitor_position()
            else:
                self._scan_and_enter()
        except BinanceAPIException as e:
            logging.error("BinanceAPIException: %s", e)

    # ────────────────────────── 진입
    def _scan_and_enter(self):
        self.iter = getattr(self, "iter", 0) + 1
        logging.debug("LOOP #%s  %s", self.iter, pd.Timestamp.utcnow())

        movers = self.c.top_alt_movers(limit=60)
        for sym in movers:
            if not self._spread_ok(sym, 0.0004):
                logging.debug("%s spread FAIL", sym);   continue

            df = self._load_klines(sym)
            if df.empty or len(df) < 60:
                continue

            if not self._vol_ok(df, sym):
                continue

            cond1 = ema_vwap_di_signal(df)
            cond2 = obv_acdist_trend(df, window=30)
            logging.debug("%s cond1=%s cond2=%s", sym, cond1, cond2)
            if not (cond1 and cond2):
                continue

            # -------- 진입 --------
            price = df.close.iloc[-1]
            qty   = self._position_size(price, sym)
            if qty <= 0:
                continue

            self.c.set_leverage(sym, self.leverage)
            self.c.open_long(sym, qty)

            sl_price = round(price * (1 - self.sl_pct), self._prec[sym])
            sl = self.c.stop_market(sym, "SELL", qty, sl_price)

            self.open_symbol   = sym
            self.stop_order_id = sl["orderId"]
            logging.info("OPEN  %s qty=%.3f @ %.6f SL=%.6f",
                         sym, qty, price, sl_price)
            break

    # ────────────────────────── 포지션 모니터
    def _monitor_position(self):
        df = self._load_klines(self.open_symbol)
        if not df.empty and exit_signal(df):
            self._close_position("OBV & +DI fall")

    def _close_position(self, reason: str):
        for oid in self.tp_orders.get(self.open_symbol, []):
            try: self.c.cancel_order(self.open_symbol, oid)
            except Exception: pass
        try: self.c.cancel_order(self.open_symbol, self.stop_order_id)
        except Exception: pass

        self.c.close_position(self.open_symbol)

        info = self.c.client.futures_position_information(
            symbol=self.open_symbol)[0]
        pnl = float(info["unRealizedProfit"])
        exit_price = float(info["markPrice"])
        logging.info("CLOSE %s pnl=%.4f exit=%.6f %s",
                     self.open_symbol, pnl, exit_price, reason)

        self.tp_orders.pop(self.open_symbol, None)
        self.open_symbol, self.stop_order_id = None, None

    # ────────────────────────── 헬퍼 (spread / vol / klines 등)
    def _spread_ok(self, symbol: str, max_spread=0.0004) -> bool:
        try:
            bt = self.c.client.futures_ticker_bookTicker(symbol=symbol)
        except AttributeError:
            bt = self.c.client._request_futures_api(
                "get", "ticker/bookTicker", signed=False, params={"symbol": symbol}
            )
        bid, ask = float(bt.get("bidPrice", 0)), float(bt.get("askPrice", 0))
        return bid > 0 and (ask - bid) / bid < max_spread

    def _vol_ok(self, df: pd.DataFrame, sym: str) -> bool:
        pct = df.close.pct_change().tail(30).abs()
        vol_ratio = pct.std() / (pct.mean() or 1e-8)
        min_vol = float(self.tuning.get("min_vol_ratio", 1.0))
        if vol_ratio < min_vol:
            logging.debug("%s vol_ratio %.2f FAIL", sym, vol_ratio)
            return False
        return True

    def _load_klines(self, symbol: str) -> pd.DataFrame:
        try:
            raw = self.c.klines(symbol, self.interval, 200)
        except Exception:
            return pd.DataFrame()
        if not raw:
            return pd.DataFrame()

        cols = ["open_time","open","high","low","close","volume",
                "close_time","quote","count",
                "taker_buy_vol","taker_buy_quote","ignore"]
        df = pd.DataFrame(raw, columns=cols)
        df[["open","high","low","close","volume"]] = df[
            ["open","high","low","close","volume"]].astype(float)
        return add_indicators(df)

    def _build_lot_step(self):
        info = self.c.client.futures_exchange_info()
        return {
            s["symbol"]: Decimal(next(f for f in s["filters"]
                                      if f["filterType"]=="LOT_SIZE")["stepSize"])
            for s in info["symbols"] if s["symbol"] in self.c.alt_symbols
        }

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
