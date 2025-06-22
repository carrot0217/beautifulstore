import os
import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify, abort
from werkzeug.utils import secure_filename
from db import get_connection
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
import pandas as pd
import io
import uuid

load_dotenv()

app = Flask(__name__)
app.secret_key = 'your_secret_key'

UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

CATEGORY_LIST = [
    "의류", "일반잡화", "주방용품", "가방/신발", "도서/DVD", "식품/화장품", "가전제품"
]


SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
SUPABASE_BUCKET = os.getenv('SUPABASE_BUCKET')  # ✅ 실제 존재하는 Public 버킷

# Supabase 파일 업로드 함수 (REST API 방식)
def upload_to_supabase(file_data, filename, content_type):
    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{filename}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": content_type,
        "x-upsert": "true"
    }
    response = requests.put(url, data=file_data, headers=headers)
    if response.status_code == 200:
        return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{filename}"
    else:
        print("Upload failed:", response.text)
        return None


# ✅ 상품 이미지 재등록 라우트 추가
@app.route('/admin/items/update_image/<int:item_id>', methods=['POST'])
def update_item_image(item_id):
    if 'user_id' not in session or not session.get('is_admin'):
        return redirect(url_for('login'))

    file = request.files.get('image')
    if not file:
        flash('이미지 파일을 선택하세요.')
        return redirect(url_for('manage_items'))

    filename = f"{uuid.uuid4()}_{secure_filename(file.filename)}"
    content_type = file.content_type
    image_url = upload_to_supabase(file.read(), filename, content_type)

    if image_url:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE items SET image = %s WHERE id = %s", (image_url, item_id))
        conn.commit()
        cur.close(); conn.close()
        flash('✅ 이미지가 재등록되었습니다.')
    else:
        flash('❌ 이미지 업로드에 실패했습니다.')

    return redirect(url_for('manage_items'))

# ----------------------- [일반 사용자] 비동기 신청 처리 -----------------------
@app.route('/user/request/ajax', methods=['POST'])
def user_request_ajax():
    if 'user_id' not in session or session.get('is_admin'):
        return jsonify(success=False, message="로그인이 필요합니다.")
    data = request.get_json()
    item_id = int(data['item_id'])
    quantity = int(data['quantity'])

    conn = get_connection()
    cur = conn.cursor()
    user_id = session['user_id']

    cur.execute("SELECT SUM(quantity) FROM orders WHERE user_id = %s AND item = %s", (user_id, item_id))
    user_total = cur.fetchone()[0] or 0

    cur.execute("SELECT quantity, COALESCE(max_request, 999999) FROM items WHERE id=%s;", (item_id,))
    item = cur.fetchone()

    if not item:
        cur.close(); conn.close()
        return jsonify(success=False, message="상품이 존재하지 않습니다.")

    stock, max_request = item
    if quantity > stock:
        cur.close(); conn.close()
        return jsonify(success=False, message="재고보다 신청 수량이 많습니다.")

    if user_total + quantity > max_request:
        cur.close(); conn.close()
        return jsonify(success=False, message=f"신청 제한: 최대 {max_request}개 (현재 {user_total}개 신청됨)")

    # ✅ store → store_name 으로 수정
    cur.execute("""
        INSERT INTO orders (user_id, item, quantity, wish_date, status, store)
        VALUES (%s, %s, %s, NOW(), %s, %s)
    """, (user_id, item_id, quantity, "대기 중", session.get('store_name')))
    
    cur.execute("UPDATE items SET quantity = quantity - %s WHERE id = %s", (quantity, item_id))
    conn.commit()
    cur.close(); conn.close()

    return jsonify(success=True)

# ----------------------- 배송일입력 -----------------------
@app.route('/admin/update_delivery_date', methods=['POST'])
def update_delivery_date():
    if not session.get('is_admin'):
        return jsonify(success=False, message="관리자만 가능합니다.")

    data = request.get_json()
    order_id = data.get('order_id')
    delivery_date = data.get('delivery_date')

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET delivery_date = %s, status = '완료' WHERE id = %s",
                (delivery_date, order_id))
    conn.commit()
    cur.close(); conn.close()

    return jsonify(success=True)


# ----------------------- 상품 전체 페이지 -----------------------
@app.route('/user/request')
def user_request_form():
    conn = get_connection()
    cur = conn.cursor()

    current_category = request.args.get('category', '전체')

    if current_category == '전체':
        cur.execute("SELECT id, name, unit_price, image, quantity, category, description FROM items ORDER BY id DESC")
    else:
        cur.execute("""
            SELECT id, name, unit_price, image, quantity, category, description
            FROM items
            WHERE category = %s
            ORDER BY id DESC
        """, (current_category,))

    # ✅ 들여쓰기 올바르게 수정
    items = [
        {
            "id": row[0],
            "name": row[1],
            "price": row[2],
            "image": row[3],
            "quantity": row[4],
            "category": row[5],
            "description": row[6],
            "image_url": row[3] if row[3] else "/static/img/noimage.png"
        }
        for row in cur.fetchall()
    ]

    cur.close()
    conn.close()

    return render_template("user_request_form.html", items=items, categories=CATEGORY_LIST, current_category=current_category)



