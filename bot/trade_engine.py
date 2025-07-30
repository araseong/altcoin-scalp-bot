import logging, math, pandas as pd
from decimal import Decimal
from typing import Optional

from binance.exceptions import BinanceAPIException

from .binance_client import BinanceFutures
from .indicators import add_indicators
from .strategy import entry_signal, exit_signal
from statistics import stdev, mean



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
        for sym in self.c.top_alt_movers(limit=60):        # 24 h 변동률 상위 60개
            # ① 변동성 계산 (최근 30분)
            df = self._load_klines(sym)
            closes = df.close.tail(30).tolist()
            pct_moves = [abs(closes[i]/closes[i-1]-1) for i in range(1, len(closes))]
            vol_ratio = (stdev(pct_moves) / mean(pct_moves)) if mean(pct_moves) else 0

            # ② 변동성 필터
            min_vol = float(self.tuning.get("min_vol_ratio", 3))
            if vol_ratio < min_vol:
                continue

            # ③ 기존 엔트리 조건
            if entry_signal(df):
                price = df.close.iloc[-1]
                qty   = self._position_size(price, sym)
                if qty <= 0:
                    continue

                self.c.set_leverage(sym, self.leverage)
                self.c.open_long(sym, qty)

                sl_price = round(price * (1 - self.sl_pct), self._prec[sym])
                sl_ord   = self.c.stop_market(sym, "SELL", qty, sl_price)

                self.open_symbol, self.stop_order_id = sym, sl_ord["orderId"]
                logging.info("OPEN  %s qty=%s @ %.6f | SL=%.6f", sym, qty, price, sl_price)
                break   # 동시 1포지션
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
    """
    잔고 * pos_pct * leverage  만큼 실제 포지션 notional 을 맞춰 준다.
    stepSize 때문에 절삭된 뒤에도 목표 notional 의 97% 이상이 되도록
    수량을 1 step 씩 올려서 재보정한다.
    """
    bal        = self.c.balance_usdt()
    tgt_notional = bal * self.pos_pct * self.leverage        # 30% * 10배
    step       = self._lot_step[symbol]
    qty        = self._round_qty(symbol, tgt_notional / price)

    # 절삭 때문에 부족하면 step 단위로 늘려서 보정
    while qty * price < tgt_notional * 0.97:                 # 최대 3 % 오차 허용
        qty += float(step)
    return qty

