import time
import json
import traceback
from enum import Enum
from datetime import datetime, timezone
from collections import deque
from typing import Callable, Dict, List, Optional, Any


class TaskState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    TRIGGERED = "TRIGGERED"


class TriggerRule(str, Enum):
    ALL_SUCCESS = "ALL_SUCCESS"      # Ejecuta solo si todos los padres tuvieron SUCCESS
    ONE_FAILED = "ONE_FAILED"        # Contingencia: se dispara (TRIGGERED) si algun padre fallo
    ALL_DONE = "ALL_DONE"            # Auditoria: ejecuta siempre cuando todos los padres terminan


class TaskNode:
    def __init__(
        self,
        task_id: str,
        name: str,
        description: str,
        func: Callable[[Dict[str, Any], Any], Any],
        trigger_rule: TriggerRule = TriggerRule.ALL_SUCCESS,
        layer: str = "ORCHESTRATION",
    ):
        self.task_id = task_id
        self.name = name
        self.description = description
        self.func = func
        self.trigger_rule = trigger_rule
        self.layer = layer
        self.upstream_ids: List[str] = []
        self.downstream_ids: List[str] = []

        self.state: TaskState = TaskState.PENDING
        self.start_time: Optional[str] = None
        self.end_time: Optional[str] = None
        self.duration_ms: float = 0.0
        self.output: Any = None
        self.error: Optional[str] = None
        self.logs: List[str] = []

    def reset(self):
        self.state = TaskState.PENDING
        self.start_time = None
        self.end_time = None
        self.duration_ms = 0.0
        self.output = None
        self.error = None
        self.logs = []

    def log(self, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{ts}] {message}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "description": self.description,
            "trigger_rule": self.trigger_rule.value,
            "layer": self.layer,
            "upstream_ids": self.upstream_ids,
            "downstream_ids": self.downstream_ids,
            "state": self.state.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": round(self.duration_ms, 2),
            "error": self.error,
            "output": self.output,
            "logs": self.logs,
        }


