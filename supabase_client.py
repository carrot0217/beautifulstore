# supabase_client.py

import os
from dotenv import load_dotenv
from supabase import create_client, Client

# 환경 변수 로드 (로컬 실행용)
load_dotenv()

SUPABASE_URL: str = os.environ.get("SUPABASE_URL")
SUPABASE_KEY: str = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL 또는 SUPABASE_KEY 환경변수가 누락되었습니다.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)