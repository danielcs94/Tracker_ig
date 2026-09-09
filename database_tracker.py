import pymysql
import os
from datetime import date
from dotenv import load_dotenv

load_dotenv()

def get_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "8889")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", "root"),
        database=os.getenv("DB_NAME", "instagram_tracker"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False
    )

def guardar_snapshot(cuenta, total, ganados, perdidos):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO snapshots (cuenta, fecha, total_followers, ganados, perdidos)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    total_followers = VALUES(total_followers),
                    ganados = VALUES(ganados),
                    perdidos = VALUES(perdidos)
            """, (cuenta, date.today(), total, ganados, perdidos))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_followers_activos(cuenta):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username FROM followers WHERE cuenta = %s AND activo = 1", (cuenta,))
            return set(row["username"] for row in cur.fetchall())
    finally:
        conn.close()

def actualizar_followers(cuenta, usernames_ordered: list):
    """
    usernames_ordered: lista ordenada tal como aparece en el modal de Instagram
    (índice 0 = seguidor más reciente)
    """
    hoy = date.today()
    usernames_set = set(usernames_ordered)
    activos_antes = get_followers_activos(cuenta)

    # Ganados: los nuevos, en el orden en que aparecen en el modal
    ganados_set = usernames_set - activos_antes
    ganados_ordered = [u for u in usernames_ordered if u in ganados_set]
    perdidos = activos_antes - usernames_set

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Nuevos seguidores — insertamos en orden para que el id refleje la posición
            for u in ganados_ordered:
                cur.execute("""
                    INSERT INTO followers (cuenta, username, primera_vez_visto, ultima_vez_visto, activo)
                    VALUES (%s, %s, %s, %s, 1)
                    ON DUPLICATE KEY UPDATE ultima_vez_visto = %s, activo = 1
                """, (cuenta, u, hoy, hoy, hoy))
                cur.execute(
                    "INSERT INTO cambios (cuenta, fecha, username, tipo) VALUES (%s, %s, %s, 'ganado')",
                    (cuenta, hoy, u)
                )

            # Seguidores perdidos
            for u in perdidos:
                cur.execute(
                    "UPDATE followers SET activo = 0, ultima_vez_visto = %s WHERE cuenta = %s AND username = %s",
                    (hoy, cuenta, u)
                )
                cur.execute(
                    "INSERT INTO cambios (cuenta, fecha, username, tipo) VALUES (%s, %s, %s, 'perdido')",
                    (cuenta, hoy, u)
                )

            # Actualizar ultima_vez_visto para los que siguen activos
            for u in usernames_set & activos_antes:
                cur.execute(
                    "UPDATE followers SET ultima_vez_visto = %s WHERE cuenta = %s AND username = %s",
                    (hoy, cuenta, u)
                )

        conn.commit()
        print(f"  DB: +{len(ganados_ordered)} ganados, -{len(perdidos)} perdidos guardados correctamente")

    except Exception as e:
        conn.rollback()
        print(f"  DB ERROR: {e}")
        raise e
    finally:
        conn.close()

    return ganados_ordered, perdidos

def get_historial(cuenta, dias=7):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT fecha, total_followers, ganados, perdidos
                FROM snapshots WHERE cuenta = %s
                ORDER BY fecha DESC LIMIT %s
            """, (cuenta, dias))
            return cur.fetchall()
    finally:
        conn.close()
