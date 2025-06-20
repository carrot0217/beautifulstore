# supabase_client.py
from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()  # 로컬 실행 시 .env 사용 (Render에서는 무시됨)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
