import os
import json
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor

MODEL_VERSION = "v4_unified_ensemble_fft"


def _build_ml_forecast(df: pd.DataFrame, years_ahead: int = 5):
    """
    Ensamble ML (Random Forest + Gradient Boosting + Ciclos Armonicos FFT)
    entrenado con el historico real 1928 -> 2026 para proyectar fluctuaciones
    organicas y realistas de 2027 a 2031.
    """
    df_tmp = df.copy()
    df_tmp["dt"] = pd.to_datetime(df_tmp["date"])
    df_tmp["ym"] = df_tmp["dt"].dt.to_period("M")

    monthly = (
        df_tmp.groupby("ym", as_index=False)
        .agg(
            date=("date", "last"),
            close=("close", "last"),
            drawdown=("drawdown_pct", "min"),
        )
        .sort_values("date")
        .reset_index(drop=True)
    )

    monthly["dt"] = pd.to_datetime(monthly["date"])
    monthly["month"] = monthly["dt"].dt.month
    monthly["log_close"] = np.log(monthly["close"].clip(lower=1.0))
    monthly["log_ret"] = monthly["log_close"].diff().fillna(0.0)

    # 1. Ciclos armonicos (FFT) aprendidos del historico 1928 -> 2026
    t_hist = np.arange(len(monthly), dtype=float)
    ridge_trend = Ridge(alpha=1.0)
    ridge_trend.fit(t_hist.reshape(-1, 1), monthly["log_close"].values)
    detrended = monthly["log_close"].values - ridge_trend.predict(t_hist.reshape(-1, 1))

    n_obs = len(detrended)
    fft_vals = np.fft.rfft(detrended)
    freqs = np.fft.rfftfreq(n_obs, d=1.0)
    harmonics = []
    for k in range(1, len(freqs)):
        period = 1.0 / freqs[k] if freqs[k] > 0 else 0.0
        if 14.0 <= period <= 130.0:
            amp = (2.0 / n_obs) * np.abs(fft_vals[k])
            phase = np.angle(fft_vals[k])
            harmonics.append((amp, freqs[k], phase))
    harmonics.sort(key=lambda x: x[0], reverse=True)
    top_harmonics = harmonics[:6]

    def eval_cycle(t_val: float) -> float:
        return sum(amp * np.cos(2.0 * np.pi * freq * t_val + phase) for amp, freq, phase in top_harmonics)

    # 2. Features autorregresivos y estacionales
    for lag in [1, 2, 3, 6, 12]:
        monthly[f"ret_lag_{lag}"] = monthly["log_ret"].shift(lag)
    monthly["roll_mean_3"] = monthly["log_ret"].rolling(3).mean().shift(1)
    monthly["roll_mean_12"] = monthly["log_ret"].rolling(12).mean().shift(1)
    monthly["roll_std_6"] = monthly["log_ret"].rolling(6).std().shift(1)
    monthly["dd_12m"] = ((monthly["close"] / monthly["close"].rolling(12, min_periods=1).max()) - 1.0).shift(1)
    monthly["sin_m"] = np.sin(2.0 * np.pi * monthly["month"] / 12.0)
    monthly["cos_m"] = np.cos(2.0 * np.pi * monthly["month"] / 12.0)

    feature_cols = [
        "ret_lag_1", "ret_lag_2", "ret_lag_3", "ret_lag_6", "ret_lag_12",
        "roll_mean_3", "roll_mean_12", "roll_std_6", "dd_12m", "sin_m", "cos_m"
    ]

    train_df = monthly.dropna(subset=feature_cols + ["log_ret"]).copy()
    X_train = train_df[feature_cols]
    y_train = train_df["log_ret"]

    rf = RandomForestRegressor(n_estimators=150, max_depth=6, min_samples_leaf=8, random_state=42)
    gbr = GradientBoostingRegressor(n_estimators=120, max_depth=3, learning_rate=0.04, random_state=42)
    rf.fit(X_train, y_train)
    gbr.fit(X_train, y_train)

    in_sample_pred = 0.55 * rf.predict(X_train) + 0.45 * gbr.predict(X_train)
    model_residuals = y_train.values - in_sample_pred
    q_low, q_high = np.percentile(model_residuals, [14, 86])
    clipped_residuals = np.clip(model_residuals, q_low, q_high)
    residual_std = float(np.std(model_residuals))

    monthly_seasonality = monthly.groupby("month")["log_ret"].mean().to_dict()
    historical_drift = float(monthly["log_ret"].mean())

    horizon_months = years_ahead * 12
    last_dt = pd.to_datetime(monthly["date"].iloc[-1])
    last_t = float(len(monthly) - 1)
    last_close = float(monthly["close"].iloc[-1])
    running_peak = float(df_tmp["rolling_peak"].iloc[-1])

    ret_hist = list(monthly["log_ret"].iloc[-15:].values)
    close_hist = list(monthly["close"].iloc[-15:].values)

    cycle_offset = eval_cycle(last_t)
    prev_cycle_val = 0.0

    seed_val = int(abs(last_close * 100)) % 100000
    local_rng = np.random.RandomState(seed_val)

    forecast_points = []
    prev_close = last_close

    for step in range(1, horizon_months + 1):
        fut_dt = last_dt + pd.DateOffset(months=step)
        fut_month = int(fut_dt.month)
        fut_t = last_t + step

        peak_12m = max(close_hist[-12:])
        dd_12m_val = (close_hist[-1] / peak_12m) - 1.0

        x_step = pd.DataFrame([{
            "ret_lag_1": ret_hist[-1],
            "ret_lag_2": ret_hist[-2],
            "ret_lag_3": ret_hist[-3],
            "ret_lag_6": ret_hist[-6],
            "ret_lag_12": ret_hist[-12],
            "roll_mean_3": float(np.mean(ret_hist[-3:])),
            "roll_mean_12": float(np.mean(ret_hist[-12:])),
            "roll_std_6": float(np.std(ret_hist[-6:])),
            "dd_12m": dd_12m_val,
            "sin_m": np.sin(2.0 * np.pi * fut_month / 12.0),
            "cos_m": np.cos(2.0 * np.pi * fut_month / 12.0),
        }])

        ml_ret = float(0.55 * rf.predict(x_step)[0] + 0.45 * gbr.predict(x_step)[0])
        seas_ret = float(monthly_seasonality.get(fut_month, historical_drift))

        curr_cycle_val = (eval_cycle(fut_t) - cycle_offset) * 0.24
        cycle_delta = curr_cycle_val - prev_cycle_val
        prev_cycle_val = curr_cycle_val

        sampled_shock = float(local_rng.choice(clipped_residuals)) * 0.62

        step_log_ret = (
            0.45 * ml_ret
            + 0.25 * seas_ret
            + 0.30 * historical_drift
            + cycle_delta
            + sampled_shock
        )
        step_log_ret = float(np.clip(step_log_ret, -0.028, 0.054))

        close_val = float(prev_close * np.exp(step_log_ret))
        ret_hist.append(step_log_ret)
        close_hist.append(close_val)

        band_mult = np.exp(residual_std * np.sqrt(step) * 0.75)
        upper_val = close_val * band_mult
        lower_val = close_val / band_mult

        running_peak = max(running_peak, close_val)
        crit_thresh = running_peak * 0.88
        dd_pct = ((close_val - running_peak) / running_peak) * 100.0
        step_change_pct = ((close_val - prev_close) / prev_close) * 100.0
        prev_close = close_val

        forecast_points.append({
            "date": fut_dt.strftime("%Y-%m-%d"),
            "year": int(fut_dt.year),
            "close": round(close_val, 2),
            "upper_band": round(upper_val, 2),
            "lower_band": round(lower_val, 2),
            "step_return_pct": round(step_change_pct, 2),
            "rolling_peak": round(running_peak, 2),
            "critical_threshold": round(crit_thresh, 2),
            "drawdown_pct": round(dd_pct, 2),
            "is_prediction": True,
        })

    forecast_df = pd.DataFrame(forecast_points)
    annual_summary = (
        forecast_df.groupby("year", as_index=False)
        .agg(
            close_end=("close", "last"),
            optimistic_end=("upper_band", "last"),
            bearish_end=("lower_band", "last"),
        )
        .to_dict(orient="records")
    )

    return forecast_points, annual_summary


