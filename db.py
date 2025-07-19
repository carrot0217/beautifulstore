import os
import psycopg2
from dotenv import load_dotenv

# .env.txt 파일 로드
load_dotenv(dotenv_path='.env.txt')

def get_connection():
    try:
        return psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT"),
            sslmode='require'  # Supabase는 SSL 필수
        )
    except Exception as e:
        print("❌ 데이터베이스 연결 실패:", str(e))
        return None
