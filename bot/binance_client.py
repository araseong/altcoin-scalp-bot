from __future__ import annotations
import logging, math
from decimal import Decimal
from typing import List, Dict

from binance.client      import Client
from binance.exceptions  import BinanceAPIException


class BinanceFutures:
    """USDT‑M PERP 래퍼 (예외 로깅 포함)"""

    def __init__(self, api_key: str, api_secret: str, recv_window: int = 5000):
        self.client = Client(api_key, api_secret)
        self.client.RECV_WINDOW = recv_window

        self.alt_symbols: List[str] = self._load_usdt_m_altcoins()
        self._prec  : Dict[str, int]      = self._build_price_precision()
        self._step  : Dict[str, Decimal]  = self._build_lot_step()

    # ── 주문 ─────────────────────────────────
    def open_long(self, symbol: str, qty: float):
        try:
            return self.client.futures_create_order(
                symbol=symbol, side="BUY", type="MARKET", quantity=qty
            )
        except BinanceAPIException as e:
            logging.error("OPEN‑FAIL %s qty=%s | %s", symbol, qty, e)
            raise

    def close_position(self, symbol: str):
        pos = self.position_size(symbol)
        if pos == 0: return
        side = "SELL" if pos > 0 else "BUY"
        try:
            self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET", quantity=abs(pos)
            )
        except BinanceAPIException as e:
            logging.error("CLOSE‑FAIL %s | %s", symbol, e)
            raise

    def stop_market(self, symbol: str, side: str, qty: float, stop_price: float):
        try:
            return self.client.futures_create_order(
                symbol=symbol, side=side, type="STOP_MARKET",
                quantity=qty, stopPrice=stop_price, workingType="CONTRACT_PRICE"
            )
        except BinanceAPIException as e:
            logging.error("SL‑FAIL %s | %s", symbol, e)
            raise

    def cancel_order(self, symbol: str, order_id: int):
        try:
            self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
        except BinanceAPIException as e:
            logging.warning("CANCEL‑FAIL %s id=%s | %s", symbol, order_id, e)

    # ── 계좌·마켓 ────────────────────────────
    def balance_usdt(self) -> float:
        bal = self.client.futures_account_balance()
        usdt = next(b for b in bal if b["asset"] == "USDT")
        return float(usdt["balance"])

    def position_size(self, symbol: str) -> float:
        pos = self.client.futures_position_information(symbol=symbol)[0]
        return float(pos["positionAmt"])

    def set_leverage(self, symbol: str, lev: int):
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=lev)
        except BinanceAPIException as e:
            logging.warning("LEV‑FAIL %s %sx | %s", symbol, lev, e)

    def klines(self, symbol: str, interval: str, limit: int = 200):
        return self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)

    def top_alt_movers(self, limit: int | None = None) -> List[str]:
        tick = self.client.futures_ticker()
        tick = [t for t in tick if t["symbol"] in self.alt_symbols]
        movers = sorted(tick, key=lambda t: float(t["priceChangePercent"]), reverse=True)
        return [t["symbol"] for t in movers[:limit]] if limit else [t["symbol"] for t in movers]

    # ── 내부 util ────────────────────────────
    def _load_usdt_m_altcoins(self):
        info = self.client.futures_exchange_info()
        return [s["symbol"] for s in info["symbols"]
                if s["contractType"]=="PERPETUAL" and s["status"]=="TRADING"
                   and s["quoteAsset"]=="USDT"]

    def _build_price_precision(self):
        info = self.client.futures_exchange_info()
        prec = {}
        for s in info["symbols"]:
            if s["symbol"] in self.alt_symbols:
                tick = float(next(f for f in s["filters"]
                                  if f["filterType"]=="PRICE_FILTER")["tickSize"])
                prec[s["symbol"]] = int(round(-math.log10(tick), 0))
        return prec

    def _build_lot_step(self):
        info = self.client.futures_exchange_info()
        return {s["symbol"]: Decimal(next(f for f in s["filters"]
                                          if f["filterType"]=="LOT_SIZE")["stepSize"])
                for s in info["symbols"] if s["symbol"] in self.alt_symbols}
