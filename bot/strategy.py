import numpy as np
import pandas as pd


# ── 1) EMA 정배열 + +DI 우세 + VWAP 위  ──
def ema_vwap_di_signal(df: pd.DataFrame) -> bool:
    c = df.iloc[-1]
    if not (c.ema_fast > c.ema_mid > c.ema_slow):
        return False
    if c.close < c.vwap:                      # 종가가 VWAP 아래면 패스
        return False
    if c.di_plus <= c.di_minus:              # +DI 가 –DI 보다 커야
        return False
    return True


# ── 2) OBV·A/D 60 봉 동조 추세 ──────────────
def obv_acdist_trend(df: pd.DataFrame,
                     win: int = 60,
                     rho_th: float = 0.7) -> bool:
    obv = df.obv.ewm(span=9, adjust=False).mean().tail(win)
    ad  = df.acdist.ewm(span=9, adjust=False).mean().tail(win)
    if len(obv) < win:
        return False

    rho = obv.corr(ad, method="spearman")         # 상관계수
    beta_obv = np.polyfit(range(win), obv.values, 1)[0]
    beta_ad  = np.polyfit(range(win), ad.values, 1)[0]

    return rho >= rho_th and beta_obv > 0 and beta_ad > 0


# ── 3) 청산 조건 ─────────────────────────────
def exit_signal(df: pd.DataFrame) -> bool:
    s = df.tail(3)
    return s.obv.diff().lt(0).all() and s.di_plus.diff().lt(0).all()
