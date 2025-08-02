# bot/strategy.py
# ──────────────────────────────────────────────────────────
import logging
from typing import Tuple

import numpy as np
import pandas as pd
from scipy.stats import spearmanr                       # 상관계수
import ta                                               # ta‑lib 래퍼

# ────────────────────────── 보조 계산 함수 ----------------
def _bollinger(df: pd.DataFrame, win: int, mult: float) -> Tuple[pd.Series, pd.Series]:
    ma = df.close.rolling(win).mean()
    std = df.close.rolling(win).std(ddof=0)
    upper = ma + mult * std
    lower = ma - mult * std
    return upper, lower


def _keltner(df: pd.DataFrame, win: int, mult: float) -> Tuple[pd.Series, pd.Series]:
    atr = ta.volatility.AverageTrueRange(
        df.high, df.low, df.close, window=win, fillna=False
    ).average_true_range()
    ma = df.close.rolling(win).mean()
    upper = ma + mult * atr
    lower = ma - mult * atr
    return upper, lower


def ttm_squeeze(df: pd.DataFrame,
                kel_mult: float = 1.5,
                bb_mult: float = 2.0,
                win: int = 20) -> pd.Series:
    """
    TTM Squeeze: BB 폭이 KC 안쪽에 있을 때 True.
    True → False 전환이 스퀴즈 해제.
    """
    bb_up, bb_lo = _bollinger(df, win, bb_mult)
    kc_up, kc_lo = _keltner(df, win, kel_mult)
    squeeze_on = (bb_up < kc_up) & (bb_lo > kc_lo)
    return squeeze_on


def price_breaks_vah(df: pd.DataFrame,
                     look: int = 120,
                     prc_buffer: float = 0.001) -> bool:
    """
    거래량 프로파일의 VAH(Value Area High) 돌파 여부.
    """
    # 거래량 가중가격 히스토그램
    sub = df.tail(look)
    prices = (sub.high + sub.low) / 2
    weights = sub.volume
    hist, bins = np.histogram(prices, bins="auto", weights=weights)
    idx_max = hist.argmax()
    vah = bins[idx_max + 1]          # 상단 경계

    close = sub.close.iloc[-1]
    return close > vah * (1 + prc_buffer)


def rsi_confirm(df: pd.DataFrame, thr: int = 65, win: int = 14) -> bool:
    """
    RSI가 임계값 이상으로 급등했는지 확인
    """
    rsi = ta.momentum.RSIIndicator(df.close, window=win).rsi()
    return rsi.iloc[-1] >= thr


def obv_acdist_trend(df: pd.DataFrame, win: int = 60, rho_th: float = 0.7) -> bool:
    """
    OBV와 Acc/Dist의 스피어만 상관계수 ↑ 추세 동행 확인
    """
    obv = ta.volume.OnBalanceVolumeIndicator(df.close, df.volume).on_balance_volume()
    ad  = ta.volume.AccDistIndexIndicator(df.high, df.low, df.close, df.volume).acc_dist_index()

    obv_win, ad_win = obv.tail(win), ad.tail(win)
    if obv_win.isna().any() or ad_win.isna().any():
        return False

    rho, _ = spearmanr(obv_win, ad_win)
    return rho >= rho_th


# ────────────────────────── 최종 진입 시그널 ----------------
def ttm_entry_signal(df: pd.DataFrame,
                     kel_mult: float = 1.5,
                     bb_mult: float = 2.0,
                     rsi_thr: int = 65,
                     vah_look: int = 120) -> bool:
    """
    (1) 스퀴즈 해제 + (2) VAH 돌파 + (3) RSI 모멘텀
    모두 만족하면 True
    """
    # ① 스퀴즈 해제 여부
    sq = ttm_squeeze(df, kel_mult, bb_mult)
    squeeze_released = sq.iloc[-2] and not sq.iloc[-1]

    # ② VAH 돌파
    vah_ok = price_breaks_vah(df, look=vah_look)

    # ③ RSI 모멘텀
    rsi_ok = rsi_confirm(df, thr=rsi_thr)

    # ── DEBUG 로그 추가 ───────────────────────────────
    logging.debug(
        "%s squeezeRel=%s vah=%s rsi=%s",
        getattr(df, "symbol", "UNKNOWN"),   # TradeEngine에서 DataFrame에 symbol 속성 추가해둠
        squeeze_released, vah_ok, rsi_ok
    )
    # ────────────────────────────────────────────────

    return squeeze_released and vah_ok and rsi_ok
