# Monitor Bursátil Wall Street (1928 – 2031) + Orquestador DAG & Paradigma ELT

Versión unificada que combina:
1. **Reproducción en Tiempo Real + Gráfica + Tabla de Historial de Alertas**:
   - Extrae datos históricos reales desde **1928** hasta **2026** (con triple respaldo de APIs externas: **Yahoo Finance `^GSPC`**, **Stooq `^spx`** y **FRED API**), sin ningún dato incrustado en el código.
   - Proyecta los siguientes 5 años (**2027 – 2031**) mediante un ensamble de **Machine Learning (`RandomForestRegressor` + `GradientBoostingRegressor` + ciclos armónicos `FFT`)**.
   - Incluye la **Tabla de Historial (Bitácora de Alertas y Eventos Detectados)** que registra en vivo cada advertencia, crack y disparo del Circuit Breaker, junto con alarma sonora y notificaciones del navegador.
2. **Paradigma ELT (Capa Bronce -> Capa Plata -> Capa Oro)** con firma criptográfica **SHA-256**.
3. **Mini-Orquestador basado en DAGs con Diagrama SVG Interactivo**:
   - Visualiza en tiempo real los nodos `T1` a `T6` con estados `SUCCESS`, `FAILED`, `SKIPPED` y `TRIGGERED`, además de conectar automáticamente el **Circuit Breaker** cuando se detecta o simula una caída del mercado.

## Cómo Ejecutar
- **Windows**: Haz doble clic en `run.bat`.
- **Terminal**:
```bash
cd wallstreet_elt_orquestador
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```
Abre **`http://127.0.0.1:5000`** en tu navegador.
