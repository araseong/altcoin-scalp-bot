import pandas as pd
import numpy as np


# ─────────────────────────────────────────────
# 1) EMA 3선 정배열 + +DI 우세
# ─────────────────────────────────────────────
def ema_vwap_di_signal(df: pd.DataFrame) -> bool:
    """EMA(9, 26, 50) 정배열 ‑ VWAP 위 ‑ +DI > ‑DI"""
    c = df.iloc[-1]
    ema_fast, ema_mid, ema_slow = c.ema_fast, c.ema_mid, c.ema_slow
    if not (ema_fast > ema_mid > ema_slow):
        return False
    if c.close < c.vwap:
        return False
    if c.di_plus <= c.di_minus:
        return False
    return True


# ─────────────────────────────────────────────
# 2) OBV‑Accum/Dist 지수 상관계수
#    BULL : ρ ≥ +0.8 & 양쪽 ↑
# ─────────────────────────────────────────────
def obv_acdist_trend(df: pd.DataFrame, window: int = 30) -> bool:
    obv_ema = df.obv_ema.tail(window)
    ad_ema  = df.acdist_ema.tail(window)
    if len(obv_ema) < window:
        return False

    rho = obv_ema.corr(ad_ema, method="spearman")
    grad_obv = np.polyfit(range(window), obv_ema.values, 1)[0]
    grad_ad  = np.polyfit(range(window), ad_ema.values, 1)[0]

    return rho >= 0.8 and grad_obv > 0 and grad_ad > 0


# ─────────────────────────────────────────────
# 3) 청산 : OBV & +DI 동시 하락
# ─────────────────────────────────────────────
def exit_signal(df: pd.DataFrame) -> bool:
    """직전 3봉 연속 OBV·+DI 하락 → 추세 약화"""
    s = df.tail(3)
    return s.obv.diff().lt(0).all() and s.di_plus.diff().lt(0).all()
