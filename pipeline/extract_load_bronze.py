import csv
import io
import os
import json
import hashlib
from datetime import datetime, timezone
import requests
import pandas as pd
import yfinance as yf

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def _compute_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_wallstreet_data(ctx, task):
    """
    EXTRACT (Triple Proveedor Externo, Cero Datos Incrustados):
    1. Yahoo Finance API (^GSPC - S&P 500, 1928 -> 2026)
    2. Stooq Historical Market Data API (^spx, 1928 -> 2026)
    3. Federal Reserve Economic Data (FRED API - M1109BUSM293NNBR)
    """
    bronze_csv = os.path.join(ctx["data_dir"], "bronze", "wallstreet_raw_bronze.csv")
    bronze_meta = os.path.join(ctx["data_dir"], "bronze", "bronze_metadata.json")
    force_refresh = ctx.get("force_refresh", False)

    if os.path.exists(bronze_csv) and os.path.exists(bronze_meta) and not force_refresh:
        with open(bronze_meta, "r", encoding="utf-8") as f:
            meta = json.load(f)
        task.log(
            f"Capa Bronce verificada ({meta.get('raw_rows')} sesiones, "
            f"{meta.get('date_min')} -> {meta.get('date_max')})."
        )
        return {
            "source": meta.get("source_api", "Yahoo Finance API (^GSPC - S&P 500)"),
            "already_cached": True,
            "raw_csv_text": None,
            "row_count": meta.get("raw_rows", 0),
        }

    # Proveedor 1: Yahoo Finance API (^GSPC)
    task.log("Consultando Proveedor 1: Yahoo Finance API (^GSPC - S&P 500, 1928 -> 2026)...")
    try:
        ticker = yf.Ticker("^GSPC")
        df = ticker.history(start="1928-01-01", end="2026-12-31", interval="1d", auto_adjust=False)
        if df is not None and not df.empty and len(df) > 1000:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.reset_index()
            df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None).dt.strftime("%Y-%m-%d")
            raw_csv_text = df.to_csv(index=False)
            task.log(f"Extraccion exitosa desde Yahoo Finance API: {len(df)} sesiones diarias.")
            return {
                "source": "Yahoo Finance API (^GSPC - S&P 500)",
                "already_cached": False,
                "raw_csv_text": raw_csv_text,
                "row_count": len(df),
            }
    except Exception as yf_err:
        task.log(f"Aviso Yahoo Finance ({yf_err}). Activando Proveedor 2 (Stooq API)...")

    # Proveedor 2: Stooq Historical Market Data API (^spx)
    try:
        stooq_url = "https://stooq.com/q/d/l/?s=^spx&i=d"
        task.log(f"Consultando Proveedor 2: Stooq Market API ({stooq_url})...")
        resp = requests.get(stooq_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        text = resp.text.strip()
        if text and "Date,Open,High,Low,Close" in text.splitlines()[0]:
            df_stooq = pd.read_csv(io.StringIO(text))
            if len(df_stooq) > 1000:
                task.log(f"Extraccion exitosa desde Stooq API: {len(df_stooq)} sesiones diarias.")
                return {
                    "source": "Stooq Historical Market Data API (^spx)",
                    "already_cached": False,
                    "raw_csv_text": text,
                    "row_count": len(df_stooq),
                }
    except Exception as stooq_err:
        task.log(f"Aviso Stooq ({stooq_err}). Activando Proveedor 3 (FRED API)...")

    # Proveedor 3: Federal Reserve Economic Data (FRED API)
    fred_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=M1109BUSM293NNBR"
    task.log(f"Consultando Proveedor 3: FRED API ({fred_url})...")
    resp = requests.get(fred_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = []
    for r in reader:
        f_str = r.get("observation_date") or r.get("DATE", "")
        val = r.get("M1109BUSM293NNBR", "")
        if f_str and val not in ("", "."):
            v = float(val)
            rows.append({"Date": f_str, "Open": v, "High": v, "Low": v, "Close": v})
    if len(rows) < 20:
        raise RuntimeError("Ninguna de las 3 APIs externas (Yahoo, Stooq, FRED) devolvio datos validos.")

    df_fred = pd.DataFrame(rows)
    task.log(f"Extraccion exitosa desde FRED API: {len(df_fred)} observaciones.")
    return {
        "source": "Federal Reserve Economic Data (FRED API)",
        "already_cached": False,
        "raw_csv_text": df_fred.to_csv(index=False),
        "row_count": len(df_fred),
    }


def load_bronze_layer(ctx, task):
    """
    LOAD (CAPA BRONCE): Persiste los datos crudos intactos y calcula firma SHA-256
    antes de aplicar cualquier transformacion que pueda fallar o corromper el origen.
    """
    extract_out = ctx["task_outputs"]["T1_extract_api"]
    bronze_dir = os.path.join(ctx["data_dir"], "bronze")
    os.makedirs(bronze_dir, exist_ok=True)
    bronze_csv = os.path.join(bronze_dir, "wallstreet_raw_bronze.csv")
    bronze_meta = os.path.join(bronze_dir, "bronze_metadata.json")

    if extract_out.get("already_cached") and os.path.exists(bronze_csv):
        sha = _compute_sha256(bronze_csv)
        with open(bronze_meta, "r", encoding="utf-8") as f:
            meta = json.load(f)
        task.log(f"Integridad Capa Bronce verificada (SHA-256: {sha[:16]}...).")
        return meta

    raw_csv_text = extract_out["raw_csv_text"]
    with open(bronze_csv, "w", encoding="utf-8", newline="") as f:
        f.write(raw_csv_text)

    df_check = pd.read_csv(bronze_csv)
    date_col = "Date" if "Date" in df_check.columns else df_check.columns[0]
    date_min = str(df_check[date_col].min())
    date_max = str(df_check[date_col].max())
    sha = _compute_sha256(bronze_csv)

    meta = {
        "layer": "BRONZE (Raw Immutable Landing Zone)",
        "source_api": extract_out["source"],
        "ingested_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_rows": int(len(df_check)),
        "columns": list(df_check.columns),
        "date_min": date_min,
        "date_max": date_max,
        "sha256_checksum": sha,
        "file_path": "data/bronze/wallstreet_raw_bronze.csv",
    }

    with open(bronze_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    task.log(
        f"Datos crudos guardados en Capa Bronce ({meta['raw_rows']} filas, "
        f"{date_min} -> {date_max}). SHA-256: {sha[:16]}..."
    )
    return meta