# ----------------------- 관리자 전용 주문삭제  -----------------------
@app.route('/admin/delete_order', methods=['POST'])
def admin_delete_order():
    if not session.get('is_admin'):
        return jsonify(success=False, message='관리자 권한이 필요합니다.')

    data = request.get_json()
    order_id = data.get('order_id')
    reason = data.get('reason')

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE orders
        SET status = '삭제됨', cancel_reason = %s
        WHERE id = %s
    """, (reason, order_id))
    conn.commit()
    cur.close(); conn.close()

    return jsonify(success=True)



# ----------------------- 상품 등록/수정/삭제 -----------------------
@app.route('/admin/items', methods=['GET', 'POST'])
def manage_items():
    if not session.get('is_admin'):
        return redirect(url_for('dashboard'))

    conn = get_connection()
    cur = conn.cursor()
    message = request.args.get('message')

    if request.method == 'POST':
        name = request.form['name']
        description = request.form.get('description', '')
        stock = int(request.form.get('stock', 0))
        unit_price = float(request.form.get('unit_price', 0))
        max_request = request.form.get('max_request')
        max_request = int(max_request) if max_request else None
        category = request.form.get('category') or request.form.get('custom-category') or ''

        image_url = None
        if 'image' in request.files and request.files['image']:
            file = request.files['image']
            if file and file.filename:
                ext = os.path.splitext(file.filename)[1]
                unique_filename = f"{uuid.uuid4().hex}{ext}"
                content_type = file.content_type
                file_data = file.read()
                image_url = upload_to_supabase(file_data, file.filename, content_type)

        cur.execute("""
            INSERT INTO items (name, description, quantity, unit_price, category, image, max_request)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (name, description, stock, unit_price, category, image_url, max_request))
        conn.commit()
        cur.close(); conn.close()
        return redirect(url_for('manage_items', message='added'))

    cur.execute("""
        SELECT id, name, description, quantity, unit_price, category, image, COALESCE(max_request, 0)
        FROM items ORDER BY id ASC
    """)
    items = cur.fetchall()
    cur.close(); conn.close()
    return render_template('admin_items.html', items=items, message=message, categories=CATEGORY_LIST)


@app.route('/admin/items/edit/<int:item_id>', methods=['POST'])
def edit_item(item_id):
    conn = get_connection()
    cur = conn.cursor()

    name = request.form['name']
    description = request.form.get('description', '')
    stock = int(request.form.get('stock', 0))
    unit_price = float(request.form.get('unit_price', 0))
    max_request = request.form.get('max_request')
    max_request = int(max_request) if max_request else None
    category = request.form.get('category', '')

    image_url = None
    file = request.files.get('image')

    if file and file.filename != '':
        ext = os.path.splitext(file.filename)[1]
        unique_filename = f"{uuid.uuid4().hex}{ext}"
        content_type = file.content_type
        file_data = file.read()
        image_url = upload_to_supabase(file_data, unique_filename, content_type)

        print(f"[이미지 URL 생성됨] → {image_url}")  # ✅ 로그 추가

        # DB에 이미지 포함 업데이트
        cur.execute("""
            UPDATE items
            SET name=%s, description=%s, quantity=%s, unit_price=%s,
                category=%s, max_request=%s, image=%s
            WHERE id=%s
        """, (name, description, stock, unit_price, category, max_request, image_url, item_id))
    else:
        print("[이미지 없음] 기존 이미지 유지")  # ✅ 로그 추가

        # DB에 이미지 제외 업데이트
        cur.execute("""
            UPDATE items
            SET name=%s, description=%s, quantity=%s, unit_price=%s,
                category=%s, max_request=%s
            WHERE id=%s
        """, (name, description, stock, unit_price, category, max_request, item_id))

    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('manage_items', message='updated'))



@app.route('/admin/items/delete/<int:item_id>', methods=['POST'])
def delete_item(item_id):
    if not session.get('is_admin'):
        return redirect(url_for('dashboard'))
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
    conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('manage_items', message='deleted'))

# -------------------- 로그인 라우트 --------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user_id = request.form.get('user_id')
        password = request.form.get('password')

        if not user_id or not password:
            flash('❗ 아이디와 비밀번호를 모두 입력해주세요.')
            return render_template('login.html')

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT password, is_admin, store, store_name FROM users WHERE username = %s", (user_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()

        if result:
            db_password, is_admin, store, store_name = result
            print("회원 로그인 - 매장명:", store)  # 확인위한 debug print
            if password == db_password:
                session['user_id'] = user_id
                session['is_admin'] = is_admin
                session['store_name'] = store_name  # 대입 값 확인

                if is_admin:
                    return redirect(url_for('dashboard'))
                else:
                    return redirect(url_for('user_home'))
            else:
                flash('❌ 비밀번호가 일치하지 않습니다.')
        else:
            flash('❌ 존재하지 않는 사용자입니다.')

    return render_template('login.html')

# -------------------- 로그아웃 라우트 --------------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ----------------------- 관리자 대시보드 -----------------------
@app.route('/dashboard')
def dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    username = session['user_id']
    conn = get_connection()
    cur = conn.cursor()

    # 받은 쪽지
    cur.execute("""
        SELECT sender, content, TO_CHAR(timestamp, 'YYYY-MM-DD HH24:MI')
        FROM messages
        WHERE recipient = %s
        ORDER BY timestamp DESC
    """, (username,))
    messages = cur.fetchall()

    # 받는 사람 목록
    cur.execute("""
        SELECT username, store_name
        FROM users
        WHERE username != %s AND store_name IS NOT NULL AND store_name != ''
        ORDER BY username
    """, (username,))
    recipients = cur.fetchall()

    cur.close()
    conn.close()

    return render_template('admin_dashboard.html', messages=messages, recipients=recipients)

# ----------------------- 관리자 주문 목록 조회 -----------------------
@app.route('/admin/orders')
def admin_orders():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    status = request.args.get('status', '전체')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    keyword = request.args.get('keyword', '').strip()
    page = int(request.args.get('page', 1))
    per_page = 5
    offset = (page - 1) * per_page

    query = """ 
        SELECT o.id, o.wish_date, o.store, i.name, o.quantity, o.status, 
               o.delivery_date, o.comment, o.cancel_reason
        FROM orders o
        JOIN items i ON CAST(o.item AS INTEGER) = i.id
        WHERE 1=1
    """
    params = []

    if status and status != '전체':
        query += " AND o.status = %s"
        params.append(status)
    if start_date:
        query += " AND o.wish_date >= %s"
        params.append(start_date)
    if end_date:
        query += " AND o.wish_date <= %s"
        params.append(end_date)
    if keyword:
        query += " AND (i.name ILIKE %s OR o.store ILIKE %s)"
        params += [f"%{keyword}%", f"%{keyword}%"]

    query += " ORDER BY o.wish_date DESC, o.id DESC"

    conn = get_connection()
    cur = conn.cursor()

    # 총 개수 계산용 쿼리
    count_query = "SELECT COUNT(*) FROM (" + query.replace(
        "SELECT o.id, o.wish_date, o.store, i.name, o.quantity, o.status, o.delivery_date, o.comment, o.cancel_reason",
        "SELECT 1") + ") AS sub"
    cur.execute(count_query, params)
    total = cur.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    query += f" LIMIT {per_page} OFFSET {offset}"
    cur.execute(query, params)
    orders = cur.fetchall()
    cur.close(); conn.close()

    now = date.today()
    status_choices = ['전체', '대기 중', '배송중', '완료', '취소됨', '취소됨(재고부족)', '삭제됨']

    return render_template(
        'admin_orders.html',
        orders=orders,
        now=now,
        status=status,
        start_date=start_date,
        end_date=end_date,
        keyword=keyword,
        page=page,
        total_pages=total_pages,
        status_choices=status_choices
    )

