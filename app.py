import os
import json
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request

from orchestrator.dag_engine import DAG, TaskNode, TriggerRule
from pipeline.extract_load_bronze import extract_wallstreet_data, load_bronze_layer
from pipeline.transform_silver import transform_silver_layer
from pipeline.predict_gold import predict_and_build_gold_layer, MODEL_VERSION

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_PATH = os.path.join(DATA_DIR, "logs", "latest_dag_run.json")
BRONZE_META_PATH = os.path.join(DATA_DIR, "bronze", "bronze_metadata.json")
GOLD_PAYLOAD_PATH = os.path.join(DATA_DIR, "gold", "gold_dashboard_payload.json")

app = Flask(__name__)


def contingency_circuit_breaker_task(ctx, task):
    """
    T5 - CONTINGENCIA / CIRCUIT BREAKER (TriggerRule.ONE_FAILED):
    Se dispara automaticamente (estado TRIGGERED) si falla la transformacion o se activa
    el Circuit Breaker ante una caida del mercado. Protege la Capa Bronce y activa alerta.
    """
    bronze_intact = os.path.exists(os.path.join(ctx["data_dir"], "bronze", "wallstreet_raw_bronze.csv"))
    task.log("Protocolo de contingencia / Circuit Breaker activado por fallo upstream.")
    task.log(f"Integridad de Capa Bronce: {'INTACTA Y PROTEGIDA' if bronze_intact else 'NO DISPONIBLE'}.")
    return {
        "circuit_breaker": "ACTIVADO",
        "bronze_preserved": bronze_intact,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


def audit_mission_critical_task(ctx, task):
    """
    T6 - AUDITORIA MISION CRITICA (TriggerRule.ALL_DONE):
    Se ejecuta siempre al concluir el grafo para consolidar telemetria y bitacora.
    """
    task.log("Auditoria de mision critica consolidada en bitacora del orquestador.")
    return {"audited_at_utc": datetime.now(timezone.utc).isoformat()}


def build_wallstreet_dag() -> DAG:
    dag = DAG(
        dag_id="wallstreet_elt_mission_critical_dag",
        description="Orquestador ELT (Bronce -> Plata -> Oro + ML) + Circuit Breaker Wall Street (1928-2031)",
    )

    t1 = TaskNode(
        task_id="T1_extract_api",
        name="Extract API (1928-2026)",
        description="Consulta Yahoo Finance / Stooq / FRED API sin datos incrustados.",
        func=extract_wallstreet_data,
        trigger_rule=TriggerRule.ALL_SUCCESS,
        layer="EXTRACT",
    )
    t2 = TaskNode(
        task_id="T2_load_bronze",
        name="Load Capa Bronce",
        description="Persiste datos crudos inmutables y firma SHA-256.",
        func=load_bronze_layer,
        trigger_rule=TriggerRule.ALL_SUCCESS,
        layer="BRONZE",
    )
    t3 = TaskNode(
        task_id="T3_transform_silver",
        name="Transform Capa Plata",
        description="Limpia esquema, calcula variaciones, drawdown y umbral -12%.",
        func=transform_silver_layer,
        trigger_rule=TriggerRule.ALL_SUCCESS,
        layer="SILVER",
    )
    t4 = TaskNode(
        task_id="T4_ml_gold",
        name="Capa Oro + ML (2027-2031)",
        description="Entrena ensamble ML + FFT hasta 2026 y proyecta 2027-2031.",
        func=predict_and_build_gold_layer,
        trigger_rule=TriggerRule.ALL_SUCCESS,
        layer="GOLD",
    )
    t5 = TaskNode(
        task_id="T5_contingencia",
        name="Circuit Breaker / Contingencia",
        description="Se dispara solo si falla T3 o se activa alerta critica (ONE_FAILED).",
        func=contingency_circuit_breaker_task,
        trigger_rule=TriggerRule.ONE_FAILED,
        layer="CONTINGENCY",
    )
    t6 = TaskNode(
        task_id="T6_auditoria",
        name="Auditoria Mision Critica",
        description="Registra bitacora final pase lo que pase (ALL_DONE).",
        func=audit_mission_critical_task,
        trigger_rule=TriggerRule.ALL_DONE,
        layer="AUDIT",
    )

    for node in [t1, t2, t3, t4, t5, t6]:
        dag.add_task(node)

    # Flujo principal ELT
    dag.set_dependency("T1_extract_api", "T2_load_bronze")
    dag.set_dependency("T2_load_bronze", "T3_transform_silver")
    dag.set_dependency("T3_transform_silver", "T4_ml_gold")

    # Contingencia ante fallos (ONE_FAILED)
    dag.set_dependency("T3_transform_silver", "T5_contingencia")

    # Auditoria final (ALL_DONE)
    dag.set_dependency("T4_ml_gold", "T6_auditoria")
    dag.set_dependency("T5_contingencia", "T6_auditoria")

    return dag


def execute_pipeline(force_refresh: bool = False, simulate_corruption: bool = False):
    os.makedirs(os.path.join(DATA_DIR, "logs"), exist_ok=True)
    dag = build_wallstreet_dag()
    context = {
        "data_dir": DATA_DIR,
        "force_refresh": force_refresh,
        "simulate_corruption": simulate_corruption,
    }
    report = dag.run(context=context, log_path=LOG_PATH)

    bronze_meta = None
    if os.path.exists(BRONZE_META_PATH):
        with open(BRONZE_META_PATH, "r", encoding="utf-8") as f:
            bronze_meta = json.load(f)

    gold_payload = None
    if os.path.exists(GOLD_PAYLOAD_PATH):
        with open(GOLD_PAYLOAD_PATH, "r", encoding="utf-8") as f:
            gold_payload = json.load(f)

    return {
        "dag_report": report,
        "bronze_meta": bronze_meta,
        "gold_payload": gold_payload,
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status", methods=["GET"])
def api_status():
    need_rebuild = not (os.path.exists(LOG_PATH) and os.path.exists(GOLD_PAYLOAD_PATH))
    gold_payload = None

    if not need_rebuild:
        with open(GOLD_PAYLOAD_PATH, "r", encoding="utf-8") as f:
            gold_payload = json.load(f)
        if gold_payload.get("model_version") != MODEL_VERSION:
            need_rebuild = True

    if need_rebuild:
        result = execute_pipeline(force_refresh=False, simulate_corruption=False)
        return jsonify(result)

    with open(LOG_PATH, "r", encoding="utf-8") as f:
        dag_report = json.load(f)

    bronze_meta = None
    if os.path.exists(BRONZE_META_PATH):
        with open(BRONZE_META_PATH, "r", encoding="utf-8") as f:
            bronze_meta = json.load(f)

    return jsonify({
        "dag_report": dag_report,
        "bronze_meta": bronze_meta,
        "gold_payload": gold_payload,
    })


@app.route("/api/run-dag", methods=["POST"])
def api_run_dag():
    body = request.get_json(silent=True) or {}
    force_refresh = bool(body.get("force_refresh", False))
    simulate_corruption = bool(body.get("simulate_corruption", False))
    result = execute_pipeline(
        force_refresh=force_refresh,
        simulate_corruption=simulate_corruption,
    )
    return jsonify(result)


if __name__ == "__main__":
    print("=================================================================")
    print("  MONITOR WALL STREET ELT + DAG (1928 - 2026+) -> ML (2027-2031)")
    print("  Abre tu navegador en: http://127.0.0.1:5000")
    print("=================================================================")
    app.run(host="127.0.0.1", port=5000, debug=False)
