import logging, math, pandas as pd
from typing import Optional
from binance.exceptions import BinanceAPIException

from .binance_client import BinanceFutures
from .indicators import add_indicators
from .strategy import entry_signal, exit_signal

class TradeEngine:
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
        self.interval, self.leverage = interval, leverage
        self.pos_pct, self.sl_pct = pos_pct, sl_pct
        self.atr_window = atr_window

        self.open_symbol: Optional[str] = None
        self.stop_order_id: Optional[int] = None

        logging.info("=== Engine init. leverage=%s pos_pct=%s sl_pct=%s ===",
                     leverage, pos_pct, sl_pct)

    # ---------- public ----------
    def run_once(self):
        try:
            if self.open_symbol:
                self._monitor_position()
            else:
                self._scan_and_enter()
        except BinanceAPIException as api_err:
            logging.error("BinanceAPIException: %s", api_err)

    # ---------- private ----------
    def _scan_and_enter(self):
        for sym in self.c.top_alt_movers(limit=40):
            df = self._load_klines(sym)
            if entry_signal(df):
                price = df.close.iloc[-1]
                qty   = self._position_size(price)
                self.c.set_leverage(sym, self.leverage)
                self.c.open_long(sym, qty)

                sl_price = round(
                    price * (1 - self.sl_pct), self.c.price_precision(sym)
                )
                sl = self.c.stop_market(sym, "SELL", qty, sl_price)

                self.open_symbol, self.stop_order_id = sym, sl["orderId"]

                logging.info(
                    f"OPEN  {sym} qty={qty} @ {price:.4f} | SL={sl_price:.4f}"
                )
                break  # 동시 1포지션

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
        logging.info(f"CLOSE {self.open_symbol}  {reason}")
        self.open_symbol, self.stop_order_id = None, None

    # ---------- helpers ----------
    def _load_klines(self, symbol: str) -> pd.DataFrame:
        raw = self.c.klines(symbol, self.interval, 200)
        cols = [
            "open_time","open","high","low","close","volume",
            "close_time","quote","count","taker_buy_vol",
            "taker_buy_quote","ignore"
        ]
        df = pd.DataFrame(raw, columns=cols)
        df[["open","high","low","close","volume"]] = df[
            ["open","high","low","close","volume"]
        ].astype(float)
        df = add_indicators(df, self.atr_window)
        return df

    def _position_size(self, price: float) -> float:
        bal = self.c.balance_usdt()
        notional = bal * self.pos_pct * self.leverage
        qty = round(notional / price, 3)
        return max(qty, 0.001)
