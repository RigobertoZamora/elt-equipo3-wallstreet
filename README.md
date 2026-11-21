# Simulador y Detector de Caída de Wall Street (1929)

Proyecto web interactivo desarrollado con **Python (Flask)** y **JavaScript (Chart.js)** que obtiene dinámicamente los datos históricos del Crack de Wall Street de 1929 desde una **API externa** (sin datos incrustados en el código), los grafica en tiempo real y permite simular una caída del mercado mediante un botón, activando un motor de detección y alertas automáticas.

![Python](https://img.shields.io/badge/Python-3.12-blue?style=for-the-badge&logo=python&logoColor=white)
![Data Engineering](https://img.shields.io/badge/Data_Engineering-Pipeline-orange?style=for-the-badge)

<img width="1600" height="773" alt="12" src="https://github.com/user-attachments/assets/47d76037-3a32-49eb-8ab5-f1f9894a3bb5" />
<img width="1426" height="699" alt="13" src="https://github.com/user-attachments/assets/0ae9da82-0106-4a3e-8341-660171e1449d" />
<img width="1433" height="708" alt="14" src="https://github.com/user-attachments/assets/1fd15427-6f2a-46e6-8df9-263d2e66aa8c" />

## Características Principales
1. **Ingesta desde API externa (cero datos hardcodeados):** El backend (`app.py`) consulta en tiempo real la API pública de Yahoo Finance (`^GSPC` de 1929), con respaldo automático a Stooq (`^DJI`) y FRED.
2. **Gráfica en tiempo real:** Visualiza la evolución del índice bursátil y una línea dinámica de umbral crítico (-12% desde el máximo registrado).
3. **Botón "Simular Caída del Mercado":** Detona un desplome bursátil sobre el flujo de datos.
4. **Motor de Detección y Sistema de Alertas:** Evalúa en cada sesión la variación diaria y el *drawdown* acumulado. Al detectar una caída anormal, dispara:
   - Banner de alerta crítica en pantalla.
   - Alarma sonora sintetizada (Web Audio API).
   - Notificación de escritorio del navegador.
   - Registro automático en la bitácora de eventos.

## Requisitos e Instalación

1. Asegúrate de tener **Python 3.9 o superior** instalado.
2. Abre una terminal dentro de la carpeta del proyecto e instala las dependencias:
   ```bash
   pip install -r requirements.txt
   ```
3. Ejecuta el servidor:
   ```bash
   python app.py
   ```
4. Abre tu navegador en:
   `http://127.0.0.1:5000`

# Conclusión Técnica (ETL vs ELT):
En una arquitectura ETL tradicional, la validación de volatilidad y el procesamiento de órdenes se ejecutan en memoria antes de almacenar la información; si ocurre un colapso súbito como un Flash Crash y falla la tarea de análisis (T3_Analisis_Volatilidad), la ejecución se interrumpe y se pierden los registros recibidos durante la ventana de crisis. Al persistir de inmediato los datos crudos en la Capa Bronce (T2), cualquier falla o anomalía de mercado detectada en T3 cancela las etapas de transformación subsiguientes (T4 y T5 - SKIPPED) y dispara de inmediato la contingencia (T6 - Circuit Breaker). El dato original NUNCA se pierde, permitiendo auditar el incidente bursátil y reprocesar el pipeline sin re-extracción.
