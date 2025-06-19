import os
import psycopg2

def get_connection():
    # RENDER 환경변수가 true일 경우 Render 환경으로 간주
    is_render = os.environ.get('RENDER', 'false').lower() == 'true'

    if is_render:
        # Render 배포 환경용 DB 접속 정보 (Render의 환경변수에서 가져옴)
        return psycopg2.connect(
            dbname=os.environ.get('DB_NAME'),
            user=os.environ.get('DB_USER'),
            password=os.environ.get('DB_PASSWORD'),
            host=os.environ.get('DB_HOST'),
            port=os.environ.get('DB_PORT', 5432)
        )
    else:
        # 로컬 개발 환경용 DB 접속 정보 (직접 입력)
        return psycopg2.connect(
            dbname="beautifulstore",
            user="carrot0217",
            password="oxfvqRLiEN9thqDC1VuRZ9o4xHeKqLPK",  # ← 실제 로컬 PostgreSQL 비밀번호로 수정
            host="dpg-d0pihfje5dus73ds74gg-a.singapore-postgres.render.com",
            port=5432
        )
