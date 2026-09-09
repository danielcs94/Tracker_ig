# 📊 Instagram Follower Tracker

Herramienta de scraping y análisis que monitoriza los seguidores de una cuenta de Instagram a lo largo del tiempo, detecta altas y bajas, y presenta la evolución en un dashboard web con ejecución programada automática.

## 📖 Descripción

A diferencia de un scraper puntual, este proyecto está pensado para un seguimiento continuo: cada vez que se ejecuta, compara el estado actual de seguidores contra la última foto guardada en base de datos, calcula quién empezó a seguir y quién dejó de hacerlo, y almacena un histórico consultable desde un dashboard propio.

## 🏗️ Arquitectura

**Scraping (`tracker_ig.py`)** — Usa Playwright con una sesión de Chromium persistente por cuenta, de forma que solo hay que iniciar sesión en Instagram una vez. Abre el modal de seguidores y hace scroll progresivo hasta cargar la lista completa, manteniendo el orden real en que Instagram los muestra (más recientes primero).

**Persistencia y diffing (`database_tracker.py`)** — Compara el listado de seguidores actual contra el conjunto de "activos" guardado en MySQL. Los que aparecen y no estaban antes se marcan como **ganados**; los que estaban y ya no aparecen, como **perdidos** (sin borrarlos, solo se desactivan, así se conserva el historial completo). Cada ejecución genera además un snapshot diario con el total de seguidores y el balance de altas/bajas.

**Dashboard (`dashboard.py`)** — Aplicación Flask que expone la evolución histórica por cuenta (últimos 30 días), la lista de últimos seguidores ganados y perdidos, y permite programar ejecuciones automáticas diarias a una hora concreta mediante APScheduler.

## 🗃️ Modelo de datos (MySQL)

- **`snapshots`** — foto diaria por cuenta: total de seguidores, ganados y perdidos
- **`followers`** — estado de cada seguidor por cuenta (activo/inactivo, primera y última vez visto)
- **`cambios`** — histórico de eventos individuales (ganado/perdido) por usuario y fecha

La consulta de "perdidos" filtra automáticamente a quienes volvieron a seguir después de dejar de hacerlo, para no mostrar falsos negativos.

## ⚙️ Funcionalidades

- Detección de seguidores nuevos y bajas, con orden cronológico real
- Histórico de evolución de seguidores por cuenta (gráfico de los últimos 30 días)
- Soporte multi-cuenta
- Programación de ejecuciones automáticas diarias (hora configurable) sin intervención manual
- Sesión de Instagram persistente entre ejecuciones (no hay que volver a iniciar sesión cada vez)

## 🚀 Cómo ejecutar

**Requisitos:** Python 3, MySQL (local vía MAMP u otro), Playwright con Chromium instalado.

```bash
# Instalar dependencias
pip install -r requirements.txt
playwright install chromium

# Configurar variables de entorno (.env)
IG_USERNAME=tu_usuario
DB_HOST=127.0.0.1
DB_PORT=8889
DB_USER=root
DB_PASSWORD=root
DB_NAME=instagram_tracker

# Ejecutar el scraper manualmente
python tracker_ig.py

# Levantar el dashboard
python dashboard.py
# → http://localhost:5050
```

La primera vez que se ejecuta `tracker_ig.py`, se abre un navegador para iniciar sesión en Instagram; la sesión se guarda localmente y no vuelve a pedirse en ejecuciones posteriores.

## 🛠️ Tecnologías

- Python (asyncio)
- Playwright (automatización de navegador)
- Flask (dashboard web)
- MySQL / PyMySQL
- APScheduler (tareas programadas)

## 📌 Estado del proyecto

Funcional de extremo a extremo: scraping, persistencia, diffing y dashboard operativos, con automatización diaria configurable.

---

Proyecto personal desarrollado para practicar automatización web, diseño de bases de datos y desarrollo backend con Python.
