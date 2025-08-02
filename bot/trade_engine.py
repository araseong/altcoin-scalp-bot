"""
TradeEngine –  Squeeze + VAH + RSI 전략 전용
────────────────────────────────────────────────────────
"""
from __future__ import annotations
import logging
from decimal import Decimal
from typing import Optional, Dict, List

import pandas as pd
from binance.exceptions import BinanceAPIException

from .binance_client import BinanceFutures
from .indicators      import rsi           # 재사용
from .strategy        import (
    squeeze_long_trigger,
    exit_rsi_reversal,
)

# ────────────────────────── 엔진 ──────────────────────────────────────

class TradeEngine:
    def __init__(self,
                 client: BinanceFutures,
                 interval: str,
                 leverage: int,
                 pos_pct: float,
                 sl_pct: float,
                 cfg: "configparser.ConfigParser",
                 ):
        self.c = client
        self.interval = interval
        self.leverage = leverage
        self.pos_pct  = pos_pct
        self.sl_pct   = sl_pct
        self.cfg      = cfg          # 전역 설정 보관

        self.open_symbol: Optional[str] = None
        self.stop_order_id: Optional[int] = None

        self._prec = self.c._prec
        self._lot_step = self._build_lot_step()

        logging.info("=== Engine init. leverage=%s pos_pct=%s sl_pct=%s ===",
                     leverage, pos_pct, sl_pct)

    # ─────────────────── 루프 ──────────────────────
    def run_once(self):
        try:
            if self.open_symbol:
                self._monitor_position()
            else:
                self._scan_and_enter()
        except BinanceAPIException as e:
            logging.error("BinanceAPIException: %s", e)

    # ─────────────────── 진입 스캔 ─────────────────
    def _scan_and_enter(self):
        t_cfg = self.cfg["tuning"]
        s_cfg = self.cfg["strategy"]

        for sym in self.c.top_alt_movers(limit=60):
            if not self._spread_ok(sym, 0.0004):
                continue

            df = self._load_klines(sym)
            if len(df) < s_cfg.getint("vah_lookback"):
                continue
            if not self._vol_ok(df, sym):
                continue
            if not squeeze_long_trigger(df, s_cfg):
                continue

            price = df.close.iloc[-1]
            qty   = self._position_size(price, sym)
            if qty <= 0:
                continue

            self.c.set_leverage(sym, self.leverage)
            self.c.open_long(sym, qty)

            sl_price = round(price * (1 - self.sl_pct), self._prec[sym])
            sl = self.c.stop_market(sym, "SELL", qty, sl_price)

            self.open_symbol  = sym
            self.stop_order_id = sl["orderId"]

            logging.info("OPEN %s qty=%.3f @ %.6f  SL=%.6f",
                         sym, qty, price, sl_price)
            break   # 동시 1포지션

    # ─────────────────── 포지션 관리 ──────────────
    def _monitor_position(self):
        df = self._load_klines(self.open_symbol)
        if not df.empty and exit_rsi_reversal(df):
            self._close_position("RSI reversal")

    def _close_position(self, reason: str):
        try:
            self.c.cancel_order(self.open_symbol, self.stop_order_id)
        except Exception:
            pass

        self.c.close_position(self.open_symbol)
        info = self.c.client.futures_position_information(
            symbol=self.open_symbol)[0]
        pnl  = float(info["unRealizedProfit"])
        price= float(info["markPrice"])

        logging.info("CLOSE %s pnl=%.4f exit=%.6f  %s",
                     self.open_symbol, pnl, price, reason)

        self.open_symbol, self.stop_order_id = None, None

    # ─────────────────── 헬퍼 ─────────────────────
    def _spread_ok(self, symbol: str, max_spread=0.0002) -> bool:
        try:
            bt = self.c.client.futures_ticker_bookTicker(symbol=symbol)
        except AttributeError:
            bt = self.c.client._request_futures_api(
                "get", "ticker/bookTicker", signed=False, params={"symbol": symbol})
        bid = float(bt.get("bidPrice", 0)); ask = float(bt.get("askPrice", 0))
        return bid and ask and (ask - bid) / bid < max_spread

    def _vol_ok(self, df: pd.DataFrame, symbol: str = None) -> bool:
        pct = df.close.pct_change().tail(30).abs()
        mu, sigma = pct.mean(), pct.std()
        ratio = sigma / (mu or 1e-8)
        min_vol = float(self.cfg["tuning"].get("min_vol_ratio", 1.0))
        if ratio < min_vol:
            logging.debug("skip %s low vol_ratio %.2f", symbol, ratio)
            return False
        return True

    def _load_klines(self, symbol: str) -> pd.DataFrame:
        raw = self.c.klines(symbol, self.interval, 200)
        if not raw:
            return pd.DataFrame()
        cols = ["open_time","open","high","low","close","volume",
                "close_time","quote","count",
                "taker_buy_vol","taker_buy_quote","ignore"]
        df = pd.DataFrame(raw, columns=cols).astype(float, errors="ignore")
        return df

    def _build_lot_step(self) -> Dict[str, Decimal]:
        step = {}
        info = self.c.client.futures_exchange_info()
        for s in info["symbols"]:
            f = next(f for f in s["filters"] if f["filterType"] == "LOT_SIZE")
            step[s["symbol"]] = Decimal(f["stepSize"])
        return step

    def _round_qty(self, symbol: str, qty: float) -> float:
        step = self._lot_step[symbol]
        return float((Decimal(qty) // step) * step)

    def _position_size(self, price: float, symbol: str) -> float:
        bal = self.c.balance_usdt()
        target = bal * self.pos_pct * self.leverage
        qty = self._round_qty(symbol, target / price)
        return max(qty, float(self._lot_step[symbol]))