def _build_chronological_playback_timeline(df: pd.DataFrame, forecast_points: list):
    df_tmp = df.copy()
    df_tmp["dt"] = pd.to_datetime(df_tmp["date"])
    df_tmp["ym"] = df_tmp["dt"].dt.to_period("M")
    df_tmp["yq"] = df_tmp["dt"].dt.to_period("Q")

    p_1928_1932 = df_tmp[df_tmp["year"] <= 1932].groupby("ym", as_index=False).last()
    p_1933_2019 = df_tmp[(df_tmp["year"] >= 1933) & (df_tmp["year"] <= 2019)].groupby("yq", as_index=False).last()
    p_2020_2026 = df_tmp[df_tmp["year"] >= 2020].groupby("ym", as_index=False).last()

    sampled = pd.concat([p_1928_1932, p_1933_2019, p_2020_2026], ignore_index=True)
    sampled = sampled.sort_values("dt").drop_duplicates(subset=["date"]).reset_index(drop=True)

    last_exact = df_tmp.iloc[-1]
    if sampled["date"].iloc[-1] != last_exact["date"]:
        sampled = pd.concat([sampled, pd.DataFrame([last_exact])], ignore_index=True)

    timeline = []
    running_peak = 0.0
    prev_close = None

    for _, row in sampled.iterrows():
        c = float(row["close"])
        running_peak = max(running_peak, c)
        crit_thresh = running_peak * 0.88
        dd_pct = ((c - running_peak) / running_peak) * 100.0
        ret_pct = 0.0 if prev_close is None else ((c - prev_close) / prev_close) * 100.0
        prev_close = c

        timeline.append({
            "date": str(row["date"]),
            "year": int(row["year"]),
            "close": round(c, 2),
            "upper_band": None,
            "lower_band": None,
            "step_return_pct": round(ret_pct, 2),
            "rolling_peak": round(running_peak, 2),
            "critical_threshold": round(crit_thresh, 2),
            "drawdown_pct": round(dd_pct, 2),
            "is_prediction": False,
        })

    for fp in forecast_points:
        c = float(fp["close"])
        running_peak = max(running_peak, c)
        crit_thresh = running_peak * 0.88
        dd_pct = ((c - running_peak) / running_peak) * 100.0
        ret_pct = ((c - prev_close) / prev_close) * 100.0 if prev_close else 0.0
        prev_close = c

        timeline.append({
            "date": fp["date"],
            "year": fp["year"],
            "close": round(c, 2),
            "upper_band": fp["upper_band"],
            "lower_band": fp["lower_band"],
            "step_return_pct": round(ret_pct, 2),
            "rolling_peak": round(running_peak, 2),
            "critical_threshold": round(crit_thresh, 2),
            "drawdown_pct": round(dd_pct, 2),
            "is_prediction": True,
        })

    return timeline


