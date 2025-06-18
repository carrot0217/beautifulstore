# add_admin.py
import psycopg2

DB_CONFIG = {
    "host": "dpg-d0pihfje5dus73ds74gg-a.singapore-postgres.render.com",
    "port": "5432",
    "database": "beautifulstore",
    "user": "carrot0217",
    "password": "oxfvqRLiEN9thqDC1VuRZ9o4xHeKqLPK"
}

conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor()

# 관리자 계정 추가 (중복 방지)
cur.execute("SELECT * FROM users WHERE username = 'admin'")
if cur.fetchone() is None:
    cur.execute("""
        INSERT INTO users (username, password, is_admin)
        VALUES (%s, %s, %s)
    """, ('admin', 'admin123', True))
    conn.commit()
    print("✅ 관리자 계정 등록 완료 (admin / admin123)")
else:
    print("ℹ️ 관리자 계정은 이미 존재합니다.")

cur.close()
conn.close()
