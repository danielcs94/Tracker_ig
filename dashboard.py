from flask import Flask, render_template_string, jsonify, request, redirect
import pymysql
import os
import json
import asyncio
import threading
from datetime import datetime, date
from pathlib import Path
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

load_dotenv()

app = Flask(__name__)
scheduler = BackgroundScheduler()
scheduler.start()

BASE_DIR = Path(__file__).parent
SCHEDULE_FILE = BASE_DIR / "schedule.json"

# ── Helpers DB ──────────────────────────────────────────────

def get_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "8889")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", "root"),
        database=os.getenv("DB_NAME", "instagram_tracker"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )

def get_cuentas():
    conn = get_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT cuenta FROM snapshots ORDER BY cuenta")
            return [r["cuenta"] for r in cur.fetchall()]

def get_datos(cuenta):
    conn = get_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT fecha, total_followers, ganados, perdidos
                FROM snapshots WHERE cuenta = %s
                ORDER BY fecha DESC LIMIT 30
            """, (cuenta,))
            historial = cur.fetchall()
            # Perdidos: solo los que no han vuelto a seguir después
            cur.execute("""
                SELECT username, MAX(creado_en) as creado_en FROM cambios
                WHERE cuenta = %s AND tipo = 'perdido'
                AND username NOT IN (
                    SELECT p.username FROM cambios p
                    INNER JOIN cambios g ON p.username = g.username AND p.cuenta = g.cuenta
                    WHERE p.cuenta = %s AND p.tipo = 'perdido' AND g.tipo = 'ganado'
                    AND g.id > p.id
                )
                GROUP BY username
                ORDER BY MAX(id) DESC LIMIT 50
            """, (cuenta, cuenta))
            perdidos = cur.fetchall()
            # Ganados: los 10 más recientes
            cur.execute("""
                SELECT username, MAX(creado_en) as creado_en FROM cambios
                WHERE cuenta = %s AND tipo = 'ganado'
                GROUP BY username
                ORDER BY MAX(id) DESC LIMIT 10
            """, (cuenta,))
            ganados = cur.fetchall()
            return historial, perdidos, ganados

# ── Scheduler ───────────────────────────────────────────────

def load_schedule():
    if SCHEDULE_FILE.exists():
        return json.loads(SCHEDULE_FILE.read_text())
    return {"hora": "09:00", "activo": False, "ultimo_run": None, "ultimo_estado": None}

def save_schedule(data):
    SCHEDULE_FILE.write_text(json.dumps(data))

def run_tracker_auto():
    """Ejecuta el tracker en background sin interacción (sesión ya guardada)."""
    import sys
    sched = load_schedule()
    cuentas_env = os.getenv("IG_USERNAME", "")
    cuentas = [c.strip() for c in cuentas_env.split(",") if c.strip()]
    if not cuentas:
        sched["ultimo_estado"] = "Error: no hay cuentas en .env"
        save_schedule(sched)
        return

    async def _run():
        from playwright.async_api import async_playwright
        from database_tracker import actualizar_followers, guardar_snapshot

        resultados = []
        for cuenta in cuentas:
            session_dir = BASE_DIR / f".pw-session-{cuenta}"
            if not session_dir.exists():
                resultados.append(f"@{cuenta}: sin sesión guardada")
                continue
            try:
                async with async_playwright() as p:
                    context = await p.chromium.launch_persistent_context(
                        str(session_dir),
                        headless=True,
                        viewport={"width": 1280, "height": 900},
                        locale="es-ES",
                        args=["--no-sandbox", "--disable-dev-shm-usage"],
                    )
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.goto(f"https://www.instagram.com/{cuenta}/", timeout=60000)
                    await page.wait_for_timeout(4000)

                    # Cerrar popups
                    for texto in ["Ahora no", "Not Now"]:
                        try:
                            btn = page.locator(f'button:has-text("{texto}")').first
                            if await btn.is_visible():
                                await btn.click()
                                await page.wait_for_timeout(800)
                        except Exception:
                            pass

                    # Click en seguidores
                    clicked = False
                    for selector in [f'a[href*="followers"]', f'a[href="/{cuenta}/followers/"]']:
                        try:
                            el = page.locator(selector).first
                            if await el.is_visible():
                                await el.click()
                                clicked = True
                                break
                        except Exception:
                            pass

                    if not clicked:
                        await context.close()
                        resultados.append(f"@{cuenta}: no se pudo abrir seguidores (¿sesión expirada?)")
                        continue

                    await page.wait_for_timeout(3000)
                    await page.wait_for_selector('div[role="dialog"]', timeout=15000)

                    usernames = set()
                    last_count = 0
                    sin_cambios = 0

                    while True:
                        dialog = page.locator('div[role="dialog"]')
                        links = await dialog.locator('a[href^="/"]').all()
                        for link in links:
                            href = await link.get_attribute("href")
                            if href and href.count("/") == 2:
                                u = href.strip("/")
                                if u and u != cuenta:
                                    usernames.add(u)

                        if len(usernames) == last_count:
                            sin_cambios += 1
                            if sin_cambios >= 6:
                                break
                        else:
                            sin_cambios = 0
                            last_count = len(usernames)

                        await page.evaluate("""
                            () => {
                                const dialog = document.querySelector('div[role="dialog"]');
                                if (!dialog) return;
                                for (const div of dialog.querySelectorAll('div')) {
                                    if (div.scrollHeight > div.clientHeight + 10) div.scrollTop += 1200;
                                }
                            }
                        """)
                        await page.wait_for_timeout(1800)

                    await context.close()

                    ganados, perdidos = actualizar_followers(cuenta, usernames)
                    guardar_snapshot(cuenta, len(usernames), len(ganados), len(perdidos))
                    resultados.append(f"@{cuenta}: OK ({len(usernames)} seguidores, +{len(ganados)}/-{len(perdidos)})")

            except Exception as e:
                resultados.append(f"@{cuenta}: Error — {str(e)[:80]}")

        sched["ultimo_run"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        sched["ultimo_estado"] = " | ".join(resultados)
        save_schedule(sched)

    asyncio.run(_run())

def aplicar_schedule(hora, activo):
    scheduler.remove_all_jobs()
    if activo and hora:
        h, m = hora.split(":")
        scheduler.add_job(run_tracker_auto, CronTrigger(hour=int(h), minute=int(m)), id="tracker_job")

# Cargar schedule al arrancar
_s = load_schedule()
aplicar_schedule(_s.get("hora", "09:00"), _s.get("activo", False))

# ── HTML ─────────────────────────────────────────────────────

HTML = '''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Instagram Tracker</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f5f5; color: #1a1a1a; }
header { background: white; border-bottom: 1px solid #e5e5e5; padding: 0 2rem; display: flex; align-items: center; gap: 1rem; height: 56px; flex-wrap: wrap; }
header h1 { font-size: 15px; font-weight: 600; margin-right: 0.5rem; }
.cuenta-select { border: 1px solid #ddd; border-radius: 8px; padding: 5px 10px; font-size: 13px; background: white; cursor: pointer; }
.schedule-box { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.schedule-box label { font-size: 12px; color: #888; }
.schedule-box input[type="time"] { border: 1px solid #ddd; border-radius: 8px; padding: 5px 10px; font-size: 13px; }
.toggle { position: relative; width: 36px; height: 20px; }
.toggle input { opacity: 0; width: 0; height: 0; }
.slider { position: absolute; inset: 0; background: #ddd; border-radius: 20px; cursor: pointer; transition: .3s; }
.slider:before { content: ""; position: absolute; width: 14px; height: 14px; left: 3px; top: 3px; background: white; border-radius: 50%; transition: .3s; }
input:checked + .slider { background: #1D9E75; }
input:checked + .slider:before { transform: translateX(16px); }
.save-btn { background: #1a1a1a; color: white; border: none; border-radius: 8px; padding: 6px 14px; font-size: 12px; cursor: pointer; }
.save-btn:hover { background: #333; }
.run-btn { background: white; border: 1px solid #ddd; border-radius: 8px; padding: 6px 14px; font-size: 12px; cursor: pointer; display:flex; align-items:center; gap:5px; }
.run-btn:hover { background: #f5f5f5; }
.status-bar { background: #f0f9f5; border-bottom: 1px solid #d0eadf; padding: 6px 2rem; font-size: 12px; color: #0F6E56; display: {% if sched.ultimo_run %}flex{% else %}none{% endif %}; gap: 1rem; }
.main { max-width: 1100px; margin: 0 auto; padding: 1.5rem; }
.metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 1.5rem; }
.metric { background: white; border-radius: 12px; padding: 1.25rem; border: 1px solid #e5e5e5; }
.metric-label { font-size: 12px; color: #888; margin-bottom: 6px; }
.metric-value { font-size: 28px; font-weight: 600; }
.metric-value.up { color: #1D9E75; }
.metric-value.down { color: #D85A30; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
.card { background: white; border-radius: 12px; padding: 1.25rem; border: 1px solid #e5e5e5; }
.card h2 { font-size: 11px; font-weight: 600; color: #888; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 1rem; }
.user-row { display: flex; align-items: center; gap: 10px; padding: 7px 0; border-bottom: 1px solid #f5f5f5; }
.user-row:last-child { border-bottom: none; }
.avatar { width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 600; flex-shrink: 0; }
.avatar.lost { background: #FAECE7; color: #993C1D; }
.avatar.gained { background: #E1F5EE; color: #0F6E56; }
.user-handle { font-size: 13px; flex: 1; }
.user-date { font-size: 11px; color: #bbb; }
.empty { font-size: 13px; color: #bbb; padding: 0.5rem 0; }
.bars { display: flex; align-items: flex-end; gap: 4px; height: 140px; margin-top: 0.75rem; }
.bar-col { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 3px; height: 100%; justify-content: flex-end; }
.bar { width: 100%; border-radius: 3px 3px 0 0; background: #9FE1CB; min-height: 2px; }
.bar-label { font-size: 9px; color: #bbb; white-space: nowrap; }
.bar-val { font-size: 9px; color: #888; }
.no-data { display: flex; align-items: center; justify-content: center; height: 140px; color: #bbb; font-size: 13px; }
.spinner { display:inline-block; width:12px; height:12px; border:2px solid #ddd; border-top-color:#1a1a1a; border-radius:50%; animation:spin .6s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<header>
  <h1>📊 Instagram Tracker</h1>
  <select class="cuenta-select" id="cuentaSelect" onchange="cambiarCuenta()">
    {% for c in cuentas %}
    <option value="{{ c }}" {% if c == cuenta_actual %}selected{% endif %}>@{{ c }}</option>
    {% endfor %}
  </select>
  <span style="font-size:12px;color:#aaa;">{{ ultima_fecha }}</span>

  <div class="schedule-box">
    <label>Auto-análisis</label>
    <label class="toggle">
      <input type="checkbox" id="scheduleToggle" {% if sched.activo %}checked{% endif %}>
      <span class="slider"></span>
    </label>
    <input type="time" id="scheduleHora" value="{{ sched.hora }}">
    <button class="save-btn" onclick="guardarSchedule()">Guardar</button>
    <button class="run-btn" id="runBtn" onclick="ejecutarAhora()">
      <span id="runIcon">▶</span> Analizar ahora
    </button>
  </div>
</header>

{% if sched.ultimo_run %}
<div class="status-bar">
  <span>✓ Último análisis: <strong>{{ sched.ultimo_run }}</strong></span>
  <span>{{ sched.ultimo_estado }}</span>
</div>
{% endif %}

<div class="main">
  <div class="metrics">
    <div class="metric">
      <div class="metric-label">Seguidores totales</div>
      <div class="metric-value">{{ total }}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Ganados hoy</div>
      <div class="metric-value up">+{{ ganados_hoy }}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Perdidos hoy</div>
      <div class="metric-value down">-{{ perdidos_hoy }}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Neto últimos 7 días</div>
      <div class="metric-value {% if neto7 >= 0 %}up{% else %}down{% endif %}">
        {% if neto7 >= 0 %}+{% endif %}{{ neto7 }}
      </div>
    </div>
  </div>

  <div class="card" style="margin-bottom:12px;">
    <h2>Evolución de seguidores (últimos 30 días)</h2>
    {% if historial %}
    <div class="bars" id="barsChart"></div>
    {% else %}
    <div class="no-data">Sin datos suficientes todavía</div>
    {% endif %}
  </div>

    <div class="grid2">
    <div class="card">
      <h2>Dejaron de seguir</h2>
      {% if perdidos %}
        {% for c in perdidos %}
        <div class="user-row">
          <div class="avatar lost">{{ c.username[:2].upper() }}</div>
          <span class="user-handle">@{{ c.username }}</span>
          <span class="user-date">{{ c.fecha }}</span>
        </div>
        {% endfor %}
      {% else %}
        <div class="empty">Nadie te ha dejado de seguir</div>
      {% endif %}
    </div>
    <div class="card">
      <h2>Nuevos seguidores</h2>
      {% if ganados %}
        {% for c in ganados %}
        <div class="user-row">
          <div class="avatar gained">{{ c.username[:2].upper() }}</div>
          <span class="user-handle">@{{ c.username }}</span>
          <span class="user-date">{{ c.fecha }}</span>
        </div>
        {% endfor %}
      {% else %}
        <div class="empty">Sin nuevos seguidores aún</div>
      {% endif %}
    </div>
  </div>
</div>

<script>
const historial = {{ historial_json | safe }};

function renderBars() {
  const container = document.getElementById('barsChart');
  if (!container || historial.length === 0) return;
  const reversed = [...historial].reverse();
  const max = Math.max(...reversed.map(h => h.total_followers));
  const min = Math.min(...reversed.map(h => h.total_followers));
  const range = max - min || 1;
  container.innerHTML = reversed.map(h => {
    const pct = 15 + ((h.total_followers - min) / range) * 80;
    const fecha = h.fecha.slice(5);
    return `<div class="bar-col">
      <div class="bar-val">${h.total_followers}</div>
      <div class="bar" style="height:${pct}%"></div>
      <div class="bar-label">${fecha}</div>
    </div>`;
  }).join('');
}

function cambiarCuenta() {
  window.location.href = '/?cuenta=' + document.getElementById('cuentaSelect').value;
}

async function guardarSchedule() {
  const hora = document.getElementById('scheduleHora').value;
  const activo = document.getElementById('scheduleToggle').checked;
  const res = await fetch('/schedule', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({hora, activo})
  });
  const data = await res.json();
  if (data.ok) {
    const btn = document.querySelector('.save-btn');
    btn.textContent = '✓ Guardado';
    setTimeout(() => btn.textContent = 'Guardar', 2000);
  }
}

async function ejecutarAhora() {
  const btn = document.getElementById('runBtn');
  btn.innerHTML = '<span class="spinner"></span> Analizando...';
  btn.disabled = true;
  const res = await fetch('/run-now', {method: 'POST'});
  const data = await res.json();
  btn.innerHTML = '▶ Analizar ahora';
  btn.disabled = false;
  if (data.ok) window.location.reload();
}

renderBars();
</script>
</body>
</html>
'''

# ── Rutas ────────────────────────────────────────────────────

@app.route("/")
def index():
    cuenta = request.args.get("cuenta")
    cuentas = get_cuentas()

    if not cuentas:
        return "<h2 style='font-family:sans-serif;padding:2rem'>No hay datos. Ejecuta tracker_ig.py primero.</h2>"

    if not cuenta or cuenta not in cuentas:
        cuenta = cuentas[0]

    historial, perdidos, ganados = get_datos(cuenta)
    sched = load_schedule()

    total = historial[0]["total_followers"] if historial else 0
    ganados_hoy = historial[0]["ganados"] if historial else 0
    perdidos_hoy = historial[0]["perdidos"] if historial else 0
    ultima_fecha = "Última actualización: " + str(historial[0]["fecha"]) if historial else ""
    neto7 = sum(r["ganados"] - r["perdidos"] for r in historial[:7])

    historial_json = json.dumps([
        {"fecha": str(r["fecha"]), "total_followers": r["total_followers"],
         "ganados": r["ganados"], "perdidos": r["perdidos"]}
        for r in historial
    ])

    perdidos_fmt = [{"username": c["username"], "fecha": str(c["creado_en"])[:16].replace("T", " ")} for c in perdidos]
    ganados_fmt = [{"username": c["username"], "fecha": str(c["creado_en"])[:16].replace("T", " ")} for c in ganados]

    return render_template_string(HTML,
        cuentas=cuentas, cuenta_actual=cuenta,
        total=total, ganados_hoy=ganados_hoy, perdidos_hoy=perdidos_hoy,
        neto7=neto7, ultima_fecha=ultima_fecha,
        historial=historial, historial_json=historial_json,
        perdidos=perdidos_fmt, ganados=ganados_fmt, sched=sched,
    )

@app.route("/schedule", methods=["POST"])
def set_schedule():
    data = request.get_json()
    hora = data.get("hora", "09:00")
    activo = data.get("activo", False)
    sched = load_schedule()
    sched["hora"] = hora
    sched["activo"] = activo
    save_schedule(sched)
    aplicar_schedule(hora, activo)
    return jsonify({"ok": True})

@app.route("/run-now", methods=["POST"])
def run_now():
    thread = threading.Thread(target=run_tracker_auto, daemon=True)
    thread.start()
    thread.join(timeout=300)
    return jsonify({"ok": True})

if __name__ == "__main__":
    print("Dashboard disponible en http://localhost:5050")
    app.run(port=5050, debug=False)
