from __future__ import annotations

import logging
import math
from decimal import Decimal
from typing import List, Dict

from binance.client import Client
from binance.exceptions import BinanceAPIException


class BinanceFutures:
    """USDT‑M PERP 전용 래퍼"""

    def __init__(self, api_key: str, api_secret: str, recv_window: int = 5000):
        # ① 클라이언트 생성
        self.client = Client(api_key, api_secret)
        self.client.RECV_WINDOW = recv_window

        # ② 거래가능 심볼 목록 & 가격·수량 자리수
        self.alt_symbols: List[str] = self._load_usdt_m_altcoins()
        self._prec: Dict[str, int] = self._build_price_precision()
        self._step: Dict[str, Decimal] = self._build_lot_step()

    # ─────────────────────────────────────────────
    # PUBLIC API (봇에서 호출)
    # ─────────────────────────────────────────────
    # ---------- 주문 ----------
    def open_long(self, symbol: str, qty: float):
        """Market BUY"""
        try:
            return self.client.futures_create_order(
                symbol=symbol, side="BUY", type="MARKET", quantity=qty
            )
        except BinanceAPIException as e:
            logging.error("OPEN‑FAIL %s qty=%s | %s", symbol, qty, e)
            raise

    def close_position(self, symbol: str):
        """시장가 반대 주문으로 즉시 청산 (Long→SELL, Short→BUY)"""
        try:
            pos = self.position_size(symbol)
            if pos > 0:
                side = "SELL"
            elif pos < 0:
                side = "BUY"
            else:
                return
            self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET", quantity=abs(pos)
            )
        except BinanceAPIException as e:
            logging.error("CLOSE‑FAIL %s | %s", symbol, e)
            raise

    def stop_market(self, symbol: str, side: str, qty: float, stop_price: float):
        """SL 주문 – STOP_MARKET"""
        try:
            return self.client.futures_create_order(
                symbol=symbol, side=side, type="STOP_MARKET",
                quantity=qty, stopPrice=stop_price,
                workingType="CONTRACT_PRICE"
            )
        except BinanceAPIException as e:
            logging.error("SL‑FAIL %s | %s", symbol, e)
            raise

    def cancel_order(self, symbol: str, order_id: int):
        try:
            self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
        except BinanceAPIException as e:
            logging.warning("CANCEL‑FAIL %s id=%s | %s", symbol, order_id, e)

    # ---------- 계좌·마켓 ----------
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
            logging.warning("LEV‑FAIL %s %s× | %s", symbol, lev, e)

    def klines(self, symbol: str, interval: str, limit: int = 200):
        return self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)

    def top_alt_movers(self, limit: int | None = None) -> List[str]:
        """24 h 변동률 상위 알트 리스트"""
        tickers = self.client.futures_ticker()
        # USDT‑M 알트만 필터
        tickers = [t for t in tickers if t["symbol"] in self.alt_symbols]
        # 등락률 내림차순
        movers = sorted(tickers, key=lambda t: float(t["priceChangePercent"]), reverse=True)
        return [t["symbol"] for t in movers[:limit]] if limit else [t["symbol"] for t in movers]

    # ─────────────────────────────────────────────
    # PRIVATE
    # ─────────────────────────────────────────────
    def _load_usdt_m_altcoins(self) -> List[str]:
        info = self.client.futures_exchange_info()
        return [
            s["symbol"] for s in info["symbols"]
            if s["contractType"] == "PERPETUAL"
            and s["status"] == "TRADING"
            and s["quoteAsset"] == "USDT"
            and not s["symbol"].endswith("USDC")     # 거버넌스/스테이블 제외
        ]

    def _build_price_precision(self) -> Dict[str, int]:
        prec = {}
        info = self.client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] in self.alt_symbols:
                filt = next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
                tick = float(filt["tickSize"])
                prec[s["symbol"]] = int(round(-math.log10(tick), 0))
        return prec

    def _build_lot_step(self) -> Dict[str, Decimal]:
        step = {}
        info = self.client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] in self.alt_symbols:
                f = next(f for f in s["filters"] if f["filterType"] == "LOT_SIZE")
                step[s["symbol"]] = Decimal(f["stepSize"])
        return step
