"""
보조지표 계산 전담 모듈
────────────────────────────────────────────────────────
"""
from __future__ import annotations
import numpy as np
import pandas as pd

# ────────────────────────── 공통 지표 ──────────────────────────────────

def add_squeeze_ind(df: pd.DataFrame,
                    bb_window: int = 20,
                    kc_window: int = 20,
                    kc_mult: float = 1.5) -> pd.DataFrame:
    """TTM‑Squeeze: Bollinger Bands & Keltner Channel + squeeze flag"""
    ma  = df.close.rolling(bb_window).mean()
    std = df.close.rolling(bb_window).std()

    df["bb_up"] = ma + 2 * std
    df["bb_dn"] = ma - 2 * std

    tr  = df[["high", "low", "close"]].apply(
        lambda x: max(x["high"] - x["low"],
                      abs(x["high"] - x["close"]),
                      abs(x["low"] - x["close"])), axis=1)
    atr = tr.rolling(kc_window).mean()

    df["kc_up"] = ma + kc_mult * atr
    df["kc_dn"] = ma - kc_mult * atr

    df["squeeze_on"] = (df.bb_dn > df.kc_dn) & (df.bb_up < df.kc_up)
    return df


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI(EMA 버전)"""
    delta = series.diff()
    up = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period).mean()
    dn = -delta.clip(upper=0).ewm(alpha=1/period, min_periods=period).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def volume_profile(df: pd.DataFrame,
                   bins: int = 24,
                   lookback: int = 120) -> float:
    """
    최근 lookback 봉 대상 간이 Volume‑Profile → VAH(70 % Value Area High) 반환
    """
    ref = df.tail(lookback)
    hist, edges = np.histogram(
        ref.close, bins=bins, weights=ref.volume)
    sort_idx = np.argsort(hist)[::-1]
    cum_vol = hist[sort_idx].cumsum()
    target  = ref.volume.sum() * 0.70
    mask    = cum_vol <= target
    vah_bin = sort_idx[mask][-1] if mask.any() else sort_idx[0]
    return float(edges[vah_bin + 1])
