import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright
from database_tracker import actualizar_followers, guardar_snapshot, get_historial
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
INSTAGRAM_USER = os.getenv("IG_USERNAME")

async def cerrar_popups(page):
    for texto in ["Ahora no", "Not Now", "Cerrar", "Close"]:
        try:
            btn = page.locator(f'button:has-text("{texto}")').first
            if await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(800)
        except Exception:
            pass

async def get_followers(page, username):
    print(f"  Abriendo perfil de @{username}...")
    await page.goto(f"https://www.instagram.com/{username}/", timeout=60000)
    await page.wait_for_timeout(4000)
    await cerrar_popups(page)

    print("  Buscando enlace de seguidores...")
    clicked = False
    for selector in [f'a[href*="followers"]', f'a[href="/{username}/followers/"]']:
        try:
            el = page.locator(selector).first
            if await el.is_visible():
                await el.click()
                clicked = True
                break
        except Exception:
            pass

    if not clicked:
        try:
            for link in await page.locator('a').all():
                href = await link.get_attribute("href") or ""
                text = (await link.inner_text()).lower()
                if "follower" in href or "seguidor" in text or "follower" in text:
                    await link.click()
                    clicked = True
                    break
        except Exception:
            pass

    if not clicked:
        print("  No se encontró el enlace. Haz click en 'seguidores' en el navegador y pulsa ENTER.")
        input("  Pulsa ENTER cuando el modal esté abierto > ")
    else:
        await page.wait_for_timeout(3000)

    print("  Esperando modal...")
    try:
        await page.wait_for_selector('div[role="dialog"]', timeout=15000)
    except Exception:
        print("  No apareció el modal. Ábrelo manualmente y pulsa ENTER.")
        input("  Pulsa ENTER > ")
        try:
            await page.wait_for_selector('div[role="dialog"]', timeout=5000)
        except Exception:
            print("  No se detectó el modal. Abortando.")
            return []

    print("  Extrayendo seguidores (scrolling)...")
    # Lista ordenada — los primeros en el modal son los más recientes en Instagram
    usernames_ordered = []
    seen = set()
    last_count = 0
    sin_cambios = 0

    while True:
        dialog = page.locator('div[role="dialog"]')
        links = await dialog.locator('a[href^="/"]').all()
        for link in links:
            href = await link.get_attribute("href")
            if href and href.count("/") == 2:
                u = href.strip("/")
                if u and u != username and u not in seen:
                    seen.add(u)
                    usernames_ordered.append(u)

        if len(usernames_ordered) == last_count:
            sin_cambios += 1
            if sin_cambios >= 6:
                break
        else:
            sin_cambios = 0
            last_count = len(usernames_ordered)
            print(f"  {len(usernames_ordered)} seguidores cargados...", end="\r")

        try:
            await page.evaluate("""
                () => {
                    const dialog = document.querySelector('div[role="dialog"]');
                    if (!dialog) return;
                    for (const div of dialog.querySelectorAll('div')) {
                        if (div.scrollHeight > div.clientHeight + 10) div.scrollTop += 1200;
                    }
                }
            """)
        except Exception:
            pass
        await page.wait_for_timeout(1800)

    print(f"\n  Total: {len(usernames_ordered)} seguidores")
    return usernames_ordered


async def main():
    print("=" * 50)
    print("  Instagram Follower Tracker")
    print("=" * 50)

    if not INSTAGRAM_USER:
        print("\nError: falta IG_USERNAME en el .env")
        return

    print(f"\nCuenta por defecto en .env: @{INSTAGRAM_USER}")
    otra = input("¿Analizar otra cuenta? Escribe el usuario o pulsa ENTER para usar la de .env: ").strip()
    cuenta = otra if otra else INSTAGRAM_USER

    session_dir = BASE_DIR / f".pw-session-{cuenta}"
    session_dir.mkdir(exist_ok=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(session_dir),
            headless=False,
            viewport={"width": 1280, "height": 900},
            locale="es-ES",
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        page = context.pages[0] if context.pages else await context.new_page()

        print(f"\nSe ha abierto el navegador para @{cuenta}.")
        print("Inicia sesión en Instagram si es necesario.")
        input("Cuando estés en tu feed, pulsa ENTER > ")

        followers_ordered = await get_followers(page, cuenta)
        await context.close()

    if not followers_ordered:
        print("No se pudieron obtener seguidores. Abortando.")
        return

    followers_set = set(followers_ordered)

    print("\nGuardando en base de datos...")
    ganados, perdidos = actualizar_followers(cuenta, followers_ordered)
    guardar_snapshot(cuenta, len(followers_ordered), len(ganados), len(perdidos))

    print("\n" + "=" * 50)
    print(f"  RESUMEN — @{cuenta}")
    print("=" * 50)
    print(f"  Seguidores totales : {len(followers_ordered)}")
    print(f"  Nuevos seguidores  : +{len(ganados)}")
    print(f"  Dejaron de seguir  : -{len(perdidos)}")

    if ganados:
        print(f"\n  Te han seguido:")
        for u in ganados:
            print(f"    + @{u}")

    if perdidos:
        print(f"\n  Te han dejado de seguir:")
        for u in sorted(perdidos):
            print(f"    - @{u}")

    print("\n  Historial reciente:")
    for row in get_historial(cuenta, 7):
        print(f"    {row['fecha']}  |  {row['total_followers']} seguidores  |  +{row['ganados']} / -{row['perdidos']}")

    print(f"\n  Listo. Abre el dashboard en http://localhost:5050")


if __name__ == "__main__":
    asyncio.run(main())
