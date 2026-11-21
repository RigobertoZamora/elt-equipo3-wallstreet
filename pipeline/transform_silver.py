import os
import json
import hashlib
import pandas as pd


def _compute_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def transform_silver_layer(ctx, task):
    """
    TRANSFORM (CAPA PLATA): Lee unicamente desde Capa Bronce, valida SHA-256,
    limpia tipos y calcula variaciones, maximo historico, drawdown y umbral critico (-12%).
    """
    bronze_csv = os.path.join(ctx["data_dir"], "bronze", "wallstreet_raw_bronze.csv")
    bronze_meta = os.path.join(ctx["data_dir"], "bronze", "bronze_metadata.json")
    silver_dir = os.path.join(ctx["data_dir"], "silver")
    os.makedirs(silver_dir, exist_ok=True)
    silver_csv = os.path.join(silver_dir, "wallstreet_silver.csv")

    if not os.path.exists(bronze_csv) or not os.path.exists(bronze_meta):
        raise FileNotFoundError("Capa Bronce no encontrada. Ejecute Extract & Load primero.")

    with open(bronze_meta, "r", encoding="utf-8") as f:
        meta = json.load(f)

    current_sha = _compute_sha256(bronze_csv)
    if current_sha != meta.get("sha256_checksum"):
        raise RuntimeError("Violacion de integridad: Capa Bronce alterada.")

    task.log(f"Firma SHA-256 de Capa Bronce verificada ({current_sha[:16]}...).")

    # Modo de prueba de fallo / Circuit Breaker para validar Trigger Rules del DAG
    if ctx.get("simulate_corruption", False):
        task.log("Circuit Breaker / Anomalia detectada en Capa Plata...")
        raise ValueError(
            "CircuitBreakerTriggered: Se detecto anomalia critica en Capa Plata; "
            "Capa Bronce permanece 100% intacta y se dispara contingencia T5."
        )

    df = pd.read_csv(bronze_csv)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).copy()
    for col in ["open", "high", "low", "close"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = pd.to_numeric(df["close"], errors="coerce")

    df = df.dropna(subset=["close"])
    df = df[df["close"] > 0]
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)

    df["year"] = df["date"].dt.year
    df["daily_return_pct"] = df["close"].pct_change().fillna(0.0) * 100.0
    df["rolling_peak"] = df["close"].cummax()
    df["critical_threshold"] = df["rolling_peak"] * 0.88
    df["drawdown_pct"] = ((df["close"] - df["rolling_peak"]) / df["rolling_peak"]) * 100.0
    df["date_str"] = df["date"].dt.strftime("%Y-%m-%d")

    cols_out = [
        "date_str", "year", "open", "high", "low", "close",
        "daily_return_pct", "rolling_peak", "critical_threshold", "drawdown_pct"
    ]
    df_out = df[cols_out].rename(columns={"date_str": "date"})
    df_out.to_csv(silver_csv, index=False)

    crash_days = int((df_out["drawdown_pct"] <= -12.0).sum())
    task.log(
        f"Capa Plata generada: {len(df_out)} sesiones limpias ({df_out['date'].min()} -> {df_out['date'].max()}). "
        f"Sesiones en zona de riesgo (<=-12%): {crash_days}."
    )
    return {
        "silver_rows": int(len(df_out)),
        "risk_sessions": crash_days,
        "date_min": str(df_out["date"].min()),
        "date_max": str(df_out["date"].max()),
    }