def predict_and_build_gold_layer(ctx, task):
    """
    CAPA ORO + ML: Entrena modelo ensamble de Machine Learning (1928 -> 2026),
    proyecta fluctuaciones organicas (2027 -> 2031) y construye la serie temporal.
    """
    silver_csv = os.path.join(ctx["data_dir"], "silver", "wallstreet_silver.csv")
    bronze_meta_path = os.path.join(ctx["data_dir"], "bronze", "bronze_metadata.json")
    gold_dir = os.path.join(ctx["data_dir"], "gold")
    os.makedirs(gold_dir, exist_ok=True)

    if not os.path.exists(silver_csv):
        raise FileNotFoundError("Capa Plata no encontrada.")

    df = pd.read_csv(silver_csv)
    source_api = "Yahoo Finance API (^GSPC - S&P 500)"
    if os.path.exists(bronze_meta_path):
        with open(bronze_meta_path, "r", encoding="utf-8") as f:
            source_api = json.load(f).get("source_api", source_api)

    task.log("Entrenando ensamble ML (Random Forest + Gradient Boosting + Ciclos FFT) 1928 -> 2026...")
    forecast_points, annual_summary = _build_ml_forecast(df, years_ahead=5)
    timeline = _build_chronological_playback_timeline(df, forecast_points)

    pd.DataFrame(forecast_points).to_csv(
        os.path.join(gold_dir, "wallstreet_predictions_2027_2031.csv"), index=False
    )

    gold_payload = {
        "model_version": MODEL_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_api": source_api,
        "raw_total_days": int(len(df)),
        "date_min": str(df["date"].min()),
        "date_max": str(df["date"].max()),
        "playback_series": timeline,
        "annual_forecast": annual_summary,
    }

    with open(os.path.join(gold_dir, "gold_dashboard_payload.json"), "w", encoding="utf-8") as f:
        json.dump(gold_payload, f, indent=2, ensure_ascii=False)

    task.log(
        f"Capa Oro publicada: {len(timeline)} puntos cronologicos listos "
        f"(1928 -> 2026 real + 2027 -> {annual_summary[-1]['year']} prediccion ML)."
    )
    return {
        "gold_points": len(timeline),
        "forecast_end_year": annual_summary[-1]["year"],
        "forecast_end_close": annual_summary[-1]["close_end"],
    }