# ----------------------- 관리자 주문 코멘트 수정 -----------------------
@app.route('/admin/orders/comment/<int:order_id>', methods=['POST'])
def update_order_comment(order_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    comment = request.form.get('comment')
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET comment = %s WHERE id = %s", (comment, order_id))
    conn.commit()
    cur.close(); conn.close()
    flash('코멘트가 저장되었습니다!')
    return redirect(url_for('admin_orders'))

# ----------------------- 관리자 배송일 입력 및 완료 처리 -----------------------
@app.route('/admin/orders/delivery/<int:order_id>', methods=['POST'])
def set_delivery(order_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    delivery_date = request.form.get('delivery_date') or None
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET delivery_date = %s, status = '완료' WHERE id = %s", (delivery_date, order_id))
    conn.commit()
    cur.close(); conn.close()
    flash('배송일이 입력되었고, 주문이 완료 처리되었습니다!')
    return redirect(url_for('admin_orders'))

# ----------------------- 관리자 주문 취소 (사유 선택) -----------------------
@app.route('/admin/orders/cancel/<int:order_id>', methods=['POST'])
def admin_cancel_order(order_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    reason_type = request.form.get('reason_type')
    custom_reason = request.form.get('custom_reason', '').strip()
    reason = reason_type if reason_type == '재고부족' else custom_reason

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT status, item, quantity FROM orders WHERE id = %s", (order_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        flash('주문이 존재하지 않습니다.')
        return redirect(url_for('admin_orders'))

    status, item_id, quantity = row
    if status == '대기 중':
        cur.execute("UPDATE items SET quantity = quantity + %s WHERE id = %s", (quantity, item_id))

    cur.execute("UPDATE orders SET status = %s, cancel_reason = %s WHERE id = %s",
                ('취소됨', reason, order_id))
    conn.commit()
    cur.close(); conn.close()
    flash('주문이 취소되었습니다.')
    return redirect(url_for('admin_orders'))
# ----------------------- 주문취소 사 -----------------------
@app.route('/user/orders/cancel_ajax/<int:order_id>', methods=['POST'])
def cancel_order_ajax(order_id):
    if 'user_id' not in session:
        return jsonify(success=False, message='로그인이 필요합니다.')

    user_id = session['user_id']
    data = request.get_json()
    reason = data.get('reason', '').strip()

    if not reason:
        return jsonify(success=False, message='사유가 비어있습니다.')

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT status, user_id FROM orders WHERE id = %s", (order_id,))
    row = cur.fetchone()

    if not row or row[1] != user_id:
        cur.close(); conn.close()
        return jsonify(success=False, message='권한이 없습니다.')

    if row[0].startswith('취소') or row[0] == '삭제됨':
        cur.close(); conn.close()
        return jsonify(success=False, message='이미 취소된 주문입니다.')

    # ✅ 이 부분을 정확히 고쳤습니다!
    cur.execute("""
        UPDATE orders SET status = '취소됨(사용자)', cancel_reason = %s WHERE id = %s
    """, (reason, order_id))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify(success=True)


# ----------------------- 완료 주문 삭제 -----------------------
@app.route('/admin/orders/delete_completed_30days', methods=['POST'])
def delete_completed_orders_30days():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM orders 
        WHERE status = '완료'
          AND (delivery_date IS NULL OR delivery_date < CURRENT_DATE - INTERVAL '30 days')
    """)
    deleted = cur.rowcount
    conn.commit()
    cur.close(); conn.close()
    flash(f'30일 경과 완료 주문 {deleted}건이 삭제되었습니다!')
    return redirect(url_for('admin_orders'))

@app.route('/admin/orders/delete_completed_all', methods=['POST'])
def delete_completed_orders_all():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM orders WHERE status = '완료'")
    deleted = cur.rowcount
    conn.commit()
    cur.close(); conn.close()
    flash(f'모든 완료 주문 {deleted}건이 삭제되었습니다!')
    return redirect(url_for('admin_orders'))
# ----------------------- 관리자 주문내역 일괄 삭제 -----------------------

@app.route('/admin/delete_bulk', methods=['POST'])
def delete_bulk_orders():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    order_ids = request.form.getlist('order_ids')
    if not order_ids:
        flash("삭제할 주문을 선택해주세요.")
        return redirect(url_for('admin_orders'))

    try:
        conn = get_connection()
        cur = conn.cursor()

        # 🔧 문자열 리스트를 정수 리스트로 변환
        ids_int = list(map(int, order_ids))

        # ✅ 실제로 삭제 수행
        query = "DELETE FROM orders WHERE id = ANY(%s)"
        cur.execute(query, (ids_int,))
        conn.commit()

        flash(f"{len(ids_int)}건의 주문이 완전히 삭제되었습니다.")
    except Exception as e:
        conn.rollback()
        flash("삭제 실패: " + str(e))
    finally:
        cur.close()
        conn.close()

    return redirect(url_for('admin_orders'))


# ----------------------- 사용자 주문 이력 페이지 -----------------------
@app.route('/user/orders')
def user_orders():
    if 'user_id' not in session or session.get('is_admin'):
        return redirect(url_for('login'))

    user_id = session['user_id']
    page = int(request.args.get('page', 1))
    per_page = 10
    offset = (page - 1) * per_page

    conn = get_connection()
    cur = conn.cursor()

    # 전체 주문 수 (현재 로그인한 사용자 기준)
    cur.execute("""
        SELECT COUNT(*)
        FROM orders o
        JOIN items i ON CAST(o.item AS INTEGER) = i.id
        WHERE o.user_id = %s
    """, (user_id,))
    total_orders = cur.fetchone()[0]
    total_pages = (total_orders - 1) // per_page + 1 if total_orders > 0 else 1

    # 주문 목록 조회 (해당 사용자만)
    cur.execute("""
        SELECT o.wish_date, i.name, o.quantity, o.status, 
               o.delivery_date, o.id, o.cancel_reason
        FROM orders o
        JOIN items i ON CAST(o.item AS INTEGER) = i.id
        WHERE o.user_id = %s
        ORDER BY o.wish_date DESC
        LIMIT %s OFFSET %s
    """, (user_id, per_page, offset))
    rows = cur.fetchall()

    cur.close()
    conn.close()

    # 데이터 정리
    orders = []
    for row in rows:
        wish_date, item_name, quantity, status, delivery_date, order_id, cancel_reason = row
        if isinstance(delivery_date, str):
            try:
                delivery_date = datetime.strptime(delivery_date, "%Y-%m-%d").date()
            except:
                delivery_date = None
        orders.append({
            "id": order_id,
            "wish_date": wish_date,
            "item_name": item_name,
            "quantity": quantity,
            "status": status,
            "delivery_date": delivery_date,
            "cancel_reason": cancel_reason
        })

    # 품목 리스트 필터용
    item_list = list(sorted(set([o["item_name"] for o in orders])))
    today = date.today()

    return render_template(
        'user_orders.html',
        orders=orders,
        today=today,
        item_list=item_list,
        current_page=page,
        total_pages=total_pages,
        request=request
    )

# ----------------------- 쪽지 보내기 (AJAX용) -----------------------
@app.route('/messages/send', methods=['POST'])
def send_message_ajax():
    if 'user_id' not in session:
        return jsonify(success=False, message="로그인이 필요합니다.")

    data = request.get_json()
    sender = session['user_id']
    recipient = data.get('to')
    content = data.get('content', '').strip()

    if not recipient or not content:
        return jsonify(success=False, message="받는 사람과 내용을 모두 입력해주세요.")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO messages (sender, recipient, content)
        VALUES (%s, %s, %s)
    """, (sender, recipient, content))
    conn.commit()
    cur.close(); conn.close()

    return jsonify(success=True, message="쪽지가 전송되었습니다.")


# ----------------------- 쪽지 수신자 목록 (AJAX용) -----------------------
@app.route('/messages/recipients')
def get_message_recipients():
    if 'user_id' not in session:
        return jsonify(success=False, users=[])

    username = session['user_id']

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT username, store_name, is_admin
        FROM users
        WHERE username != %s AND store_name IS NOT NULL AND store_name != ''
        ORDER BY store_name
    """, (username,))
    rows = cur.fetchall()
    cur.close(); conn.close()

    users = [{
        "username": row[0],
        "store": row[1],
        "is_admin": row[2]
    } for row in rows]
    return jsonify(success=True, users=users)
# ----------------------- 사용자 주문 취소 -----------------------
@app.route('/user/orders/cancel/<int:order_id>', methods=['POST'])
def cancel_user_order(order_id):
    if 'user_id' not in session or session.get('is_admin'):
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id, status, quantity, item FROM orders WHERE id = %s", (order_id,))
    row = cur.fetchone()

    if not row:
        cur.close(); conn.close()
        flash('주문이 존재하지 않습니다.')
        return redirect(url_for('user_orders'))

    order_user_id, status, quantity, item_id = row
    if order_user_id != user_id:
        cur.close(); conn.close()
        abort(403)
    if status != '대기 중':
        cur.close(); conn.close()
        flash('이미 처리된 주문은 취소할 수 없습니다.')
        return redirect(url_for('user_orders'))

    cur.execute("UPDATE items SET quantity = quantity + %s WHERE id = %s", (quantity, item_id))
    cur.execute("UPDATE orders SET status = %s, cancel_reason = %s WHERE id = %s",
                ('취소됨', '사용자 직접 취소', order_id))
    conn.commit()
    cur.close(); conn.close()

    flash('주문이 취소되었습니다.')
    return redirect(url_for('user_orders'))

# ----------------------- 사용자 주문 삭제 (취소된 주문만) -----------------------
@app.route('/user/orders/delete/<int:order_id>', methods=['POST'])
def delete_user_order(order_id):
    if 'user_id' not in session or session.get('is_admin'):
        return jsonify(success=False, message='로그인이 필요합니다.')

    user_id = session['user_id']
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id, status FROM orders WHERE id = %s", (order_id,))
    row = cur.fetchone()

    if not row:
        cur.close(); conn.close()
        return jsonify(success=False, message='주문이 존재하지 않습니다.')

    order_user_id, status = row
    if order_user_id != user_id:
        cur.close(); conn.close()
        return jsonify(success=False, message='권한이 없습니다.')

    # 취소된 주문만 삭제 가능
    if status not in ('취소됨', '취소됨(사용자)', '취소됨(재고부족)', '삭제됨'):
        cur.close(); conn.close()
        return jsonify(success=False, message='취소된 주문만 삭제할 수 있습니다.')

    # 실제 삭제 수행
    cur.execute("DELETE FROM orders WHERE id = %s", (order_id,))
    conn.commit()
    cur.close(); conn.close()

    return jsonify(success=True)

# ----------------------- 사용자 주문 엑셀다운로드 -----------------------
@app.route('/user/orders/download')
def download_user_orders():
    if 'user_id' not in session or session.get('is_admin'):
        return redirect(url_for('login'))

    user_id = session['user_id']

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT o.created_at, i.name, o.quantity, o.status, o.delivery_date
        FROM orders o
        JOIN items i ON CAST(o.item AS INTEGER) = i.id
        WHERE o.user_id = %s
        ORDER BY o.created_at DESC
    """, (user_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()

    # DataFrame 변환
    df = pd.DataFrame(rows, columns=['신청일', '상품명', '수량', '상태', '배송일'])

    # 엑셀로 저장
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='주문내역')

    output.seek(0)
    filename = f"{user_id}_orders.xlsx"
    return send_file(output, download_name=filename, as_attachment=True)


# ----------------------- 사용자 홈 → 대시보드 이동 라우트 -----------------------
@app.route('/user/home')
def user_home():
    if 'user_id' not in session or session.get('is_admin'):
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_connection()
    cur = conn.cursor()

    # 사용자 정보 가져오기
    cur.execute("SELECT store_name FROM users WHERE id = %s", (user_id,))
    store_name_row = cur.fetchone()
    store_name = store_name_row[0] if store_name_row else ''

    # 입고 일정
    cur.execute("""
        SELECT o.order_date, i.name, o.quantity, o.wish_date
        FROM orders o
        JOIN items i ON o.item = i.id
        WHERE o.store_name = %s AND o.status = '완료'
        ORDER BY o.wish_date DESC
        LIMIT 3
    """, (store_name,))
    upcoming_orders = cur.fetchall()

    # 최근 주문 이력 (최근 3일)
    cur.execute("""
        SELECT o.order_date, i.name, o.quantity, o.status, o.reason
        FROM orders o
        JOIN items i ON o.item = i.id
        WHERE o.store_name = %s AND o.order_date >= CURRENT_DATE - INTERVAL '3 days'
        ORDER BY o.order_date DESC
        LIMIT 5
    """, (store_name,))
    recent_orders = cur.fetchall()

    # 쪽지함 (받은 쪽지)
    cur.execute("""
        SELECT m.id, u.store_name, m.message, m.timestamp
        FROM messages m
        JOIN users u ON m.sender = u.id
        WHERE m.recipient = %s
        ORDER BY m.timestamp DESC
        LIMIT 5
    """, (user_id,))
    messages = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("user_home.html",
                           user_name=session.get('user_name', ''),
                           store_name=store_name,
                           upcoming_orders=upcoming_orders,
                           recent_orders=recent_orders,
                           messages=messages)

# ----------------------- 관리자페이지 매장 수정라우트 ----------------------
@app.route('/admin/users/<int:user_id>/edit_store', methods=['POST'])
def edit_user_store(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    store_name = request.form.get('store_name')

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
        SET store_name = %s
        WHERE id = %s
    """, (store_name, user_id))
    conn.commit()
    cur.close()
    conn.close()

    flash("매장명이 수정되었습니다.")
    return redirect(url_for('manage_users'))
# ----------------------- 관리자페이지 매장명 수정 -----------------------
@app.route('/admin/users/update', methods=['POST'])
def update_store_name():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    username = request.form.get('username')
    store_name = request.form.get('store_name')

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
        SET store_name = %s
        WHERE username = %s
    """, (store_name, username))
    conn.commit()
    cur.close()
    conn.close()

    flash("매장명이 수정되었습니다.")
    return redirect(url_for('manage_users'))
# ----------------------- 입고일 -----------------------
@app.route("/user/schedule")
def user_schedule():
    user_id = session.get('user_id')
    if not user_id or session.get('is_admin'):
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    today = datetime.today().date()
    seven_days_later = today + timedelta(days=7)

    # ✅ CAST로 형 변환하여 integer = integer 비교
    cur.execute("""
        SELECT i.name, o.quantity, o.delivery_date
        FROM orders o
        JOIN items i ON CAST(o.item AS INTEGER) = i.id
        WHERE o.status = '완료'
          AND o.user_id = %s
          AND o.delivery_date BETWEEN %s AND %s
        ORDER BY o.delivery_date ASC
    """, (user_id, today, seven_days_later))

    schedule = [
        {
            "name": row[0],
            "quantity": row[1],
            "delivery_date": row[2].strftime("%Y-%m-%d") if row[2] else ""
        }
        for row in cur.fetchall()
    ]

    cur.close()
    conn.close()

    return render_template("user_schedule.html", schedule=schedule)
# ----------------------- 상품 상세 정보 AJAX -----------------------
@app.route('/user/item/<int:item_id>')
def get_item_details(item_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT name, description, quantity, unit_price, COALESCE(max_request, 9999)
        FROM items WHERE id = %s
    """, (item_id,))
    row = cur.fetchone()
    cur.close(); conn.close()

    if not row:
        return jsonify(success=False)


    return jsonify({
        "name": row[0],
        "description": row[1],
        "stock": row[2],
        "cost": row[3],
        "limit_qty": row[4]
    })

# ----------------------- 관리자 통계 페이지 -----------------------
@app.route('/admin/stats')
def admin_stats():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    conn = get_connection()
    cur = conn.cursor()

    # 주문 상태별 통계
    query = "SELECT status, COUNT(*) FROM orders WHERE 1=1"
    params = []
    if start_date and end_date:
        query += " AND wish_date BETWEEN %s AND %s"
        params = [start_date, end_date]
    query += " GROUP BY status"

    cur.execute(query, params)
    stats = cur.fetchall()
    labels = [row[0] for row in stats]
    values = [row[1] for row in stats]

    cur.close(); conn.close()
    return render_template('admin_stats.html', labels=labels, values=values)

# ----------------------- 관리자 통계 다운로드 -----------------------
@app.route('/admin/stats/download')
def download_stats():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    conn = get_connection()
    cur = conn.cursor()
    query = """
        SELECT o.wish_date, i.name, o.quantity, o.status, o.delivery_date, o.store
        FROM orders o
        JOIN items i ON CAST(o.item AS INTEGER) = i.id
        WHERE 1=1
    """
    params = []

    if start_date and end_date:
        query += " AND o.wish_date BETWEEN %s AND %s"
        params.extend([start_date, end_date])

    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close(); conn.close()

    df = pd.DataFrame(rows, columns=['신청일자', '상품명', '수량', '상태', '배송일', '매장'])
    df['신청일자'] = pd.to_datetime(df['신청일자']).dt.strftime("%Y-%m-%d")
    df['배송일'] = pd.to_datetime(df['배송일'], errors='coerce').dt.strftime("%Y-%m-%d")

    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    df.to_excel(writer, index=False, sheet_name='주문통계')
    writer.close()
    output.seek(0)

    now = datetime.now().strftime("%Y%m%d")
    return send_file(output, as_attachment=True,
                     download_name=f"order_stats_{now}.xlsx",
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
# ----------------------- 관리자 홈 -----------------------
@app.route('/admin/home')
def admin_home():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    return render_template('admin_home.html')

# ----------------------- 활동로그 -----------------------
@app.route('/admin/logs')
def admin_logs():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    # 필터링 파라미터 가져오기
    actor = request.args.get('actor', '')
    target = request.args.get('target', '')
    action = request.args.get('action', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    query = """
        SELECT action, actor, target, timestamp
        FROM activity_logs
        WHERE 1=1
    """
    params = []

    if actor:
        query += " AND actor ILIKE %s"
        params.append(f"%{actor}%")
    if target:
        query += " AND target ILIKE %s"
        params.append(f"%{target}%")
    if action:
        query += " AND action = %s"
        params.append(action)
    if start_date:
        query += " AND timestamp >= %s"
        params.append(start_date)
    if end_date:
        query += " AND timestamp <= %s"
        params.append(end_date)

    query += " ORDER BY timestamp DESC"
    cur.execute(query, params)
    logs = cur.fetchall()

    # 일별 로그 수
    cur.execute("""
        SELECT TO_CHAR(timestamp::date, 'YYYY-MM-DD') AS log_date, COUNT(*) 
        FROM activity_logs 
        GROUP BY log_date 
        ORDER BY log_date
    """)
    daily_data = [{'date': row[0], 'count': row[1]} for row in cur.fetchall()]

    # 행동별 로그 비율
    cur.execute("""
        SELECT action, COUNT(*) 
        FROM activity_logs 
        GROUP BY action 
        ORDER BY COUNT(*) DESC
    """)
    action_data = [{'action': row[0], 'count': row[1]} for row in cur.fetchall()]
    cur.close(); conn.close()

    return render_template(
        'admin_logs.html',
        logs=logs,
        daily_data=daily_data,
        action_data=action_data
    )
# ----------------------- 다운로드 로그 -----------------------
@app.route('/admin/logs/download')
def download_logs():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    # 필터 적용 동일하게
    actor = request.args.get('actor', '')
    target = request.args.get('target', '')
    action = request.args.get('action', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    query = """
        SELECT action, actor, target, timestamp
        FROM activity_logs
        WHERE 1=1
    """
    params = []

    if actor:
        query += " AND actor ILIKE %s"
        params.append(f"%{actor}%")
    if target:
        query += " AND target ILIKE %s"
        params.append(f"%{target}%")
    if action:
        query += " AND action = %s"
        params.append(action)
    if start_date:
        query += " AND timestamp >= %s"
        params.append(start_date)
    if end_date:
        query += " AND timestamp <= %s"
        params.append(end_date)

    query += " ORDER BY timestamp DESC"
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close(); conn.close()

    df = pd.DataFrame(rows, columns=["행동", "행위자", "대상자", "시간"])
    df['시간'] = pd.to_datetime(df['시간']).dt.strftime("%Y-%m-%d %H:%M:%S")

    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    df.to_excel(writer, index=False, sheet_name='활동로그')
    writer.close()
    output.seek(0)

    now = datetime.now().strftime('%Y%m%d')
    return send_file(output, as_attachment=True,
                     download_name=f"admin_logs_{now}.xlsx",
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
# ----------------------- 전체 쪽지 관리 (관리자) -----------------------
@app.route('/admin/messages')
def admin_messages():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, sender, receiver, content, timestamp
        FROM messages
        ORDER BY timestamp DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    messages_list = [
        {
            'id': row[0],
            'sender': row[1],
            'receiver': row[2],
            'content': row[3],
            'timestamp': row[4].strftime("%Y-%m-%d %H:%M")
        }
        for row in rows
    ]

    return render_template('admin_messages.html', messages_list=messages_list)


# ----------------------- 모든 쪽지 삭제 -----------------------
@app.route('/messages/delete_all', methods=['POST'])
def delete_all_messages():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM messages")
    conn.commit()
    cur.close()
    conn.close()

    flash("모든 쪽지가 삭제되었습니다.")
    return redirect(url_for('admin_messages'))


# ----------------------- 쪽지 1건 삭제 (AJAX 요청용) -----------------------
@app.route('/messages/delete', methods=['POST'])
def delete_single_message():
    if not session.get('is_admin'):
        return jsonify(success=False, message="권한이 없습니다.")

    data = request.get_json()
    msg_id = data.get('id')

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM messages WHERE id = %s", (msg_id,))
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    if deleted:
        return jsonify(success=True, message="쪽지가 삭제되었습니다.")
    else:
        return jsonify(success=False, message="쪽지를 찾을 수 없습니다.")


# ----------------------- 쪽지함 연결 -----------------------
# 받은 쪽지 보기
@app.route('/user/messages')
def inbox():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    username = session['user_id']  # 세션 변수명이 user_id이지만 실제로는 username을 담고 있음
    conn = get_connection()
    cur = conn.cursor()

    # 받은 쪽지 목록
    cur.execute("""
        SELECT sender, content, TO_CHAR(timestamp, 'YYYY-MM-DD HH24:MI')
        FROM messages
        WHERE recipient = %s
        ORDER BY timestamp DESC
    """, (username,))
    messages = cur.fetchall()

    # 받는 사람 목록 (username + store_name)
    cur.execute("""
        SELECT username, store_name
        FROM users
        WHERE username != %s AND store_name IS NOT NULL AND store_name != ''
        ORDER BY username
    """, (username,))
    recipients = cur.fetchall()

    cur.close()
    conn.close()

    return render_template('inbox.html', messages=messages, recipients=recipients)


# 쪽지 상세 보기
@app.route('/user/messages/<int:msg_id>')
def message_detail(msg_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    username = session['user_id']
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT sender, content, TO_CHAR(timestamp, 'YYYY-MM-DD HH24:MI')
        FROM messages
        WHERE id = %s AND recipient = %s
    """, (msg_id, username))
    msg = cur.fetchone()

    # 읽음 처리
    cur.execute("UPDATE messages SET is_read = TRUE WHERE id = %s", (msg_id,))
    conn.commit()
    cur.close()
    conn.close()

    if msg:
        return render_template('message_detail.html', message=msg)
    else:
        return "쪽지를 찾을 수 없습니다.", 404


# 쪽지 보내기
@app.route('/user/messages/send', methods=['GET', 'POST'])
def send_message():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    username = session['user_id']

    if request.method == 'POST':
        sender = username
        recipient = request.form['recipient']
        content = request.form['content']

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO messages (sender, recipient, content)
            VALUES (%s, %s, %s)
        """, (sender, recipient, content))
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('inbox'))

    # GET 요청 시 받는 사람 목록 전달
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT username, store_name
        FROM users
        WHERE username != %s AND store_name IS NOT NULL AND store_name != ''
        ORDER BY username
    """, (username,))
    recipients = cur.fetchall()
    cur.close()
    conn.close()

    return render_template('send_message.html', recipients=recipients)

# ----------------------- 관리자 대시보드 쪽지 -----------------------
@app.route('/messages/recipients')
def message_recipients():
    if 'user_id' not in session:
        return jsonify(success=False, users=[])

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT username, store, is_admin
        FROM users
        WHERE username != %s AND store IS NOT NULL AND store != ''
        ORDER BY store
    """, (session['user_id'],))
    rows = cur.fetchall()
    cur.close(); conn.close()

    users = [{"username": row[0], "store": row[1], "is_admin": row[2]} for row in rows]
    return jsonify(success=True, users=users)


# ----------------------- 아카이브 -----------------------

@app.route('/admin/archive/view')
def archive_orders_view():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    item_name = request.args.get('item_name')
    status = request.args.get('status')

    conn = get_connection()
    cur = conn.cursor()

    query = """
        SELECT o.wish_date, i.name, o.quantity, o.status, o.delivery_date
        FROM orders o
        JOIN items i ON CAST(o.item AS INTEGER) = i.id
        WHERE o.status = '완료'
    """
    params = []

    if start_date:
        query += " AND o.wish_date >= %s"
        params.append(start_date)
    if end_date:
        query += " AND o.wish_date <= %s"
        params.append(end_date)
    if item_name:
        query += " AND i.name = %s"
        params.append(item_name)
    if status:
        query += " AND o.status = %s"
        params.append(status)

    query += " ORDER BY o.wish_date DESC"

    cur.execute(query, params)
    orders = cur.fetchall()

    archive_orders = [
        {
            "wish_date": row[0].strftime('%Y-%m-%d'),
            "item_name": row[1],
            "quantity": row[2],
            "status": row[3],
            "delivery_date": row[4].strftime('%Y-%m-%d') if row[4] else None
        }
        for row in orders
    ]

    # 품목명 목록
    cur.execute("SELECT DISTINCT name FROM items")
    item_names = [row[0] for row in cur.fetchall()]

    cur.close(); conn.close()

    return render_template("archive_orders.html", archive_orders=archive_orders, item_names=item_names)


@app.route('/admin/archive/download')
def download_archive():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    item_name = request.args.get('item_name')
    status = request.args.get('status')

    conn = get_connection()
    cur = conn.cursor()

    query = """
        SELECT o.wish_date, i.name, o.quantity, o.status, o.delivery_date
        FROM orders o
        JOIN items i ON CAST(o.item AS INTEGER) = i.id
        WHERE o.status = '완료'
    """
    params = []

    if start_date:
        query += " AND o.wish_date >= %s"
        params.append(start_date)
    if end_date:
        query += " AND o.wish_date <= %s"
        params.append(end_date)
    if item_name:
        query += " AND i.name = %s"
        params.append(item_name)
    if status:
        query += " AND o.status = %s"
        params.append(status)

    query += " ORDER BY o.wish_date DESC"
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close(); conn.close()

    df = pd.DataFrame(rows, columns=['신청일자', '상품명', '수량', '상태', '배송일'])
    df['신청일자'] = pd.to_datetime(df['신청일자']).dt.strftime('%Y-%m-%d')
    df['배송일'] = pd.to_datetime(df['배송일'], errors='coerce').dt.strftime('%Y-%m-%d')

    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    df.to_excel(writer, index=False, sheet_name='아카이브 주문')
    writer.close()
    output.seek(0)

    today = datetime.now().strftime('%Y%m%d')
    return send_file(output, as_attachment=True, download_name=f"archived_orders_{today}.xlsx", mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
# ----------------------- 사용자관리 -----------------------
@app.route('/admin/users', methods=['GET', 'POST'])
def manage_users():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    keyword = request.args.get('keyword', '').strip()
    if keyword:
        cur.execute("""
            SELECT id, username, is_admin, store_name
            FROM users
            WHERE username ILIKE %s OR store_name ILIKE %s
            ORDER BY id ASC
        """, (f'%{keyword}%', f'%{keyword}%'))
    else:
        cur.execute("SELECT id, username, is_admin, store_name FROM users ORDER BY id ASC")

    users = cur.fetchall()

    # 등록 처리
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        store = request.form.get('store')
        is_admin = True if request.form.get('is_admin') else False

        if not username or not password or not store:
            flash("❗ 모든 필드를 입력해주세요.")
        else:
            # 중복 확인
            cur.execute("SELECT * FROM users WHERE username = %s", (username,))
            if cur.fetchone():
                flash("❌ 이미 존재하는 사용자입니다.")
            else:
                cur.execute("""
                    INSERT INTO users (username, password, store, store_name, is_admin)
                    VALUES (%s, %s, %s, %s, %s)
                """, (username, password, store, store, is_admin))
                conn.commit()
                flash("✅ 사용자 등록 완료")
                return redirect(url_for('manage_users'))

    cur.close()
    conn.close()
    return render_template('admin_users.html', users=users, keyword=keyword)

# 관리자 권한 토글
@app.route('/admin/users/toggle_admin/<int:user_id>', methods=['POST'])
def toggle_admin(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
    current = cur.fetchone()
    if current:
        new_status = not current[0]
        cur.execute("UPDATE users SET is_admin = %s WHERE id = %s", (new_status, user_id))
        conn.commit()
        flash("🔄 권한이 변경되었습니다.")
    cur.close()
    conn.close()
    return redirect(url_for('manage_users'))


# 사용자 삭제
@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()
    flash("🗑️ 사용자가 삭제되었습니다.")
    return redirect(url_for('manage_users'))
# ----------------------- 재고관리 -----------------------
@app.route('/admin/items/chart')
def item_stock_chart():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    conn = get_connection()
    cur = conn.cursor()

    query = """
        SELECT i.name, COALESCE(SUM(o.quantity), 0) AS total_out
        FROM items i
        LEFT JOIN orders o ON i.id = CAST(o.item AS INTEGER)
    """
    params = []

    if start_date and end_date:
        query += " AND o.wish_date BETWEEN %s AND %s"
        params.extend([start_date, end_date])

    query += " GROUP BY i.name ORDER BY i.name"

    cur.execute(query, params)
    results = cur.fetchall()
    cur.close(); conn.close()

    labels = [row[0] for row in results]
    values = [row[1] for row in results]

    return render_template('items_chart.html', labels=labels, values=values)
# ----------------------- 공지 게시판 -----------------------
@app.route('/admin/notices', methods=['GET', 'POST'])
def admin_notices():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        image_file = request.files.get('image')
        image_filename = None

        if image_file and image_file.filename:
            ext = os.path.splitext(image_file.filename)[1]
            image_filename = f"{uuid.uuid4().hex}{ext}"
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
            image_file.save(image_path)

        cur.execute("""
            INSERT INTO notices (title, content, image)
            VALUES (%s, %s, %s)
        """, (title, content, image_filename))

        conn.commit()

    cur.execute("SELECT id, title, image, created_at FROM notices ORDER BY created_at DESC LIMIT 10")
    notices = cur.fetchall()
    cur.close(); conn.close()

    return render_template("admin_notices.html", notices=notices)

# ----------------------- 공지사항 삭제  라우트 ----------------
@app.route('/admin/notices/delete/<int:notice_id>', methods=['POST'])
def delete_notice(notice_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM notices WHERE id = %s", (notice_id,))
    conn.commit()
    cur.close()
    conn.close()

    flash("✅ 공지사항이 삭제되었습니다.")
    return redirect(url_for('admin_notices'))

# ----------------------- 비품등록 처리 라우트 -----------------------
@app.route('/admin/equipment/add', methods=['POST'])
def add_equipment():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    name = request.form['name']

    image_file = request.files.get('image')
    image_filename = None
    if image_file and image_file.filename:
        ext = image_file.filename.split('.')[-1]
        image_filename = f"{uuid.uuid4()}.{ext}"
        image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))

    # HTML 입력 필드에서 단가, 수량, 설명 제거된 상태이므로 기본값 삽입
    default_unit_price = 0
    default_stock = 0

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO equipments (name, unit_price, stock, image_filename)
        VALUES (%s, %s, %s, %s)
    """, (name, default_unit_price, default_stock, image_filename))
    conn.commit()
    cur.close()
    conn.close()

    return redirect(url_for('admin_equipments'))

