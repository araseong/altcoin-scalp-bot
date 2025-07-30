import logging, math, pandas as pd
from decimal import Decimal
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

        # ---------- 심볼별 LOT_SIZE / tickSize 캐시 ----------
        self._prec = self.c._prec                           # tickSize 자리수
        self._lot_step = self._build_lot_step()             # stepSize(수량 단위)

        logging.info(
            "=== Engine init. leverage=%s pos_pct=%s sl_pct=%s ===",
            leverage,
            pos_pct,
            sl_pct,
        )

    # ======================================================
    # PUBLIC
    # ======================================================
    def run_once(self):
        try:
            if self.open_symbol:
                self._monitor_position()
            else:
                self._scan_and_enter()
        except BinanceAPIException as api_err:
            logging.error("BinanceAPIException: %s", api_err)

    # ======================================================
    # ENTRY & EXIT
    # ======================================================
    def _scan_and_enter(self):
        for sym in self.c.top_alt_movers(limit=40):
            df = self._load_klines(sym)
            if entry_signal(df):
                price = df.close.iloc[-1]

                qty = self._position_size(price, sym)
                if qty <= 0:
                    logging.warning("%s qty too small, skipped", sym)
                    continue

                self.c.set_leverage(sym, self.leverage)
                self.c.open_long(sym, qty)

                sl_price = round(
                    price * (1 - self.sl_pct), self._prec[sym]
                )
                sl = self.c.stop_market(sym, "SELL", qty, sl_price)

                self.open_symbol, self.stop_order_id = sym, sl["orderId"]

                logging.info(
                    "OPEN  %s qty=%s @ %.6f | SL=%.6f",
                    sym,
                    qty,
                    price,
                    sl_price,
                )
                break  # 동시 1포지션 규칙

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

    # ======================================================
    # HELPERS
    # ======================================================
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

    # ---------- 수량 계산 ----------
    def _build_lot_step(self):
        step = {}
        info = self.c.client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] in self.c.alt_symbols:
                filt = next(f for f in s["filters"] if f["filterType"] == "LOT_SIZE")
                step[s["symbol"]] = Decimal(filt["stepSize"])
        return step

    def _round_qty(self, symbol: str, qty: float) -> float:
        """LOT_SIZE step에 맞춰 버림."""
        step = self._lot_step[symbol]
        return float((Decimal(qty) // step) * step)

    def _position_size(self, price: float, symbol: str) -> float:
        balance = self.c.balance_usdt()
        notional = balance * self.pos_pct * self.leverage
        raw_qty = notional / price
        qty = self._round_qty(symbol, raw_qty)

        # 최소 수량이 0이면 0.001 등 stepSize 자체를 최소치로 사용
        min_qty = float(self._lot_step[symbol])
        return max(qty, min_qty)

