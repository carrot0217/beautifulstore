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

# 사용자 테이블
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password VARCHAR(100) NOT NULL,
    is_admin BOOLEAN DEFAULT FALSE
);
""")

# 상품 테이블
cur.execute("""
CREATE TABLE IF NOT EXISTS items (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    quantity INTEGER DEFAULT 0
);
""")

# 주문 테이블
cur.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    item_id INTEGER REFERENCES items(id),
    quantity INTEGER NOT NULL,
    order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    shipped_date TIMESTAMP
);
""")

# 활동 로그 테이블
cur.execute("""
CREATE TABLE IF NOT EXISTS activity_logs (
    id SERIAL PRIMARY KEY,
    actor VARCHAR(50),
    target VARCHAR(50),
    action VARCHAR(100),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")

conn.commit()
cur.close()
conn.close()
print("✅ 모든 테이블 생성 완료!")