@app.route('/admin/equipments', methods=['GET', 'POST'])
def manage_equipments():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        name = request.form.get('name')
        file = request.files.get('file')  # ✅ HTML form의 name="file"과 일치

        image_url = None
        if file and file.filename != '':
            filename = str(uuid.uuid4()) + os.path.splitext(file.filename)[1]
            file_data = file.read()
            content_type = file.content_type
            image_url = upload_to_supabase(file_data, filename, content_type)

        cur.execute("""
            INSERT INTO equipments (name, image_url, created_at)
            VALUES (%s, %s, NOW())
        """, (name, image_url))
        conn.commit()

    cur.execute("""
        SELECT id, name, image_url, unit_price, stock, created_at
        FROM equipments ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    equipments = []
    for row in rows:
        equipments.append({
            "id": row[0],
            "name": row[1],
            "image_url": row[2],
            "unit_price": row[3],
            "stock": row[4],
            "created_at": row[5]
        })

    cur.close()
    conn.close()

    return render_template('admin_equipments.html', equipments=equipments)



# --------------------- 사용자 상품 요청 폼 ---------------------
@app.route('/user/items')
def user_items():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    category = request.args.get('category', '전체')

    conn = get_connection()
    cur = conn.cursor()

    if category == '전체':
        cur.execute("SELECT id, name, unit_price, quantity, description, image, category FROM items ORDER BY id DESC")
    else:
        cur.execute("""
            SELECT id, name, unit_price, quantity, description, image, category
            FROM items
            WHERE category = %s
            ORDER BY id DESC
        """, (category,))

    items = [
        {
            "id": row[0],
            "name": row[1],
            "price": row[2] if row[2] is not None else 0,
            "quantity": row[3],
            "description": row[4],
            "image_url": row[5] if row[5] else "/static/img/noimage.png",
            "category": row[6]
        }
        for row in cur.fetchall()
    ]

    cur.execute("SELECT DISTINCT category FROM items ORDER BY category")
    categories = [row[0] for row in cur.fetchall()]

    cur.close()
    conn.close()

    return render_template('user_request_form.html', items=items, categories=categories, current_category=category)
# ----------------------- 이킙먼트 유저 -----------------------
@app.route('/user/equipments')
def user_equipments():
    if 'user_id' not in session or session.get('is_admin'):
        return redirect(url_for('login'))

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, image_url, unit_price, stock
        FROM equipments
        ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    equipments = []
    for row in rows:
        equipments.append({
            "id": row[0],
            "name": row[1],
            "image_url": row[2],
            "unit_price": row[3],
            "stock": row[4]
        })

    cur.close()
    conn.close()

    return render_template("user_equipments.html", equipments=equipments)


@app.route('/user/equipment/request', methods=['POST'])
def user_equipment_request():
    if 'user_id' not in session:
        return jsonify(success=False, message="로그인 필요")

    equipment_id = request.form.get('equipment_id')
    quantity = request.form.get('quantity')

    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO equipment_requests (user_id, equipment_id, quantity, request_date)
            VALUES (%s, %s, %s, CURRENT_DATE)
        """, (session['user_id'], equipment_id, quantity))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, message=str(e))

@app.route('/admin/equipment/delete', methods=['POST'])
def delete_equipments():
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    ids = request.form.getlist('delete_ids')
    if ids:
        conn = get_connection()
        cur = conn.cursor()
        # ✅ 정수형으로 명시적 캐스팅
        cur.execute("DELETE FROM equipments WHERE id = ANY(%s::int[])", (ids,))
        conn.commit()
        cur.close()
        conn.close()

    return redirect(url_for('manage_equipments'))


# ----------------------- 서버 실행 -----------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=True)

# ----------------------- 루트 경로 -----------------------
@app.route('/')
def index():
    return redirect(url_for('login'))

