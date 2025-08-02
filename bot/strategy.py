"""
strategy.py
===========

• ttm_entry_signal :
    볼린저 밴드 ‑ 켈트너 채널 스퀴즈 해제 + VAH 돌파 + RSI 모멘텀
• exit_signal :
    진입 뒤 RSI 모멘텀이 꺾이거나 중간 볼린저 밴드 미만 하락 시 종료
"""

from __future__ import annotations
import logging
from typing import Tuple

import numpy as np
import pandas as pd
import ta                       # pandas‑ta가 아니라 *ta* 패키지
from scipy.stats import spearmanr


# ───────────────────────────────────────────────────────────
def _add_indicators(df: pd.DataFrame,
                    bb_len: int = 20,
                    kc_len: int = 20,
                    atr_mult: float = 1.5,
                    rsi_len: int = 14) -> pd.DataFrame:
    """필요 지표를 한꺼번에 계산해 df 에 컬럼 추가"""
    # --- 볼린저
    bb = ta.volatility.BollingerBands(df.close, window=bb_len, window_dev=2)
    df["bb_l"]   = bb.bollinger_lband()
    df["bb_m"]   = bb.bollinger_mavg()
    df["bb_h"]   = bb.bollinger_hband()
    df["bb_w"]   = df["bb_h"] - df["bb_l"]

    # --- 켈트너 : KC 상·하단 = EMA + / – ATR·mult
    atr = ta.volatility.AverageTrueRange(
        high=df.high, low=df.low, close=df.close, window=kc_len
    ).average_true_range()
    ema = df.close.ewm(span=kc_len, adjust=False).mean()
    df["kc_l"] = ema - atr_mult * atr
    df["kc_h"] = ema + atr_mult * atr
    df["kc_w"] = df["kc_h"] - df["kc_l"]

    # --- RSI
    df["rsi"] = ta.momentum.RSIIndicator(df.close, window=rsi_len).rsi()

    return df


def _volume_profile_vah(df: pd.DataFrame, lookback: int = 60) -> float:
    """
    최근 lookback 봉 기준 거래량 프로파일 상단(VAH) 추정값 반환.
    아주 정확한 Market‑Profile 로직 대신 간단한 ‘볼륨‑가중 히스토그램’으로 근사.
    """
    seg = df.tail(lookback)
    prices = seg.close.values
    vols   = seg.volume.values

    # 50개의 price bin → 각 bin 의 가중 거래량 합
    bins = np.linspace(prices.min(), prices.max(), 50)
    idx  = np.digitize(prices, bins)
    vol_in_bin = {}
    for i, v in zip(idx, vols):
        vol_in_bin[i] = vol_in_bin.get(i, 0) + v

    # 누적 거래량 70 % 지점의 upper‑bin 을 VAH 로 사용
    sorted_bins = sorted(vol_in_bin.items(), key=lambda x: x[0])
    total_vol   = sum(vol_in_bin.values())
    acc = 0
    for b, v in sorted_bins:
        acc += v
        if acc / total_vol >= 0.7:
            return bins[min(b, len(bins)-1)]
    # fallback
    return prices.max()


# ───────────────────────────────────────────────────────────
def ttm_entry_signal(df: pd.DataFrame,
                     cfg: dict | None = None) -> bool:
    """
    True  → Long 진입
    구성 요소
    1) 이전 봉까지 스퀴즈(on), 현재 봉 스퀴즈 해제(off)
    2) 현재 종가 > VAH  (거래량 중심부 돌파)
    3) RSI ≥ 65
    """
    if len(df) < 100:
        return False

    # 지표 붙이기
    df = _add_indicators(df)

    # ── 스퀴즈 판단
    prev_in_squeeze = df.bb_w.iloc[-2] < df.kc_w.iloc[-2]
    now_out_of_sqz  = df.bb_w.iloc[-1] > df.kc_w.iloc[-1]
    squeeze_release = prev_in_squeeze and now_out_of_sqz

    # ── VAH 돌파
    vah  = _volume_profile_vah(df, lookback=60)
    vah_break = df.close.iloc[-1] > vah

    # ── RSI 모멘텀
    rsi_ok = df.rsi.iloc[-1] >= 65

    ok = squeeze_release and vah_break and rsi_ok
    logging.debug(
        "sig‑chk sqz=%s vah=%.5f cls=%.5f rsi=%.1f ⇒ %s",
        squeeze_release,
        vah,
        df.close.iloc[-1],
        df.rsi.iloc[-1],
        ok,
    )
    return ok


# ───────────────────────────────────────────────────────────
def exit_signal(df: pd.DataFrame,
                rsi_hi: float = 70,
                rsi_fall: float = 60) -> bool:
    """
    1) 최근 3봉 내 RSI 가 rsi_hi 이상이었던 적이 있고
    2) 방금 전‑>현재 봉에서 rsi_fall 아래로 하락하면 True
    """
    if len(df) < 5 or "rsi" not in df.columns:
        df = _add_indicators(df)

    rsi = df.rsi
    was_high = (rsi.iloc[-3:] >= rsi_hi).any()
    fell     = rsi.iloc[-2] >= rsi_fall and rsi.iloc[-1] < rsi_fall
    return bool(was_high and fell)