class DAG:
    """
    Mini-Orquestador basado en Grafo Aciclico Dirigido (DAG).
    Implementa ordenamiento topologico (Kahn), validacion aciclica,
    estados (SUCCESS, FAILED, SKIPPED, TRIGGERED) y Trigger Rules.
    """

    def __init__(self, dag_id: str, description: str):
        self.dag_id = dag_id
        self.description = description
        self.tasks: Dict[str, TaskNode] = {}
        self.dependencies: List[List[str]] = []

    def add_task(self, task: TaskNode) -> TaskNode:
        if task.task_id in self.tasks:
            raise ValueError(f"La tarea '{task.task_id}' ya existe en el DAG.")
        self.tasks[task.task_id] = task
        return task

    def set_dependency(self, upstream_id: str, downstream_id: str):
        if upstream_id not in self.tasks or downstream_id not in self.tasks:
            raise KeyError(f"Dependencia invalida: {upstream_id} -> {downstream_id}")
        if downstream_id not in self.tasks[upstream_id].downstream_ids:
            self.tasks[upstream_id].downstream_ids.append(downstream_id)
        if upstream_id not in self.tasks[downstream_id].upstream_ids:
            self.tasks[downstream_id].upstream_ids.append(upstream_id)
        pair = [upstream_id, downstream_id]
        if pair not in self.dependencies:
            self.dependencies.append(pair)

    def get_topological_order(self) -> List[str]:
        in_degree = {tid: len(t.upstream_ids) for tid, t in self.tasks.items()}
        queue = deque([tid for tid, deg in in_degree.items() if deg == 0])
        order: List[str] = []

        while queue:
            current = queue.popleft()
            order.append(current)
            for nxt in self.tasks[current].downstream_ids:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if len(order) != len(self.tasks):
            raise RuntimeError("Error critico: Se detecto un ciclo en el DAG.")
        return order

    def _evaluate_trigger_rule(self, task: TaskNode) -> tuple[bool, TaskState, str]:
        if not task.upstream_ids:
            return True, TaskState.RUNNING, "Nodo raiz sin dependencias previas."

        parent_states = [self.tasks[pid].state for pid in task.upstream_ids]

        if task.trigger_rule == TriggerRule.ALL_SUCCESS:
            if all(s == TaskState.SUCCESS for s in parent_states):
                return True, TaskState.RUNNING, "Regla ALL_SUCCESS cumplida."
            return False, TaskState.SKIPPED, "Omitida (SKIPPED) por fallo o salto en nodo upstream (ALL_SUCCESS)."

        elif task.trigger_rule == TriggerRule.ONE_FAILED:
            if any(s == TaskState.FAILED for s in parent_states):
                return True, TaskState.RUNNING, "Regla ONE_FAILED activada: ejecutando protocolo de contingencia."
            return False, TaskState.SKIPPED, "Omitida (SKIPPED): sin fallos upstream que requieran contingencia."

        elif task.trigger_rule == TriggerRule.ALL_DONE:
            terminal = {TaskState.SUCCESS, TaskState.FAILED, TaskState.SKIPPED, TaskState.TRIGGERED}
            if all(s in terminal for s in parent_states):
                return True, TaskState.RUNNING, "Regla ALL_DONE cumplida: ejecutando auditoria final."
            return False, TaskState.SKIPPED, "Esperando finalizacion de nodos padres."

        return False, TaskState.SKIPPED, "Regla desconocida."

    def run(self, context: Optional[Dict[str, Any]] = None, log_path: Optional[str] = None) -> Dict[str, Any]:
        ctx: Dict[str, Any] = context if context is not None else {}
        ctx["dag_id"] = self.dag_id
        ctx["task_outputs"] = {}

        order = self.get_topological_order()
        for t in self.tasks.values():
            t.reset()

        dag_start = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()

        for tid in order:
            task = self.tasks[tid]
            should_run, next_state, reason = self._evaluate_trigger_rule(task)
            task.log(reason)

            if not should_run:
                task.state = next_state
                task.start_time = datetime.now(timezone.utc).isoformat()
                task.end_time = task.start_time
                continue

            task.state = TaskState.RUNNING
            task.start_time = datetime.now(timezone.utc).isoformat()
            t0 = time.perf_counter()

            try:
                result = task.func(ctx, task)
                task.output = result
                ctx["task_outputs"][tid] = result
                if task.trigger_rule == TriggerRule.ONE_FAILED:
                    task.state = TaskState.TRIGGERED
                    task.log("Contingencia disparada y ejecutada (TRIGGERED).")
                else:
                    task.state = TaskState.SUCCESS
                    task.log("Tarea completada exitosamente (SUCCESS).")
            except Exception as exc:
                task.state = TaskState.FAILED
                task.error = f"{type(exc).__name__}: {str(exc)}"
                task.log(f"ERROR CRITICO (FAILED): {task.error}")
                task.log(traceback.format_exc().splitlines()[-1])
            finally:
                task.duration_ms = (time.perf_counter() - t0) * 1000.0
                task.end_time = datetime.now(timezone.utc).isoformat()

        total_ms = (time.perf_counter() - dag_start) * 1000.0
        ended_at = datetime.now(timezone.utc).isoformat()

        summary_counts = {
            "SUCCESS": sum(1 for t in self.tasks.values() if t.state == TaskState.SUCCESS),
            "FAILED": sum(1 for t in self.tasks.values() if t.state == TaskState.FAILED),
            "SKIPPED": sum(1 for t in self.tasks.values() if t.state == TaskState.SKIPPED),
            "TRIGGERED": sum(1 for t in self.tasks.values() if t.state == TaskState.TRIGGERED),
        }

        overall_status = "SUCCESS" if summary_counts["FAILED"] == 0 else "CONTINGENCY_TRIGGERED"

        report = {
            "dag_id": self.dag_id,
            "run_id": f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "description": self.description,
            "overall_status": overall_status,
            "started_at": started_at,
            "ended_at": ended_at,
            "total_duration_ms": round(total_ms, 2),
            "summary": summary_counts,
            "execution_order": order,
            "dependencies": self.dependencies,
            "tasks": [self.tasks[tid].to_dict() for tid in order],
        }

        if log_path:
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)

        return report
