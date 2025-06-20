import os
import requests
import uuid

# 환경변수에서 Supabase 정보 불러오기
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "uploads")

def upload_to_supabase(file_data, filename, content_type):
    """Supabase Storage에 파일 업로드 후 공개 URL 반환"""
    try:
        # 고유한 경로를 가진 파일명 생성 (예: uploads/uuid.jpg)
        path = f"{uuid.uuid4().hex}_{filename}"
        url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{path}"

        headers = {
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": content_type
        }

        response = requests.post(url, headers=headers, data=file_data)

        if response.status_code in [200, 201]:
            public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{path}"
            print(f"[✅ Supabase 업로드 완료] {public_url}")
            return public_url
        else:
            print(f"[❌ Supabase 오류] status={response.status_code}, message={response.text}")
            return None

    except Exception as e:
        print(f"[🚨 예외 발생] {e}")
        return None
