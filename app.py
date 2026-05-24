import os
import uuid
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from jinja2 import DictLoader
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, extract, desc
from flask_migrate import Migrate
from werkzeug.utils import secure_filename

load_dotenv()

# --- 1. INITIALIZE APP & JINJA DICT TEMPLATES ---
app = Flask(__name__)
app.secret_key = "flex_vape_final_unified_key"

# --- NEON POSTGRESQL CONFIGURATION ---
DATABASE_URL = os.environ.get('DATABASE_URL', '')

# Fix for SQLAlchemy: Neon may return 'postgres://' -- normalize to 'postgresql://'
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 280,
}

db = SQLAlchemy(app)

UPLOAD_FOLDER = '/tmp/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- JINJA DICTLOADER TEMPLATES MAP ---
TEMPLATES = {}

# --- 2. DATABASE MODELS ---
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    barcode = db.Column(db.String(50), unique=True, nullable=True)
    name = db.Column(db.String(100), nullable=False)
    flavor = db.Column(db.String(100))
    type = db.Column(db.String(50))
    version = db.Column(db.String(50))
    mg = db.Column(db.String(20))
    qty = db.Column(db.Integer, default=0)
    cost = db.Column(db.Float, default=0.0)
    price = db.Column(db.Float, default=0.0)
    discount = db.Column(db.Float, default=0.0)  # Discount percentage (0-100)
    image = db.Column(db.String(255), default='default.jpg')
    date_added = db.Column(db.DateTime, default=datetime.now)

class StockInLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=datetime.now)
    name = db.Column(db.String(100))
    flavor = db.Column(db.String(100))
    category = db.Column(db.String(50))
    qty = db.Column(db.Integer)

class StockOutLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=datetime.now)
    name = db.Column(db.String(100))
    flavor = db.Column(db.String(100))
    category = db.Column(db.String(50))
    qty = db.Column(db.Integer)
    price = db.Column(db.Float, default=0.0)
    cost = db.Column(db.Float, default=0.0)

# --- 3. LOGIN PROTECTION ---
# Set FLEX_USER and FLEX_PASS environment variables in production.
ADMIN_USER = os.environ.get("FLEX_USER", "flexinventory")
ADMIN_PASS = os.environ.get("FLEX_PASS", "flexsystem")

@app.before_request
def require_login():
    allowed_routes = ['login', 'static']
    if 'logged_in' not in session and request.endpoint not in allowed_routes:
        return redirect(url_for('login'))

# --- 4. HELPERS ---
def get_products_dict():
    products = Product.query.all()
    return {str(p.id): {
        "id": p.id,
        "barcode": p.barcode or '',
        "name": p.name,
        "flavor": p.flavor or '',
        "type": p.type or '',
        "version": p.version or '',
        "mg": p.mg or '',
        "qty": p.qty or 0,
        "cost": p.cost or 0.0,
        "price": p.price or 0.0,
        "discount": p.discount or 0.0,
        "image": p.image
    } for p in products}

# --- 5. ROUTES ---

@app.route('/')
def dashboard():
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    day_name = now.strftime('%A')
    month_name = now.strftime('%B')

    sales_today_count = StockOutLog.query.filter(func.date(StockOutLog.date) == today_str).count()
    rev_month = db.session.query(func.sum(StockOutLog.qty * StockOutLog.price)).filter(
        extract('month', StockOutLog.date) == now.month,
        extract('year', StockOutLog.date) == now.year
    ).scalar() or 0

    products_all = Product.query.all()
    total_qty = sum(p.qty for p in products_all)
    low_stock_count = Product.query.filter(Product.qty < 5).count()

    stats = {
        'total_qty': total_qty, 
        'low_stock': low_stock_count,
        'revenue_month': f"₱{rev_month:,.2f}", 
        'sales_today_count': sales_today_count,
        'day_name': day_name,
        'month_name': month_name,
    }
    return render_template('dashboard.html', stats=stats)

@app.route('/history')
def history():
    daily_history = db.session.query(
        func.date(StockOutLog.date).label('day'),
        func.count(StockOutLog.id).label('count'),
        func.sum(StockOutLog.qty * StockOutLog.price).label('revenue')
    ).group_by(func.date(StockOutLog.date)).order_by(desc('day')).limit(60).all()

    monthly_history = db.session.query(
        extract('year', StockOutLog.date).label('year'),
        extract('month', StockOutLog.date).label('month'),
        func.sum(StockOutLog.qty * StockOutLog.price).label('revenue')
    ).group_by('year', 'month').order_by(desc('year'), desc('month')).all()

    formatted_monthly = []
    for row in monthly_history:
        m_name = datetime(int(row.year), int(row.month), 1).strftime('%B')
        formatted_monthly.append({'year': row.year, 'month': m_name, 'revenue': row.revenue})

    return render_template('history.html', daily=daily_history, monthly=formatted_monthly)

@app.route('/inventory')
def inventory():
    return render_template('inventory.html', products=get_products_dict())

@app.route('/api/product/barcode/<barcode>')
def get_product_by_barcode(barcode):
    p = Product.query.filter_by(barcode=barcode).first()
    if p:
        return jsonify({"success": True, "id": p.id, "name": p.name, "flavor": p.flavor, "price": p.price, "qty": p.qty, "image": p.image})
    return jsonify({"success": False}), 404

@app.route('/products', methods=['GET', 'POST'])
def products():
    if request.method == 'POST':
        action = request.form.get('action')
        p_id = request.form.get('editing_key')
        
        if action == 'delete' and p_id:
            p = db.session.get(Product, p_id)
            if p: 
                db.session.delete(p)
                db.session.commit()
            return redirect(url_for('products'))

        name = request.form.get('name')
        price = float(request.form.get('price') or 0)
        cost = float(request.form.get('cost') or 0)
        discount = float(request.form.get('discount') or 0)
        barcode = request.form.get('barcode', '').strip() or str(int(time.time()))
        
        # Handle Image File Upload safely
        file = request.files.get('product_image')
        image_filename = None
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            unique_filename = f"{uuid.uuid4().hex}_{filename}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
            image_filename = unique_filename

        if p_id:
            p = db.session.get(Product, p_id)
            if not p:
                return redirect(url_for('products'))
            p.name, p.price, p.barcode = name, price, barcode
            p.flavor = request.form.get('flavor')
            p.type = request.form.get('type')
            p.version = request.form.get('version')
            p.mg = request.form.get('mg')
            p.cost = cost
            p.discount = discount
            if image_filename:
                p.image = image_filename
        else:
            qty = int(request.form.get('quantity') or 0)
            new_p = Product(name=name, price=price, barcode=barcode, 
                            qty=qty, 
                            type=request.form.get('type'), 
                            flavor=request.form.get('flavor'), 
                            cost=cost,
                            discount=discount,
                            version=request.form.get('version'), 
                            mg=request.form.get('mg'),
                            image=image_filename or 'default.jpg')
            db.session.add(new_p)
            
            if qty > 0:
                db.session.add(StockInLog(name=name, flavor=request.form.get('flavor'), category=request.form.get('type'), qty=qty))
                
        db.session.commit()
        return redirect(url_for('products'))
    return render_template('products.html', products=get_products_dict())

@app.route('/sales', methods=['GET', 'POST'])
def sales():
    if request.method == 'POST':
        p_id = request.form.get('product_key')
        qty = int(request.form.get('quantity') or 0)
        manual_discount = float(request.form.get('manual_discount') or 0)
        p = db.session.get(Product, p_id)
        if p and qty > 0 and p.qty >= qty:
            product_discount = p.discount or 0
            total_discount = min(product_discount + manual_discount, 100)
            discounted_price = p.price * (1 - total_discount / 100)
            p.qty -= qty
            db.session.add(StockOutLog(name=p.name, flavor=p.flavor, category=p.type, qty=qty, price=discounted_price, cost=p.cost))
            db.session.commit()
            flash("Sale recorded successfully.", "success")
        else:
            flash("Insufficient inventory level.", "danger")
        return redirect(url_for('sales'))
    logs = StockOutLog.query.order_by(StockOutLog.id.desc()).limit(50).all()
    return render_template('sales.html', products=get_products_dict(), logs=logs)

@app.route('/reports')
def reports():
    period = request.args.get('period', 'daily')
    today = datetime.now().date()
    start_date = today - timedelta(days=7) if period == 'weekly' else today
    start_date_str = start_date.strftime('%Y-%m-%d')
    
    logs_out = StockOutLog.query.filter(func.date(StockOutLog.date) >= start_date_str).all()
    logs_in = StockInLog.query.filter(func.date(StockInLog.date) >= start_date_str).all()
    
    movement = []
    for p in Product.query.all():
        sold = sum(l.qty for l in logs_out if l.name == p.name and l.flavor == p.flavor)
        added = sum(l.qty for l in logs_in if l.name == p.name and l.flavor == p.flavor)
        opening = p.qty + sold - added
        if opening > 0 or added > 0 or sold > 0:
            movement.append({'name': f"{p.name} {p.flavor}", 'open': opening, 'new': added, 'sold': sold, 'end': p.qty})

    # Critical Low Stock Warning mapping
    low_stocks = {}
    low_stock_items = Product.query.filter(Product.qty < 5).all()
    if low_stock_items:
        low_stocks["low"] = low_stock_items

    return render_template('reports.html', movement=movement, revenue=sum(l.qty*l.price for l in logs_out), sales_count=len(logs_out), date=today.strftime("%B %d, %Y"), now=datetime.now().strftime("%H:%M"), period=period, report_label="Inventory Audit Report", low_stocks=low_stocks)



@app.route('/api/low_stock')
def api_low_stock():
    items = Product.query.filter(Product.qty < 5).order_by(Product.qty.asc()).limit(10).all()
    return jsonify([{"name": p.name, "flavor": p.flavor or "", "qty": p.qty} for p in items])

@app.route('/analytics')
def analytics():
    now = datetime.now()
    today = now.date()

    # --- KPI SUMMARY ---
    total_revenue = db.session.query(func.sum(StockOutLog.qty * StockOutLog.price)).scalar() or 0
    total_units_sold = db.session.query(func.sum(StockOutLog.qty)).scalar() or 0
    total_profit = db.session.query(func.sum(StockOutLog.qty * (StockOutLog.price - StockOutLog.cost))).scalar() or 0
    total_transactions = StockOutLog.query.count()

    # --- SALES TREND: last 30 days (daily revenue) ---
    trend_labels, trend_revenue, trend_units = [], [], []
    for i in range(29, -1, -1):
        d = today - timedelta(days=i)
        d_str = d.strftime('%Y-%m-%d')
        rev = db.session.query(func.sum(StockOutLog.qty * StockOutLog.price)).filter(
            func.date(StockOutLog.date) == d_str
        ).scalar() or 0
        units = db.session.query(func.sum(StockOutLog.qty)).filter(
            func.date(StockOutLog.date) == d_str
        ).scalar() or 0
        trend_labels.append(d.strftime('%b %d'))
        trend_revenue.append(round(float(rev), 2))
        trend_units.append(int(units))

    # --- MONTHLY REVENUE: last 6 months ---
    monthly_labels, monthly_revenue, monthly_profit = [], [], []
    for i in range(5, -1, -1):
        target = now - timedelta(days=i * 30)
        rev = db.session.query(func.sum(StockOutLog.qty * StockOutLog.price)).filter(
            extract('month', StockOutLog.date) == target.month,
            extract('year', StockOutLog.date) == target.year
        ).scalar() or 0
        profit = db.session.query(func.sum(StockOutLog.qty * (StockOutLog.price - StockOutLog.cost))).filter(
            extract('month', StockOutLog.date) == target.month,
            extract('year', StockOutLog.date) == target.year
        ).scalar() or 0
        monthly_labels.append(target.strftime("%b '%y"))
        monthly_revenue.append(round(float(rev), 2))
        monthly_profit.append(round(float(profit), 2))

    # --- TOP ITEMS by revenue and units ---
    top_by_revenue_raw = db.session.query(
        StockOutLog.name, StockOutLog.flavor,
        func.sum(StockOutLog.qty * StockOutLog.price).label('revenue'),
        func.sum(StockOutLog.qty).label('units')
    ).group_by(StockOutLog.name, StockOutLog.flavor).order_by(desc('revenue')).limit(10).all()

    top_by_units_raw = db.session.query(
        StockOutLog.name, StockOutLog.flavor,
        func.sum(StockOutLog.qty).label('units'),
        func.sum(StockOutLog.qty * StockOutLog.price).label('revenue')
    ).group_by(StockOutLog.name, StockOutLog.flavor).order_by(desc('units')).limit(10).all()

    top_by_revenue = [{'name': r.name, 'flavor': r.flavor or '', 'revenue': round(float(r.revenue), 2), 'units': int(r.units)} for r in top_by_revenue_raw]
    top_by_units   = [{'name': r.name, 'flavor': r.flavor or '', 'units': int(r.units), 'revenue': round(float(r.revenue), 2)} for r in top_by_units_raw]

    # --- CATEGORY PERFORMANCE ---
    cat_perf_raw = db.session.query(
        StockOutLog.category,
        func.sum(StockOutLog.qty * StockOutLog.price).label('revenue'),
        func.sum(StockOutLog.qty).label('units'),
        func.sum(StockOutLog.qty * (StockOutLog.price - StockOutLog.cost)).label('profit')
    ).group_by(StockOutLog.category).order_by(desc('revenue')).all()

    total_cat_rev = sum(float(c.revenue or 0) for c in cat_perf_raw) or 1
    cat_perf = [{
        'name': (c.category or 'Other').capitalize(),
        'revenue': round(float(c.revenue or 0), 2),
        'units': int(c.units or 0),
        'profit': round(float(c.profit or 0), 2),
        'pct': round(float(c.revenue or 0) / total_cat_rev * 100, 1)
    } for c in cat_perf_raw]

    # --- HIGH PERFORMANCE PRODUCTS ---
    all_products_perf = db.session.query(
        StockOutLog.name, StockOutLog.flavor,
        func.sum(StockOutLog.qty * StockOutLog.price).label('revenue'),
        func.sum(StockOutLog.qty * StockOutLog.cost).label('total_cost'),
        func.sum(StockOutLog.qty).label('units'),
        func.sum(StockOutLog.qty * (StockOutLog.price - StockOutLog.cost)).label('profit')
    ).group_by(StockOutLog.name, StockOutLog.flavor).having(
        func.sum(StockOutLog.qty * StockOutLog.price) > 0
    ).all()

    performers = []
    for r in all_products_perf:
        rev = float(r.revenue or 0)
        profit = float(r.profit or 0)
        margin = (profit / rev * 100) if rev > 0 else 0
        performers.append({
            'name': r.name, 'flavor': r.flavor or '',
            'revenue': round(rev, 2), 'profit': round(profit, 2),
            'units': int(r.units or 0), 'margin': round(margin, 1)
        })

    avg_margin = (sum(p['margin'] for p in performers) / len(performers)) if performers else 0
    high_performers = sorted([p for p in performers if p['margin'] >= avg_margin], key=lambda x: x['margin'], reverse=True)[:10]

    # --- HOURLY SALES PATTERN ---
    hourly_raw = db.session.query(
        extract('hour', StockOutLog.date).label('hour'),
        func.sum(StockOutLog.qty).label('units')
    ).group_by('hour').all()
    hourly = {int(r.hour): int(r.units) for r in hourly_raw}
    hourly_labels = [f"{h:02d}:00" for h in range(24)]
    hourly_values = [hourly.get(h, 0) for h in range(24)]

    return render_template('analytics.html',
        total_revenue=total_revenue, total_units_sold=total_units_sold,
        total_profit=total_profit, total_transactions=total_transactions,
        avg_margin=round(avg_margin, 1),
        trend_labels=trend_labels, trend_revenue=trend_revenue, trend_units=trend_units,
        monthly_labels=monthly_labels, monthly_revenue=monthly_revenue, monthly_profit=monthly_profit,
        top_by_revenue=top_by_revenue, top_by_units=top_by_units,
        cat_perf=cat_perf,
        high_performers=high_performers,
        hourly_labels=hourly_labels, hourly_values=hourly_values
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('username') == ADMIN_USER and request.form.get('password') == ADMIN_PASS:
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid authentication credentials.", "danger")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    global ADMIN_USER, ADMIN_PASS
    msg = None
    msg_type = 'success'

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'change_password':
            current = request.form.get('current_password', '')
            new_pw = request.form.get('new_password', '')
            confirm = request.form.get('confirm_password', '')
            if current != ADMIN_PASS:
                msg, msg_type = "Current password is incorrect.", 'danger'
            elif len(new_pw) < 4:
                msg, msg_type = "New password must be at least 4 characters.", 'danger'
            elif new_pw != confirm:
                msg, msg_type = "New passwords do not match.", 'danger'
            else:
                ADMIN_PASS = new_pw
                msg = "Password updated successfully."

        elif action == 'change_username':
            new_user = request.form.get('new_username', '').strip()
            pw_confirm = request.form.get('password_for_user', '')
            if pw_confirm != ADMIN_PASS:
                msg, msg_type = "Password confirmation incorrect.", 'danger'
            elif len(new_user) < 3:
                msg, msg_type = "Username must be at least 3 characters.", 'danger'
            else:
                ADMIN_USER = new_user
                msg = f"Username changed to '{ADMIN_USER}'."

        elif action == 'clear_sales_log':
            pw = request.form.get('danger_password', '')
            if pw != ADMIN_PASS:
                msg, msg_type = "Incorrect password. Sales log not cleared.", 'danger'
            else:
                StockOutLog.query.delete()
                db.session.commit()
                msg = "All sales logs cleared."

        elif action == 'clear_stock_in_log':
            pw = request.form.get('danger_password2', '')
            if pw != ADMIN_PASS:
                msg, msg_type = "Incorrect password. Stock-in log not cleared.", 'danger'
            else:
                StockInLog.query.delete()
                db.session.commit()
                msg = "All stock-in logs cleared."

    total_products = Product.query.count()
    total_sales_logs = StockOutLog.query.count()
    total_stockin_logs = StockInLog.query.count()

    return render_template('settings.html',
        msg=msg, msg_type=msg_type,
        admin_user=ADMIN_USER,
        total_products=total_products,
        total_sales_logs=total_sales_logs,
        total_stockin_logs=total_stockin_logs
    )

@app.route('/settings/backup')
def settings_backup():
    import json
    from flask import Response

    products = [{
        'barcode': p.barcode, 'name': p.name, 'flavor': p.flavor,
        'type': p.type, 'version': p.version, 'mg': p.mg,
        'qty': p.qty, 'cost': p.cost, 'price': p.price,
        'discount': p.discount, 'image': p.image,
        'date_added': p.date_added.isoformat() if p.date_added else None
    } for p in Product.query.all()]

    sales_logs = [{
        'date': l.date.isoformat() if l.date else None,
        'name': l.name, 'flavor': l.flavor, 'category': l.category,
        'qty': l.qty, 'price': l.price, 'cost': l.cost
    } for l in StockOutLog.query.all()]

    stockin_logs = [{
        'date': l.date.isoformat() if l.date else None,
        'name': l.name, 'flavor': l.flavor, 'category': l.category,
        'qty': l.qty
    } for l in StockInLog.query.all()]

    payload = {
        'backup_version': '1.0',
        'exported_at': datetime.now().isoformat(),
        'products': products,
        'sales_logs': sales_logs,
        'stockin_logs': stockin_logs,
    }
    filename = f"flex_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    return Response(
        json.dumps(payload, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )

@app.route('/settings/restore', methods=['POST'])
def settings_restore():
    import json
    pw = request.form.get('restore_password', '')
    if pw != ADMIN_PASS:
        flash("Incorrect password. Restore cancelled.", "danger")
        return redirect(url_for('settings'))

    file = request.files.get('restore_file')
    if not file or file.filename == '':
        flash("No file selected.", "danger")
        return redirect(url_for('settings'))

    try:
        data = json.loads(file.read().decode('utf-8'))
    except Exception:
        flash("Invalid backup file. Could not parse JSON.", "danger")
        return redirect(url_for('settings'))

    mode = request.form.get('restore_mode', 'merge')  # 'merge' or 'overwrite'

    try:
        if mode == 'overwrite':
            Product.query.delete()
            StockOutLog.query.delete()
            StockInLog.query.delete()
            db.session.commit()

        # Restore products
        existing_barcodes = {p.barcode for p in Product.query.all() if p.barcode}
        existing_names_flavors = {(p.name, p.flavor) for p in Product.query.all()}
        added_products = 0
        for p in data.get('products', []):
            key = (p.get('name'), p.get('flavor'))
            if mode == 'merge' and (p.get('barcode') in existing_barcodes or key in existing_names_flavors):
                continue
            new_p = Product(
                barcode=p.get('barcode'), name=p.get('name', ''), flavor=p.get('flavor'),
                type=p.get('type'), version=p.get('version'), mg=p.get('mg'),
                qty=p.get('qty', 0), cost=p.get('cost', 0.0), price=p.get('price', 0.0),
                discount=p.get('discount', 0.0), image=p.get('image', 'default.jpg')
            )
            db.session.add(new_p)
            added_products += 1

        # Restore sales logs
        added_sales = 0
        for l in data.get('sales_logs', []):
            db.session.add(StockOutLog(
                date=datetime.fromisoformat(l['date']) if l.get('date') else datetime.now(),
                name=l.get('name', ''), flavor=l.get('flavor'), category=l.get('category'),
                qty=l.get('qty', 0), price=l.get('price', 0.0), cost=l.get('cost', 0.0)
            ))
            added_sales += 1

        # Restore stock-in logs
        added_stockin = 0
        for l in data.get('stockin_logs', []):
            db.session.add(StockInLog(
                date=datetime.fromisoformat(l['date']) if l.get('date') else datetime.now(),
                name=l.get('name', ''), flavor=l.get('flavor'), category=l.get('category'),
                qty=l.get('qty', 0)
            ))
            added_stockin += 1

        db.session.commit()
        flash(
            f"Restore complete — {added_products} products, {added_sales} sales, {added_stockin} stock-in records imported.",
            "success"
        )
    except Exception as e:
        db.session.rollback()
        flash(f"Restore failed: {str(e)}", "danger")

    return redirect(url_for('settings'))


# --- 6. DEFINE EMBEDDED JINJA TEMPLATE STRINGS ---

TEMPLATES["base.html"] = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>F.L.E.X | Inventory Management</title>
    <link rel="icon" type="image/jpeg" href="/static/images/flex_vape_shop.jpg">
    
    <!-- Professional Font & Icons -->
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.4.0/css/all.min.css" crossorigin="anonymous" referrerpolicy="no-referrer">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Outfit:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
    
    <!-- Barcode Scanner Library -->
    <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>

    <style>
        :root {
            --navy: #162135;
            --purple: #705194;
            --bg-body: #f8f7ff;
            --sidebar-width: 260px;
            --text-main: #1e293b;
            --text-muted: #64748b;
            --border: #e2e8f0;
            --header-height: 60px;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }

        body { 
            font-family: 'Outfit', 'Inter', sans-serif; 
            background: var(--bg-body); 
            color: var(--text-main);
            display: flex; 
            min-height: 100vh;
            overflow-x: hidden;
        }

        /* --- SCANNER OVERLAY STYLES --- */
        #fsScannerContainer {
            display: none;
            position: fixed;
            inset: 0;
            background: #000;
            z-index: 9999;
        }

        #fsReader { width: 100%; height: 100%; }
        #fsReader video { object-fit: cover !important; }

        .scan-overlay {
            position: absolute;
            inset: 0;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            pointer-events: none;
        }

        .scan-frame {
            position: relative;
            width: 280px;
            height: 280px;
            border: 1px solid rgba(255,255,255,0.2);
        }

        /* White Corners */
        .corner {
            position: absolute;
            width: 40px;
            height: 40px;
            border: 5px solid #fff;
            border-radius: 12px;
        }
        .top-left { top: -5px; left: -5px; border-right: 0; border-bottom: 0; border-radius: 12px 0 0 0; }
        .top-right { top: -5px; right: -5px; border-left: 0; border-bottom: 0; border-radius: 0 12px 0 0; }
        .bottom-left { bottom: -5px; left: -5px; border-right: 0; border-top: 0; border-radius: 0 0 0 12px; }
        .bottom-right { bottom: -5px; right: -5px; border-left: 0; border-top: 0; border-radius: 0 0 12px 0; }

        /* Animated Blue Line */
        .scan-line {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 3px;
            background: #3b82f6;
            box-shadow: 0 0 15px #3b82f6;
            animation: scanMove 2s infinite linear;
        }

        @keyframes scanMove {
            0% { top: 0; }
            50% { top: 100%; }
            100% { top: 0; }
        }

        .scan-text {
            color: white;
            margin-top: 40px;
            font-weight: 600;
            text-shadow: 0 2px 4px rgba(0,0,0,0.5);
            letter-spacing: 1px;
        }

        .scan-close-btn {
            position: absolute;
            top: 40px;
            left: 25px;
            background: rgba(0,0,0,0.5);
            color: white;
            border: none;
            width: 50px;
            height: 50px;
            border-radius: 50%;
            font-size: 20px;
            cursor: pointer;
            pointer-events: auto;
        }

        /* --- MOBILE TOP BAR --- */
        .mobile-header {
            display: none; 
            position: fixed;
            top: 0; left: 0; right: 0;
            height: var(--header-height);
            background: var(--navy);
            color: white;
            align-items: center;
            justify-content: space-between; 
            padding: 0 15px;
            z-index: 1001;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }

        .menu-btn {
            background: rgba(255,255,255,0.1);
            border: none;
            color: white;
            font-size: 1.2rem;
            cursor: pointer;
            width: 40px;
            height: 40px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: 0.3s;
        }

        /* --- SIDEBAR --- */
        .sidebar { 
            width: var(--sidebar-width); 
            background: linear-gradient(180deg, #0f1c2e 0%, #162135 40%, #1a1535 100%); 
            height: 100vh; 
            position: fixed; 
            left: 0; top: 0; 
            z-index: 1002; 
            color: white; 
            display: flex;
            flex-direction: column;
            padding: 30px 0 20px 0;
            border-right: 1px solid rgba(112,81,148,0.2);
            box-shadow: 4px 0 24px rgba(0,0,0,0.3);
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .sidebar-header { padding: 0 25px 20px 25px; text-align: center; }
        .logo-img { width: 90px; height: 90px; border-radius: 50%; border: 4px solid var(--purple); background: #1D2D44; margin-bottom: 15px; object-fit: cover; }
        .sidebar-header h3 { font-weight: 800; letter-spacing: 3px; font-size: 1.2rem; color: white; text-shadow: 0 0 20px rgba(167,139,202,0.5); }
        .divider { height: 1px; background: rgba(255,255,255,0.08); margin: 15px 25px; }
        .menu-label { padding: 10px 25px; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 1.5px; color: #576c8c; font-weight: 700; }
        .nav-links { list-style: none; flex-grow: 1; padding: 0 15px; }
        .nav-links li { margin-bottom: 8px; }
        .nav-links a { color: #94a3b8; text-decoration: none; padding: 12px 20px; display: flex; align-items: center; gap: 15px; font-size: 0.95rem; font-weight: 500; transition: 0.2s; border-radius: 12px; }
        .nav-links a:hover { color: white; background: rgba(255,255,255,0.05); }
        .nav-links a.active { background: linear-gradient(90deg, rgba(112,81,148,0.9), rgba(85,60,123,0.7)); color: white !important; font-weight: 600; box-shadow: 0 8px 20px -8px rgba(112,81,148,0.6); border-left: 3px solid #a78bca; }
        .nav-links a i { font-size: 1.2rem; width: 25px; opacity: 0.8; }

        /* --- LOGOUT AREA --- */
        .logout-container { padding: 0 15px; margin-top: auto; }
        .logout-link { padding: 15px 20px; color: #ff8a8a; text-decoration: none; font-size: 0.95rem; font-weight: 600; background: rgba(255,255,255,0.04); border-radius: 14px; display: flex; align-items: center; gap: 12px; transition: 0.3s; }
        .logout-link:hover { background: rgba(255,138,138,0.1); color: #ff6b6b; }

        /* --- OVERLAY & CONTENT --- */
        .sidebar-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 1000; backdrop-filter: blur(2px); }
        .main-content { margin-left: var(--sidebar-width); padding: 40px; width: calc(100% - var(--sidebar-width)); flex-grow: 1; }
        .main-content.no-sidebar { margin-left: 0; width: 100%; }

        /* --- FLASH MESSAGES --- */
        .flash-container { margin-bottom: 20px; display: flex; flex-direction: column; gap: 10px; }
        .flash-msg { display: flex; align-items: center; gap: 10px; padding: 12px 18px; border-radius: 12px; font-size: 0.875rem; font-weight: 600; position: relative; }
        .flash-success { background: #d1fae5; color: #065f46; border: 1px solid #a7f3d0; }
        .flash-danger  { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }
        .flash-warning { background: #fef9c3; color: #854d0e; border: 1px solid #fde68a; }
        .flash-close { background: none; border: none; cursor: pointer; font-size: 1.1rem; margin-left: auto; opacity: 0.6; line-height: 1; padding: 0 4px; }
        .flash-close:hover { opacity: 1; }

        @media (max-width: 1024px) {
            .mobile-header { display: flex; }
            .sidebar { transform: translateX(-100%); }
            .sidebar.open { transform: translateX(0); }
            .sidebar-overlay.show { display: block; }
            .main-content { margin-left: 0; width: 100%; padding: 20px; padding-top: 80px; }
        }
    </style>
</head>
<body>

    <!-- GLOBAL FULL SCREEN SCANNER UI -->
    <div id="fsScannerContainer">
        <div id="fsReader"></div>
        <div class="scan-overlay">
            <div class="scan-frame">
                <div class="corner top-left"></div>
                <div class="corner top-right"></div>
                <div class="corner bottom-left"></div>
                <div class="corner bottom-right"></div>
                <div class="scan-line"></div>
            </div>
            <p class="scan-text">ALIGN BARCODE WITHIN FRAME</p>
        </div>
        <button class="scan-close-btn" onclick="stopFSScanner()">
            <i class="fas fa-times"></i>
        </button>
    </div>

    {% if session.get('logged_in') %}
    <header class="mobile-header">
        <button class="menu-btn" id="openMenu">
            <i class="fa-solid fa-bars"></i>
        </button>
        <span style="font-weight: 800; letter-spacing: 1px; font-size: 1.1rem; position: absolute; left: 50%; transform: translateX(-50%);">F.L.E.X</span>
        <div style="width: 40px;"></div>
    </header>

    <div class="sidebar-overlay" id="overlay"></div>

    <nav class="sidebar" id="sidebar">
        <div class="sidebar-header">
            <img src="/static/images/flex_vape_shop.jpg" alt="F.L.E.X Logo" style="width:90px;height:90px;border-radius:50%;border:3px solid var(--purple);object-fit:cover;margin:0 auto 15px auto;display:block;box-shadow:0 8px 24px rgba(112,81,148,0.4);">
            <h3>F.L.E.X</h3>
        </div>

        <div class="divider"></div>
        <div class="menu-label">Main Menu</div>

        <ul class="nav-links">
            <li><a href="/" class="{{ 'active' if request.path == '/' }}"><i class="fa-solid fa-chart-pie"></i> <span>Dashboard</span></a></li>
            <li><a href="/inventory" class="{{ 'active' if request.path == '/inventory' }}"><i class="fa-solid fa-boxes-stacked"></i> <span>Inventory</span></a></li>
            <li><a href="/sales" class="{{ 'active' if request.path == '/sales' }}"><i class="fa-solid fa-cart-shopping"></i> <span>Sales</span></a></li>
            <li><a href="/products" class="{{ 'active' if request.path == '/products' }}"><i class="fa-solid fa-tags"></i> <span>Products</span></a></li>
            <li><a href="/reports" class="{{ 'active' if request.path == '/reports' }}"><i class="fa-solid fa-file-waveform"></i> <span>Reports</span></a></li>
            <li><a href="/analytics" class="{{ 'active' if request.path == '/analytics' }}"><i class="fa-solid fa-chart-line"></i> <span>Analytics</span></a></li>
            <li><a href="/settings" class="{{ 'active' if request.path == '/settings' }}"><i class="fa-solid fa-gear"></i> <span>Settings</span></a></li>
        </ul>

        <div class="logout-container">
            <a href="/logout" class="logout-link">
                <i class="fa-solid fa-power-off"></i> <span>Logout System</span>
            </a>
        </div>
    </nav>
    {% endif %}

    <main class="main-content {{ 'no-sidebar' if not session.get('logged_in') }}">
        {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
        <div class="flash-container">
            {% for category, message in messages %}
            <div class="flash-msg flash-{{ category }}">
                <i class="fas {{ 'fa-check-circle' if category == 'success' else 'fa-circle-exclamation' }}"></i>
                {{ message }}
                <button class="flash-close" onclick="this.parentElement.remove()">&times;</button>
            </div>
            {% endfor %}
        </div>
        {% endif %}
        {% endwith %}
        {% block content %}{% endblock %}
    </main>

    <script>
        // --- SIDEBAR LOGIC ---
        const openBtn = document.getElementById('openMenu');
        const sidebar = document.getElementById('sidebar');
        const overlay = document.getElementById('overlay');

        if (openBtn) {
            openBtn.addEventListener('click', () => {
                sidebar.classList.add('open');
                overlay.classList.add('show');
            });
        }

        if (overlay) {
            overlay.addEventListener('click', () => {
                sidebar.classList.remove('open');
                overlay.classList.remove('show');
            });
        }
        
        window.addEventListener('resize', () => {
            if (window.innerWidth > 1024 && sidebar) {
                sidebar.classList.remove('open');
                overlay.classList.remove('show');
            }
        });

        // --- GLOBAL SCANNER LOGIC ---
        let fsHtml5QrCode;

        async function startFSScanner(onSuccessCallback) {
            const container = document.getElementById('fsScannerContainer');
            container.style.display = 'block';

            fsHtml5QrCode = new Html5Qrcode("fsReader");
            
            const config = { 
                fps: 20, 
                qrbox: { width: 280, height: 280 },
                aspectRatio: 1.0 
            };

            try {
                await fsHtml5QrCode.start(
                    { facingMode: "environment" }, 
                    config, 
                    (decodedText) => {
                        // Success Feedback
                        playScanSound();
                        onSuccessCallback(decodedText);
                        stopFSScanner();
                    }
                );
            } catch (err) {
                console.error(err);
                alert("Camera access error. Ensure you are using HTTPS.");
                stopFSScanner();
            }
        }

        function stopFSScanner() {
            if (fsHtml5QrCode) {
                fsHtml5QrCode.stop().then(() => {
                    document.getElementById('fsScannerContainer').style.display = 'none';
                }).catch(() => {
                    document.getElementById('fsScannerContainer').style.display = 'none';
                });
            } else {
                document.getElementById('fsScannerContainer').style.display = 'none';
            }
        }

        function playScanSound() {
            try {
                const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                const osc = audioCtx.createOscillator();
                osc.type = 'sine';
                osc.frequency.setValueAtTime(880, audioCtx.currentTime);
                osc.connect(audioCtx.destination);
                osc.start();
                osc.stop(audioCtx.currentTime + 0.1);
            } catch(e) {}
        }
    </script>
</body>
</html>
"""

TEMPLATES["dashboard.html"] = """
{% extends "base.html" %}
{% block content %}
<style>
:root {
    --brand:#705194; --brand-light:#f3eeff; --green:#10b981; --red:#ef4444;
    --orange:#f59e0b; --blue:#3b82f6;
    --grad:linear-gradient(135deg,#705194,#9b6fc4);
    --radius:16px; --radius-sm:10px;
    --border:#e8e4f0; --text:#1e293b; --muted:#64748b; --bg:#f8f7ff;
}
*{box-sizing:border-box;}
.pg{max-width:1100px;margin:0 auto;padding:0 0 60px;}
.pg-header{margin-bottom:28px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;}
.pg-header h1{font-size:1.7rem;font-weight:800;color:var(--text);}
.pg-header p{color:var(--muted);font-size:0.9rem;margin-top:4px;}
.history-btn{background:var(--grad);color:white;padding:9px 18px;border-radius:50px;text-decoration:none;font-size:0.82rem;font-weight:700;display:flex;align-items:center;gap:8px;transition:.2s;border:none;cursor:pointer;}
.history-btn:hover{opacity:.88;transform:translateY(-1px);}

/* KPI CARDS */
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:28px;}
.kpi-card{background:white;border-radius:var(--radius);border:1.5px solid var(--border);padding:20px 22px;box-shadow:0 2px 10px rgba(112,81,148,.05);}
.kpi-card .kpi-label{font-size:0.72rem;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:700;margin-bottom:8px;}
.kpi-card .kpi-val{font-size:1.65rem;font-weight:800;color:var(--text);line-height:1;}
.kpi-card .kpi-sub{font-size:0.78rem;color:var(--muted);margin-top:6px;}
.kpi-card .kpi-ico{width:38px;height:38px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:1rem;margin-bottom:12px;}
.kpi-ico.purple{background:#f3eeff;color:var(--brand);}
.kpi-ico.green{background:#ecfdf5;color:var(--green);}
.kpi-ico.orange{background:#fffbeb;color:var(--orange);}
.kpi-ico.blue{background:#eff6ff;color:var(--blue);}
.kpi-ico.red{background:#fef2f2;color:var(--red);}

/* OVERVIEW SECTION */
.overview-row{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px;}
.chart-card{background:white;border-radius:var(--radius);border:1.5px solid var(--border);box-shadow:0 2px 10px rgba(112,81,148,.05);overflow:hidden;}
.chart-head{display:flex;align-items:center;justify-content:space-between;padding:18px 22px 0;gap:10px;}
.chart-head-left{display:flex;align-items:center;gap:12px;}
.chart-ico{width:36px;height:36px;background:var(--grad);border-radius:9px;display:flex;align-items:center;justify-content:center;color:white;font-size:.9rem;flex-shrink:0;}
.chart-title{font-size:.95rem;font-weight:700;color:var(--text);}
.chart-sub{font-size:.76rem;color:var(--muted);}
.chart-body{padding:18px 22px 22px;}

/* QUICK LINKS */
.quick-links{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;}
.ql-item{display:flex;align-items:center;gap:12px;padding:14px 16px;border-radius:12px;border:1.5px solid var(--border);text-decoration:none;background:white;transition:.2s;color:var(--text);}
.ql-item:hover{border-color:var(--brand);background:var(--brand-light);transform:translateY(-1px);}
.ql-ico{width:36px;height:36px;border-radius:9px;display:flex;align-items:center;justify-content:center;font-size:.85rem;flex-shrink:0;}
.ql-label{font-size:.85rem;font-weight:700;}
.ql-desc{font-size:.73rem;color:var(--muted);}

/* LOW STOCK TABLE */
.rank-table{width:100%;border-collapse:collapse;}
.rank-table th{text-align:left;padding:10px 14px;font-size:.7rem;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);font-weight:700;border-bottom:1.5px solid var(--border);}
.rank-table td{padding:10px 14px;border-bottom:1px solid #f3f0fa;font-size:.84rem;vertical-align:middle;}
.rank-table tr:last-child td{border-bottom:none;}
.badge-pill{padding:3px 10px;border-radius:50px;font-size:.7rem;font-weight:700;}
.badge-red{background:#fef2f2;color:#b91c1c;}
.badge-orange{background:#fffbeb;color:#92400e;}
.badge-green{background:#ecfdf5;color:#065f46;}

@media(max-width:900px){.kpi-grid{grid-template-columns:repeat(2,1fr);}.overview-row{grid-template-columns:1fr;}}
@media(max-width:500px){.kpi-grid{grid-template-columns:1fr;}.quick-links{grid-template-columns:1fr;}}
</style>

<div class="pg">
    <div class="pg-header">
        <div>
            <h1><i class="fa-solid fa-chart-pie" style="color:var(--brand);margin-right:10px;"></i>Dashboard</h1>
            <p>Good {{ stats.day_name }} — here's your store overview</p>
        </div>
        <a href="/history" class="history-btn">
            <i class="fa-solid fa-clock-rotate-left"></i> Business History
        </a>
    </div>

    <!-- KPI CARDS -->
    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-ico purple"><i class="fas fa-boxes-stacked"></i></div>
            <div class="kpi-label">Current Stock</div>
            <div class="kpi-val">{{ stats.total_qty }}</div>
            <div class="kpi-sub">Total units in inventory</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-ico blue"><i class="fas fa-cart-shopping"></i></div>
            <div class="kpi-label">Sales Today ({{ stats.day_name }})</div>
            <div class="kpi-val">{{ stats.sales_today_count }}</div>
            <div class="kpi-sub">Transactions recorded today</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-ico green"><i class="fas fa-peso-sign"></i></div>
            <div class="kpi-label">Revenue — {{ stats.month_name }}</div>
            <div class="kpi-val" style="font-size:1.3rem;">{{ stats.revenue_month }}</div>
            <div class="kpi-sub">Monthly earnings so far</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-ico red"><i class="fas fa-triangle-exclamation"></i></div>
            <div class="kpi-label">Low Stock Items</div>
            <div class="kpi-val" style="color:var(--red);">{{ stats.low_stock }}</div>
            <div class="kpi-sub">Products below 5 units</div>
        </div>
    </div>

    <!-- QUICK OVERVIEW -->
    <div class="overview-row">
        <!-- QUICK NAVIGATION -->
        <div class="chart-card">
            <div class="chart-head">
                <div class="chart-head-left">
                    <div class="chart-ico"><i class="fas fa-bolt"></i></div>
                    <div>
                        <div class="chart-title">Quick Actions</div>
                        <div class="chart-sub">Jump to any section</div>
                    </div>
                </div>
            </div>
            <div class="chart-body">
                <div class="quick-links">
                    <a href="/inventory" class="ql-item">
                        <div class="ql-ico" style="background:#eff6ff;color:var(--blue);"><i class="fas fa-boxes-stacked"></i></div>
                        <div><div class="ql-label">Inventory</div><div class="ql-desc">View stock levels</div></div>
                    </a>
                    <a href="/sales" class="ql-item">
                        <div class="ql-ico" style="background:#ecfdf5;color:var(--green);"><i class="fas fa-cart-shopping"></i></div>
                        <div><div class="ql-label">Record Sale</div><div class="ql-desc">Log a new transaction</div></div>
                    </a>
                    <a href="/products" class="ql-item">
                        <div class="ql-ico" style="background:#f3eeff;color:var(--brand);"><i class="fas fa-tags"></i></div>
                        <div><div class="ql-label">Products</div><div class="ql-desc">Manage catalog</div></div>
                    </a>
                    <a href="/reports" class="ql-item">
                        <div class="ql-ico" style="background:#fffbeb;color:var(--orange);"><i class="fas fa-file-waveform"></i></div>
                        <div><div class="ql-label">Reports</div><div class="ql-desc">Inventory audit</div></div>
                    </a>
                    <a href="/analytics" class="ql-item">
                        <div class="ql-ico" style="background:#fdf2f8;color:#db2777;"><i class="fas fa-chart-line"></i></div>
                        <div><div class="ql-label">Analytics</div><div class="ql-desc">Sales insights</div></div>
                    </a>
                    <a href="/history" class="ql-item">
                        <div class="ql-ico" style="background:#f0fdf4;color:#16a34a;"><i class="fas fa-clock-rotate-left"></i></div>
                        <div><div class="ql-label">History</div><div class="ql-desc">Past transactions</div></div>
                    </a>
                </div>
            </div>
        </div>

        <!-- LOW STOCK ALERT -->
        <div class="chart-card">
            <div class="chart-head">
                <div class="chart-head-left">
                    <div class="chart-ico" style="background:linear-gradient(135deg,#ef4444,#dc2626);"><i class="fas fa-triangle-exclamation"></i></div>
                    <div>
                        <div class="chart-title">Low Stock Alert</div>
                        <div class="chart-sub">Items below 5 units — restock soon</div>
                    </div>
                </div>
            </div>
            <div class="chart-body" style="padding-top:10px;">
                <table class="rank-table">
                    <thead><tr><th>Product</th><th>Flavor</th><th>Qty</th><th>Status</th></tr></thead>
                    <tbody id="lowStockBody">
                        <tr><td colspan="4" style="text-align:center;color:var(--muted);padding:24px;"><i class="fas fa-spinner fa-spin"></i> Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>

<script>
fetch('/inventory').then(r => r.text()).then(html => {
    // Parse the inventory data from the page's product data
});

// Load low stock items via API
async function loadLowStock() {
    const tbody = document.getElementById('lowStockBody');
    try {
        const resp = await fetch('/api/low_stock');
        const data = await resp.json();
        if (!data.length) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:24px;"><i class="fas fa-check-circle" style="color:var(--green);"></i> All items well stocked!</td></tr>';
            return;
        }
        tbody.innerHTML = data.map(p => `
            <tr>
                <td><strong>${p.name}</strong></td>
                <td style="color:var(--brand);font-weight:600;">${p.flavor || '—'}</td>
                <td><strong style="color:var(--red);">${p.qty}</strong></td>
                <td><span class="badge-pill ${p.qty === 0 ? 'badge-red' : 'badge-orange'}">${p.qty === 0 ? 'Out of Stock' : 'Critical Low'}</span></td>
            </tr>
        `).join('');
    } catch(e) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:18px;">Unable to load stock data.</td></tr>';
    }
}
loadLowStock();
</script>

{% endblock %}
"""

TEMPLATES["inventory.html"] = """
{% extends "base.html" %}

{% block content %}
<style>
    :root {
    --brand:#705194; --brand-light:#f3eeff; --green:#10b981; --red:#ef4444;
    --orange:#f59e0b; --blue:#3b82f6;
    --grad:linear-gradient(135deg,#705194,#9b6fc4);
    --radius:16px; --radius-sm:10px;
    --border:#e8e4f0; --text:#1e293b; --muted:#64748b; --bg:#f8f7ff;
}
    *{box-sizing:border-box;}

    .inventory-container {
        max-width: 100%;
        margin: 0 auto;
        padding: 10px;
    }

    /* --- TOP HEADER --- */
    .header-flex {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 25px;
    }

    .header-title h1 { font-size: 1.7rem; font-weight: 800; color: var(--text); margin: 0; letter-spacing: -0.5px; }
    .header-title p { color: var(--muted); margin: 4px 0 0; font-size: 0.88rem; }

    .notif-bell {
        background: white; width: 45px; height: 45px; border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05); color: #1a2b4b; position: relative;
    }

    /* --- LIST CARD --- */
    .list-card {
        background: white; border-radius: var(--radius); padding: 25px;
        box-shadow: 0 2px 10px rgba(112,81,148,.05); border: 1.5px solid var(--border);
    }

    .list-header {
        display: flex; justify-content: space-between; align-items: center;
        margin-bottom: 25px; flex-wrap: wrap; gap: 15px;
    }

    .search-box { position: relative; width: 100%; max-width: 350px; }
    .search-box i { position: absolute; left: 15px; top: 50%; transform: translateY(-50%); color: #cbd5e1; }
    .search-box input {
        width: 100%; padding: 12px 15px 12px 45px;
        border: 1px solid #e2e8f0; border-radius: 50px; font-size: 0.85rem;
        transition: 0.3s;
    }
    .search-box input:focus { border-color: #705194; outline: none; box-shadow: 0 0 0 3px rgba(112, 81, 148, 0.1); }

    /* --- TABLE STYLING --- */
    .table-responsive { width: 100%; overflow-x: auto; border-radius: 12px; }
    .product-table { width: 100%; border-collapse: collapse; min-width: 1000px; }
    .product-table th { 
        text-align: left; padding: 15px; font-size: 0.65rem; text-transform: uppercase; 
        color: #718096; background: #f8fafc; border-bottom: 1px solid #edf2f7; 
    }
    .product-table td { padding: 15px; border-bottom: 1px solid #f7fafc; vertical-align: middle; }

    .img-cell { width: 48px; height: 48px; border-radius: 10px; background: #f1f5f9; overflow: hidden; }
    .img-cell img { width: 100%; height: 100%; object-fit: cover; }

    .name-cell strong { display: block; color: var(--text); font-size: 0.9rem; font-weight: 700; }
    .flavor-cell { color: #705194; font-weight: 600; font-size: 0.85rem; }

    .badge-cat { background: #e0e7ff; color: #4338ca; padding: 5px 12px; border-radius: 50px; font-size: 0.65rem; font-weight: 800; text-transform: uppercase; }

    /* Stock Status */
    .stock-pill {
        display: inline-flex; align-items: center; gap: 6px;
        padding: 6px 14px; border-radius: 50px; font-weight: 800; font-size: 0.75rem;
    }
    .stock-ok { background: #d1fae5; color: #065f46; }
    .stock-low { background: #fee2e2; color: #991b1b; }
    .stock-out { background: #f3f4f6; color: #4b5563; text-decoration: line-through; }

    .dot { width: 6px; height: 6px; border-radius: 50%; }
    .dot-ok { background: #10b981; }
    .dot-low { background: #ef4444; }

    @media (max-width: 768px) {
        .header-flex { flex-direction: column; align-items: flex-start; gap: 15px; }
        .list-header { flex-direction: column; align-items: stretch; }
        .search-box { max-width: 100%; }
    }
</style>

<div class="inventory-container">
    
    <!-- HEADER -->
    <div class="header-flex">
        <div class="header-title">
            <h1>Inventory</h1>
        </div>
        <div class="notif-bell">
            <i class="fas fa-bell"></i>
        </div>
    </div>

    <!-- MASTER LIST CARD -->
    <div class="list-card">
        <div class="list-header">
            <div style="display:flex; align-items:center; gap:12px;">
                <div style="background: var(--purple-grad); color:white; width:35px; height:35px; border-radius:10px; display:flex; align-items:center; justify-content:center;">
                    <i class="fas fa-boxes" style="font-size:0.8rem;"></i>
                </div>
                <strong style="color: #1a2b4b;">Stock Level Monitor</strong>
            </div>
            
            <div class="search-box">
                <i class="fas fa-search"></i>
                <input type="text" id="invSearch" placeholder="Search product name or flavor..." onkeyup="filterInventory()">
            </div>
        </div>

        <div class="table-responsive">
            <table class="product-table" id="invTable">
                <thead>
                    <tr>
                        <th>Image</th>
                        <th>Product Name</th>
                        <th>Flavor</th>
                        <th>Category</th>
                        <th>Version</th>
                        <th>ML/MG</th>
                        <th>Cost</th>
                        <th>Price</th>
                        <th>Current Stock</th>
                    </tr>
                </thead>
                <tbody>
                    {% for key, p in products.items() %}
                    <tr>
                        <td>
                            <div class="img-cell">
                                <img src="{{ url_for('static', filename='uploads/' + p.image) if p.image else '' }}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" style="width:100%;height:100%;object-fit:cover;"><div style="display:none;width:100%;height:100%;align-items:center;justify-content:center;color:#cbd5e1;font-size:1.2rem;"><i class="fas fa-image"></i></div>
                            </div>
                        </td>
                        <td class="name-cell">
                            <strong>{{ p.name }}</strong>
                        </td>
                        <td class="flavor-cell">
                            {{ p.flavor or '-' }}
                        </td>
                        <td><span class="badge-cat">{{ p.type }}</span></td>
                        <td>{{ p.version or '-' }}</td>
                        <td>{{ p.mg or '-' }}</td>
                        <td style="color: #64748b; font-size: 0.85rem;">₱{{ "{:,.2f}".format(p.cost) }}</td>
                        <td style="color: #1a2b4b; font-weight: 700;">₱{{ "{:,.2f}".format(p.price) }}</td>
                        <td>
                            {% if p.qty <= 0 %}
                                <span class="stock-pill stock-out">
                                    0 PCS
                                </span>
                            {% elif p.qty < 5 %}
                                <span class="stock-pill stock-low">
                                    <div class="dot dot-low"></div>
                                    {{ p.qty }} PCS (Low)
                                </span>
                            {% else %}
                                <span class="stock-pill stock-ok">
                                    <div class="dot dot-ok"></div>
                                    {{ p.qty }} PCS
                                </span>
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>

<script>
    function filterInventory() {
        let input = document.getElementById("invSearch");
        let filter = input.value.toUpperCase();
        let table = document.getElementById("invTable");
        let tr = table.getElementsByTagName("tr");

        for (let i = 1; i < tr.length; i++) {
            // Column 1 is Name, Column 2 is Flavor
            let tdName = tr[i].getElementsByTagName("td")[1];
            let tdFlavor = tr[i].getElementsByTagName("td")[2];
            
            if (tdName && tdFlavor) {
                let nameVal = tdName.textContent || tdName.innerText;
                let flavorVal = tdFlavor.textContent || tdFlavor.innerText;
                
                if (nameVal.toUpperCase().indexOf(filter) > -1 || flavorVal.toUpperCase().indexOf(filter) > -1) {
                    tr[i].style.display = "";
                } else {
                    tr[i].style.display = "none";
                }
            }
        }
    }
</script>
{% endblock %}
"""

TEMPLATES["login.html"] = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Flex Vape | Login</title>
    <link rel="icon" type="image/jpeg" href="/static/images/flex_vape_shop.jpg">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.4.0/css/all.min.css" crossorigin="anonymous" referrerpolicy="no-referrer">
    <style>
        :root {
            --brand-navy: #0f172a;
            --brand-purple: #705194;
            --error-red: #ef4444;
            --bg-soft: #f8fafc;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Outfit', sans-serif; }

        body {
            background-color: #0f172a;
            background-image: radial-gradient(ellipse at 30% 20%, rgba(112,81,148,0.25) 0%, transparent 50%),
                              radial-gradient(ellipse at 80% 80%, rgba(22,33,53,0.8) 0%, transparent 50%),
                              radial-gradient(circle at 2px 2px, rgba(255,255,255,0.04) 1px, transparent 0);
            background-size: auto, auto, 32px 32px;
            display: flex; justify-content: center; align-items: center;
            min-height: 100vh;
            padding: 20px;
        }

        /* --- THE SHAKE ANIMATION --- */
        @keyframes shake-animation {
            10%, 90% { transform: translate3d(-1px, 0, 0); }
            20%, 80% { transform: translate3d(2px, 0, 0); }
            30%, 50%, 70% { transform: translate3d(-4px, 0, 0); }
            40%, 60% { transform: translate3d(4px, 0, 0); }
        }

        .shake { animation: shake-animation 0.5s cubic-bezier(.36,.07,.19,.97) both; }

        .login-card {
            background: rgba(255,255,255,0.04);
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            width: 100%; max-width: 420px;
            padding: 2.8rem;
            border-radius: 28px;
            box-shadow: 0 40px 80px -20px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.08), inset 0 1px 0 rgba(255,255,255,0.1);
            border: 1px solid rgba(112,81,148,0.3);
            text-align: center;
        }

        /* --- LOGO DESIGN --- */
        .logo-wrapper {
            width: 110px; height: 110px;
            margin: 0 auto 1.5rem;
            position: relative;
            border-radius: 50%;
            overflow: hidden;
            box-shadow: 0 0 0 3px rgba(112,81,148,0.6), 0 0 30px rgba(112,81,148,0.4), 0 16px 32px rgba(0,0,0,0.4);
        }

        .error-alert {
            background-color: #fef2f2;
            border: 1px solid #fee2e2;
            color: #991b1b;
            padding: 0.8rem;
            border-radius: 12px;
            margin-bottom: 1.5rem;
            display: flex; align-items: center; justify-content: center;
            gap: 8px; font-size: 0.85rem; font-weight: 600;
        }

        h2 { font-size: 1.6rem; color: #f1f5f9; font-weight: 800; margin-bottom: 0.5rem; letter-spacing: -0.5px; }
        p.subtitle { color: #94a3b8; font-size: 0.95rem; margin-bottom: 2rem; }

        .form-group { text-align: left; margin-bottom: 1.25rem; }
        .form-group label { display: block; font-size: 0.75rem; font-weight: 700; color: #cbd5e1; text-transform: uppercase; margin-bottom: 0.5rem; letter-spacing: 0.5px; }
        
        .input-wrapper { position: relative; }
        .input-wrapper i { position: absolute; left: 16px; top: 50%; transform: translateY(-50%); color: #94a3b8; font-size: 1rem; transition: 0.2s; }
        
        .form-group input {
            width: 100%; padding: 14px 14px 14px 48px;
            border: 1.5px solid rgba(255,255,255,0.1); border-radius: 14px; font-size: 1rem;
            outline: none; transition: 0.2s; background: rgba(255,255,255,0.06); color: #f1f5f9;
        }

        .form-group input:focus { border-color: var(--brand-purple); background: rgba(255,255,255,0.1); color: #f1f5f9; box-shadow: 0 0 0 4px rgba(112, 81, 148, 0.15); }
        .form-group input:-webkit-autofill,
        .form-group input:-webkit-autofill:hover,
        .form-group input:-webkit-autofill:focus {
            -webkit-box-shadow: 0 0 0px 1000px #1e1b2e inset, 0 0 0 4px rgba(112, 81, 148, 0.15);
            -webkit-text-fill-color: #f1f5f9;
            border-color: var(--brand-purple);
            transition: background-color 5000s ease-in-out 0s;
        }
        .form-group input:focus + i { color: var(--brand-purple); }

        .btn-login {
            width: 100%; background: var(--brand-purple); color: white; border: none;
            padding: 16px; border-radius: 14px; font-weight: 700; font-size: 1rem;
            cursor: pointer; transition: 0.3s; margin-top: 1rem;
            box-shadow: 0 10px 15px -3px rgba(112, 81, 148, 0.3);
        }

        .btn-login:hover { background: #5a3d7a; transform: translateY(-1px); box-shadow: 0 20px 25px -5px rgba(112, 81, 148, 0.4); }
        .btn-login:active { transform: translateY(0); }
    </style>
</head>
<body>

    {% with messages = get_flashed_messages() %}
    <div class="login-card {{ 'shake' if messages }}">
        
        <div class="logo-wrapper"><img src="data:image/jpeg;base64,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" alt="F.L.E.X" style="width:100%;height:100%;object-fit:cover;border-radius:50%;"></div>
        
        <h2>F.L.E.X System</h2>
        <p class="subtitle">Enter your admin credentials</p>

        <!-- Display Error Messages -->
        {% if messages %}
            {% for message in messages %}
              <div class="error-alert">
                  <i class="fas fa-circle-exclamation"></i>
                  <span>{{ message }}</span>
              </div>
            {% endfor %}
        {% endif %}

        <form method="POST">
            <div class="form-group">
                <label>Username</label>
                <div class="input-wrapper">
                    <i class="fas fa-user"></i>
                    <input type="text" name="username" placeholder="Admin ID" required autofocus>
                </div>
            </div>

            <div class="form-group">
                <label>Password</label>
                <div class="input-wrapper">
                    <i class="fas fa-lock"></i>
                    <input type="password" name="password" placeholder="••••••••" required>
                </div>
            </div>

            <button type="submit" class="btn-login">Secure Sign In</button>
        </form>
    </div>
    {% endwith %}

</body>
</html>
"""

TEMPLATES["products.html"] = """
{% extends "base.html" %}

{% block content %}
<!-- Include Barcode Generation Library -->
<script src="https://cdn.jsdelivr.net/npm/jsbarcode@3.11.5/dist/JsBarcode.all.min.js"></script>

<style>
    *, *::before, *::after { box-sizing: border-box; }

    :root {
        --brand:#705194; --brand-light:#f3eeff; --green:#10b981; --red:#ef4444;
        --orange:#f59e0b; --blue:#3b82f6;
        --grad:linear-gradient(135deg,#705194,#9b6fc4);
        --surface:#ffffff;
        --bg:#f8f7ff;
        --border:#e8e4f0;
        --text:#1e293b;
        --muted:#64748b;
        --radius:16px;
        --radius-sm:10px;
        --shadow:0 2px 10px rgba(112,81,148,.05);
    }

    body { background: var(--bg); }
    .pg { max-width: 900px; margin: 0 auto; padding: 16px; }

    /* PAGE HEADER */
    .pg-header { margin-bottom: 25px; padding-bottom: 10px; border-bottom: 1.5px solid var(--border); }
    .pg-header h1 { font-size:1.7rem; font-weight:800; color:var(--text); margin:0; letter-spacing:-0.5px; }
    .pg-header p { color:var(--muted); margin:4px 0 0; font-size:0.88rem; }

    /* CARD */
    .card { background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow); border: 1px solid var(--border); margin-bottom: 20px; overflow: hidden; }
    .card-head { padding: 14px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 10px; border-left: 4px solid var(--brand); }
    .card-head .ico { background: var(--grad); color: white; width: 32px; height: 32px; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; flex-shrink: 0; }
    .card-head strong { color: var(--text); font-size: 0.9rem; }

    /* FORM */
    .form-body { padding: 20px; }
    .form-top { display: flex; gap: 20px; margin-bottom: 18px; align-items: center; flex-wrap: wrap; }

    /* Photo */
    .photo-box { width: 110px; height: 110px; border: 2px dashed var(--border); border-radius: var(--radius); display: flex; flex-direction: column; align-items: center; justify-content: center; cursor: pointer; background: var(--bg); overflow: hidden; transition: border-color 0.2s; flex-shrink: 0; }
    .photo-box img { width: 100%; height: 100%; object-fit: cover; display: none; }
    .photo-hint { text-align: center; color: var(--muted); }
    .photo-hint i { font-size: 1.4rem; display: block; margin-bottom: 4px; }
    .photo-hint span { font-size: 0.65rem; text-transform: uppercase; font-weight: 700; }

    /* Fields */
    .right-col { flex: 1; min-width: 180px; }
    .fields-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; }
    @media (max-width: 480px) { .fields-grid { grid-template-columns: 1fr 1fr; } }

    .field { display: flex; flex-direction: column; gap: 5px; }
    .field label { font-size: 0.6rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.6px; color: var(--muted); }
    .field input, .field select { padding: 10px 12px; background: var(--bg); border: 1.5px solid var(--border); border-radius: var(--radius-sm); font-size: 0.85rem; color: var(--text); width: 100%; }
    .field input:focus, .field select:focus { outline: none; border-color: var(--brand); background: white; }

    .form-footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
    .btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; padding: 0 20px; height: 44px; border-radius: var(--radius-sm); font-weight: 700; font-size: 0.85rem; cursor: pointer; border: none; transition: 0.2s; }
    .btn-primary { background: var(--grad); color: white; }
    .btn-ghost { background: var(--bg); color: var(--muted); }

    /* Table */
    .tbl-wrap { overflow-x: auto; }
    .prod-table { width: 100%; border-collapse: collapse; min-width: 700px; }
    .prod-table th { text-align: left; padding: 10px 14px; font-size: 0.6rem; text-transform: uppercase; color: var(--muted); background: var(--bg); border-bottom: 1px solid var(--border); }
    .prod-table td { padding: 11px 14px; border-bottom: 1px solid #eee; vertical-align: middle; font-size: 0.83rem; }
    
    .act-btn { width: 32px; height: 32px; border-radius: 8px; border: 1px solid #e2e8f0; background: white; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; transition: 0.2s; }
    .act-btn:hover { background: #f8fafc; border-color: var(--brand); }
</style>

<div class="pg">
    <div class="pg-header">
        <h1>Products</h1>
        <p>Manage your vape inventory details.</p>
    </div>

    <!-- FORM CARD -->
    <div class="card">
        <div class="card-head">
            <div class="ico"><i class="fas fa-box"></i></div>
            <strong>Product Information</strong>
        </div>

        <form method="POST" enctype="multipart/form-data" id="productForm">
            <input type="hidden" name="editing_key" id="editing_key">
            <input type="hidden" name="action" value="save" id="form_action">
            <input type="hidden" name="barcode" id="barcode">

            <div class="form-body">
                <div class="form-top">
                    <div class="photo-box" onclick="document.getElementById('fileInput').click()">
                        <img id="imgPreview" alt="preview">
                        <div class="photo-hint" id="uploadHint">
                            <i class="fas fa-camera"></i>
                            <span>Upload</span>
                        </div>
                    </div>
                    <input type="file" name="product_image" id="fileInput" hidden onchange="previewImg(this)">

                    <div class="right-col">
                        <div class="field">
                            <label>Product Name / Description</label>
                            <input type="text" name="name" id="name" placeholder="Enter product name..." required>
                        </div>
                    </div>
                </div>

                <div class="fields-grid">
                    <div class="field"><label>Flavor</label><input type="text" name="flavor" id="flavor"></div>
                    <div class="field">
                        <label>Category</label>
                        <select name="type" id="type" required>
                            <option value="pods">Pods</option>
                            <option value="juice">Juice</option>
                            <option value="disposable">Disposable</option>
                            <option value="device">Device</option>
                            <option value="cartridge">Cartridge</option>
                        </select>
                    </div>
                    <div class="field"><label>Version</label><input type="text" name="version" id="version" placeholder="e.g. V2"></div>
                    <div class="field"><label>MG / ML</label><input type="text" name="mg" id="mg" placeholder="e.g. 3% / 30ml"></div>
                    
                    <div class="field" id="qty_group"><label>Initial Qty</label><input type="number" name="quantity" id="quantity" value="0"></div>
                    
                    <div class="field"><label>Selling Price ₱</label><input type="number" step="0.01" name="price" id="price" required></div>
                    <div class="field"><label>Discount %</label><input type="number" step="0.01" name="discount" id="discount" min="0" max="100" placeholder="0" value="0"></div>
                </div>

                <div class="form-footer">
                    <button type="button" class="btn btn-ghost" onclick="resetForm()">Cancel</button>
                    <button type="submit" class="btn-primary btn">Save Product</button>
                </div>
            </div>
        </form>
    </div>

    <!-- MASTERLIST -->
    <div class="card">
        <div class="card-head">
            <strong>Inventory List</strong>
            <div style="margin-left: auto;">
                <input type="text" id="masterSearch" placeholder="Search products..." onkeyup="filterList()" style="padding: 5px 10px; border-radius: 20px; border: 1px solid #ddd; font-size: 0.8rem;">
            </div>
        </div>

        <div class="tbl-wrap">
            <table class="prod-table" id="masterTable">
                <thead>
                    <tr>
                        <th>Product Name</th>
                        <th>Stock</th>
                        <th>Price</th>
                        <th>Discount</th>
                        <th>Final Price</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {% for key, p in products.items() %}
                    <tr>
                        <td>
                            <strong>{{ p.name }}</strong><br>
                            <small style="color:var(--muted)">{{ p.flavor or '' }} {{ '| ' + p.mg if p.mg else '' }}</small>
                        </td>
                        <td style="font-weight:bold; color: {{ 'red' if p.qty < 5 else 'green' }}">{{ p.qty }}</td>
                        <td>₱{{ "{:,.2f}".format(p.price) }}</td>
                        <td>
                            {% if p.discount and p.discount > 0 %}
                            <span style="background:#fef9c3;color:#854d0e;padding:2px 8px;border-radius:12px;font-size:0.78rem;font-weight:700;">{{ p.discount|int }}% OFF</span>
                            {% else %}
                            <span style="color:var(--muted);font-size:0.8rem;">—</span>
                            {% endif %}
                        </td>
                        <td style="font-weight:800;color:var(--brand);">
                            ₱{{ "{:,.2f}".format(p.price * (1 - (p.discount or 0) / 100)) }}
                        </td>
                        <td>
                            <div style="display:flex;gap:5px;">
                                <button class="act-btn" onclick="editProduct('{{ key }}')" style="color:blue;"><i class="fas fa-edit"></i></button>
                                <button class="act-btn" onclick="delProduct('{{ key }}')" style="color:red;"><i class="fas fa-trash"></i></button>
                            </div>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>

<script>
const productsData = {{ products|tojson }};

function previewImg(input) {
    if (!input.files?.[0]) return;
    const r = new FileReader();
    r.onload = e => {
        const img = document.getElementById('imgPreview');
        img.src = e.target.result; img.style.display = 'block';
        document.getElementById('uploadHint').style.display = 'none';
    };
    r.readAsDataURL(input.files[0]);
}

function editProduct(key) {
    const p = productsData[key];
    document.getElementById('editing_key').value = key;
    document.getElementById('barcode').value = p.barcode || '';
    document.getElementById('name').value = p.name;
    document.getElementById('flavor').value = p.flavor || '';
    document.getElementById('type').value = p.type;
    document.getElementById('version').value = p.version || '';
    document.getElementById('mg').value = p.mg || '';
    document.getElementById('price').value = p.price;
    document.getElementById('discount').value = p.discount || 0;
    document.getElementById('qty_group').style.display = 'none';
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function resetForm() {
    document.getElementById('productForm').reset();
    document.getElementById('editing_key').value = '';
    document.getElementById('barcode').value = '';
    document.getElementById('qty_group').style.display = '';
    document.getElementById('imgPreview').style.display = 'none';
    document.getElementById('uploadHint').style.display = 'flex';
}

function delProduct(key) {
    if (!confirm('Delete this product?')) return;
    document.getElementById('editing_key').value = key;
    document.getElementById('form_action').value = 'delete';
    document.getElementById('productForm').submit();
}

function filterList() {
    const q = document.getElementById('masterSearch').value.toUpperCase();
    const rows = document.querySelectorAll('#masterTable tbody tr');
    rows.forEach(r => {
        r.style.display = r.innerText.toUpperCase().includes(q) ? '' : 'none';
    });
}
</script>
{% endblock %}
"""

TEMPLATES["history.html"] = """
{% extends "base.html" %}

{% block content %}
<style>
    :root {
        --brand-navy: #0f172a;
        --brand-purple: #6366f1;
        --brand-green: #10b981;
        --bg-main: #f8fafc;
        --border-color: #e2e8f0;
        --text-main: #1e293b;
        --text-muted: #64748b;
        --card-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
    }

    /* Professional Reset & Wrapper */
    .history-wrapper {
        max-width: 1200px;
        margin: 0 auto;
        padding: 2rem 1rem;
        font-family: 'Inter', -apple-system, sans-serif;
        background-color: var(--bg-main);
        min-height: 100vh;
    }

    /* Top Navigation Area */
    .top-bar {
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
        margin-bottom: 2rem;
        border-bottom: 1px solid var(--border-color);
        padding-bottom: 1.5rem;
    }

    .btn-back {
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        color: var(--text-muted);
        text-decoration: none;
        font-size: 0.875rem;
        font-weight: 500;
        padding: 0.5rem 0.75rem;
        border-radius: 6px;
        transition: all 0.2s ease;
        background: white;
        border: 1px solid var(--border-color);
        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }

    .btn-back:hover {
        background: #f1f5f9;
        color: var(--brand-navy);
        border-color: #cbd5e1;
    }

    .pg-title-group h1 {
        font-size: 1.875rem;
        font-weight: 800;
        color: var(--brand-navy);
        letter-spacing: -0.025em;
        margin: 0;
    }

    .pg-title-group p {
        color: var(--text-muted);
        font-size: 0.95rem;
        margin-top: 0.25rem;
    }

    /* --- GRID SYSTEM --- */
    .history-grid {
        display: grid;
        grid-template-columns: 1fr;
        gap: 1.5rem;
    }

    @media (min-width: 1024px) {
        .history-grid {
            grid-template-columns: 2fr 1fr;
        }
    }

    /* --- CARD STYLING --- */
    .h-card {
        background: #ffffff;
        border-radius: 12px;
        border: 1px solid var(--border-color);
        box-shadow: var(--card-shadow);
        overflow: hidden;
        display: flex;
        flex-direction: column;
    }

    .h-card-header {
        padding: 1.25rem 1.5rem;
        border-bottom: 1px solid var(--border-color);
        background: #fafafa;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }

    .h-card-title {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        font-weight: 700;
        font-size: 0.95rem;
        color: var(--brand-navy);
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    /* --- MODERN TABLE --- */
    .table-container {
        width: 100%;
        overflow-x: auto;
    }

    .h-table {
        width: 100%;
        border-collapse: collapse;
        text-align: left;
    }

    .h-table th {
        background: #ffffff;
        padding: 1rem 1.5rem;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        color: var(--text-muted);
        border-bottom: 1px solid var(--border-color);
    }

    .h-table tr {
        transition: background 0.1s ease;
    }

    .h-table tr:hover {
        background: #f8fafc;
    }

    .h-table td {
        padding: 1.25rem 1.5rem;
        border-bottom: 1px solid #f1f5f9;
        font-size: 0.9rem;
        color: var(--text-main);
    }

    /* Component Details */
    .date-cell {
        font-weight: 600;
        color: var(--brand-navy);
    }

    .count-badge {
        display: inline-block;
        padding: 0.25rem 0.6rem;
        background: #eff6ff;
        color: #2563eb;
        border-radius: 9999px;
        font-size: 0.75rem;
        font-weight: 700;
        border: 1px solid #dbeafe;
    }

    .revenue-text {
        font-family: 'Monaco', 'Consolas', monospace;
        font-weight: 700;
        color: var(--brand-green);
        font-size: 1rem;
    }

    .revenue-secondary {
        font-family: 'Monaco', 'Consolas', monospace;
        font-weight: 700;
        color: var(--brand-navy);
    }

    .empty-state {
        padding: 4rem 2rem;
        text-align: center;
        color: var(--text-muted);
    }

    .info-footer {
        padding: 1rem;
        background: #f1f5f9;
        border-top: 1px solid var(--border-color);
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-size: 0.75rem;
        color: var(--text-muted);
    }

    @media (max-width: 640px) {
        .top-bar {
            flex-direction: column-reverse;
            align-items: flex-start;
            gap: 1rem;
        }
        .revenue-text { font-size: 0.85rem; }
    }
</style>

<div class="history-wrapper">
    
    <!-- HEADER SECTION -->
    <div class="top-bar">
        <div class="pg-title-group">
            <p>Archive & Analytics</p>
            <h1>Business History</h1>
        </div>
        <a href="/" class="btn-back">
            <i class="fa-solid fa-arrow-left"></i>
            <span>Back to Dashboard</span>
        </a>
    </div>

    <div class="history-grid">
        
        <!-- MAIN SECTION: DAILY LOGS -->
        <div class="h-card">
            <div class="h-card-header">
                <div class="h-card-title">
                    <i class="fa-solid fa-calendar-check" style="color: var(--brand-purple);"></i>
                    Daily Performance Log
                </div>
            </div>
            
            <div class="table-container">
                <table class="h-table">
                    <thead>
                        <tr>
                            <th>Reporting Date</th>
                            <th style="text-align:center;">Txn Count</th>
                            <th style="text-align:right;">Gross Revenue</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for log in daily %}
                        <tr>
                            <td class="date-cell">{{ log.day }}</td>
                            <td style="text-align:center;">
                                <span class="count-badge">{{ log.count }} Sales</span>
                            </td>
                            <td style="text-align:right;">
                                <span class="revenue-text">₱{{ "{:,.2f}".format(log.revenue) }}</span>
                            </td>
                        </tr>
                        {% else %}
                        <tr>
                            <td colspan="3" class="empty-state">
                                <i class="fa-solid fa-inbox fa-2x" style="margin-bottom: 1rem; opacity: 0.3;"></i>
                                <p>No historical records found for the current period.</p>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- SIDE SECTION: MONTHLY TRENDS -->
        <div class="h-card">
            <div class="h-card-header">
                <div class="h-card-title">
                    <i class="fa-solid fa-chart-line" style="color: var(--brand-purple);"></i>
                    Monthly Growth
                </div>
            </div>

            <div class="table-container">
                <table class="h-table">
                    <thead>
                        <tr>
                            <th>Period</th>
                            <th style="text-align: right;">Total</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for log in monthly %}
                        <tr>
                            <td>
                                <div style="font-weight: 700; color: var(--brand-navy);">{{ log.month }}</div>
                                <div style="font-size: 0.75rem; color: var(--text-muted);">Fiscal Year {{ log.year }}</div>
                            </td>
                            <td style="text-align: right;">
                                <span class="revenue-secondary">₱{{ "{:,.2f}".format(log.revenue) }}</span>
                            </td>
                        </tr>
                        {% else %}
                        <tr>
                            <td colspan="2" class="empty-state">No monthly data.</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            
            <div class="info-footer">
                <i class="fa-solid fa-circle-info"></i>
                Data is finalized daily at 23:59 (Server Time).
            </div>
        </div>

    </div>
</div>
{% endblock %}
"""

TEMPLATES["reports.html"] = """
{% extends "base.html" %}

{% block content %}
<script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js" crossorigin="anonymous" referrerpolicy="no-referrer"></script>

<style>
    :root {
        --brand:#705194; --brand-light:#f3eeff; --green:#10b981; --red:#ef4444;
        --orange:#f59e0b; --blue:#3b82f6;
        --grad:linear-gradient(135deg,#705194,#9b6fc4);
        --bg:#f8f7ff;
        --border:#e8e4f0; --text:#1e293b; --muted:#64748b;
        --radius:16px; --radius-sm:10px;
        --shadow:0 2px 10px rgba(112,81,148,.05);
        /* legacy aliases */
        --brand-navy: #162135;
        --brand-purple: #705194;
        --brand-green: #10b981;
        --brand-red: #ef4444;
        --soft-bg: #f8f7ff;
        --border-light: #e8e4f0;
    }

    /* --- PAGE UI WRAPPER --- */
    .report-ui-wrapper { max-width: 900px; margin: 0 auto; padding: 10px; }

    /* --- RESPONSIVE CONTROLS --- */
    .report-controls {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: white;
        padding: 12px;
        border-radius: var(--radius);
        margin-bottom: 20px;
        border: 1.5px solid var(--border);
        box-shadow: var(--shadow);
        flex-wrap: wrap; 
        gap: 12px;
    }

    .period-selector { 
        display: flex; 
        background: #f1f5f9; 
        padding: 4px; 
        border-radius: 8px; 
        flex: 1; 
        min-width: 200px; 
    }
    .period-btn { 
        text-decoration: none; 
        padding: 8px 12px; 
        flex: 1; 
        text-align: center; 
        border-radius: 6px; 
        font-size: 0.8rem; 
        font-weight: 600; 
        color: #64748b; 
        transition: 0.2s; 
    }
    .period-btn.active { background: white; color: var(--brand); box-shadow: 0 2px 10px rgba(112,81,148,.1); font-weight:700; }

    .btn-group { 
        display: flex; 
        gap: 8px; 
        flex: 1; 
        justify-content: flex-end; 
        min-width: 200px; 
    }
    .btn-action { 
        flex: 1; 
        border: none; 
        padding: 10px; 
        border-radius: 8px; 
        font-weight: 700; 
        cursor: pointer; 
        display: flex; 
        align-items: center; 
        justify-content: center; 
        gap: 6px; 
        font-size: 0.8rem; 
        color: white; 
    }
    .btn-pdf { background: #475569; }
    .btn-img { background: var(--brand-purple); }

    /* --- THE OFFICIAL REPORT DOCUMENT --- */
    #report-capture-area {
        background: white;
        width: 100%;
        margin: 0 auto;
        padding: 5vw; /* Fluid padding based on screen width */
        color: var(--brand-navy);
        font-family: 'Inter', sans-serif;
        border: 1px solid var(--border-light);
        position: relative;
        box-sizing: border-box;
    }

    /* CENTERED LOGO HEADER */
    .doc-header {
        text-align: center;
        border-bottom: 2px solid var(--brand-navy);
        padding-bottom: 20px;
        margin-bottom: 30px;
    }

    .brand-info h2 { margin: 0; font-size: clamp(1.1rem, 4vw, 1.6rem); font-weight: 800; letter-spacing: 1px; }
    .brand-info p { margin: 5px 0 0; font-size: clamp(0.7rem, 2vw, 0.85rem); color: #64748b; text-transform: uppercase; }
    .report-type-label { margin-top: 15px; font-size: clamp(0.9rem, 3vw, 1.1rem); font-weight: 700; color: var(--brand-purple); text-transform: uppercase; }
    .report-date { font-size: 0.8rem; color: #94a3b8; margin-top: 5px; }

    /* Highlights Section */
    .report-grid { 
        display: grid; 
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); 
        gap: 15px; 
        margin-bottom: 30px; 
    }
    .stat-card { background: var(--soft-bg); padding: 15px; border-radius: 12px; border: 1px solid var(--border-light); text-align: center;}
    .stat-card label { display: block; font-size: 0.6rem; font-weight: 800; color: #94a3b8; text-transform: uppercase; margin-bottom: 5px; }
    .stat-card .value { font-size: clamp(1.1rem, 4vw, 1.6rem); font-weight: 800; }
    .stat-card .value.green { color: var(--brand-green); }

    /* Movement Table */
    .table-responsive {
        width: 100%;
        overflow-x: auto; 
        -webkit-overflow-scrolling: touch;
        margin-bottom: 25px;
        border-radius: 8px;
    }
    
    /* Swipe Hint for Mobile */
    .swipe-hint { display: none; font-size: 0.65rem; color: #94a3b8; margin-bottom: 5px; text-align: right; font-style: italic; }

    .report-table { width: 100%; border-collapse: collapse; min-width: 500px; }
    .report-table th { background: #f1f5f9; text-align: left; padding: 10px; font-size: 0.7rem; color: #475569; border: 1px solid var(--border-light); }
    .report-table td { padding: 10px; font-size: 0.8rem; border: 1px solid var(--border-light); }

    /* Alerts */
    .section-heading { font-size: 0.75rem; font-weight: 800; text-transform: uppercase; margin-bottom: 12px; color: #475569; display: flex; align-items: center; gap: 8px;}
    .section-heading::after { content: ""; flex: 1; height: 1px; background: var(--border-light); }
    .warning-box { background: #fff1f2; border: 1px solid #ffe4e6; border-radius: 12px; padding: 12px; }
    .warning-item { font-size: 0.75rem; font-weight: 600; color: #991b1b; display: flex; justify-content: space-between; padding: 4px 0; }

    .doc-footer { margin-top: 30px; padding-top: 15px; border-top: 1px solid var(--border-light); display: flex; justify-content: space-between; font-size: 0.6rem; color: #94a3b8; flex-wrap: wrap; gap: 8px; }

    /* --- MOBILE BREAKPOINT --- */
    @media (max-width: 600px) {
        .report-ui-wrapper { padding: 5px; }
        .report-controls { padding: 10px; border-radius: 0; margin-left: -5px; margin-right: -5px; }
        .swipe-hint { display: block; }
        #report-capture-area { padding: 20px 15px; border-left: none; border-right: none; }
        .btn-group { min-width: 100%; }
        .period-selector { min-width: 100%; }
    }

    /* --- PRINT FIXES --- */
    @media print {
        nav, .sidebar, .mobile-header, .mobile-toggle, .no-print, header, .swipe-hint { display: none !important; }
        body { background: white; margin: 0; padding: 0; }
        .main-content { margin-left: 0 !important; width: 100% !important; padding: 0 !important; }
        #report-capture-area { border: none; box-shadow: none; padding: 40px; width: 100%; }
        .table-responsive { overflow: visible !important; }
    }
</style>

<div class="report-ui-wrapper">
    
    <!-- Controls -->
    <div class="report-controls no-print">
        <div class="period-selector">
            <a href="/reports?period=daily" class="period-btn {{ 'active' if period == 'daily' }}">Daily Audit</a>
            <a href="/reports?period=weekly" class="period-btn {{ 'active' if period == 'weekly' }}">Weekly Audit</a>
        </div>

        <div class="btn-group">
            <button onclick="window.print()" class="btn-action btn-pdf">
                <i class="fas fa-file-pdf"></i> PDF
            </button>
            <button onclick="downloadReportImage()" class="btn-action btn-img">
                <i class="fas fa-image"></i> IMAGE
            </button>
        </div>
    </div>

    <!-- The Document -->
    <div id="report-capture-area">
        <div class="doc-header">
            <div class="brand-info">
                <h2>F.L.E.X VAPE SHOP</h2>
                <p>Inventory Management System</p>
            </div>
            
            <div class="report-type-label">{{ report_label }}</div>
            <div class="report-date">Issued: {{ date }}</div>
        </div>

        <div class="report-grid">
            <div class="stat-card">
                <label>Gross Revenue</label>
                <div class="value green">₱{{ "{:,.2f}".format(revenue) }}</div>
            </div>
            <div class="stat-card">
                <label>Sales Volume</label>
                <div class="value">{{ sales_count }} Sales</div>
            </div>
        </div>

        <div class="section-heading">Stock Movement Summary</div>
        <div class="swipe-hint">Swipe table to see more &rarr;</div>
        <div class="table-responsive">
            <table class="report-table">
                <thead>
                    <tr>
                        <th>Product & Flavor</th>
                        <th style="text-align:center;">Open</th>
                        <th style="text-align:center;">In</th>
                        <th style="text-align:center;">Out</th>
                        <th style="text-align:center;">End</th>
                    </tr>
                </thead>
                <tbody>
                    {% for item in movement %}
                    <tr>
                        <td><strong>{{ item.name }}</strong></td>
                        <td style="text-align:center;">{{ item.open }}</td>
                        <td style="text-align:center; color: var(--brand-green); font-weight: 700;">+{{ item.new }}</td>
                        <td style="text-align:center; color: var(--brand-red); font-weight: 700;">-{{ item.sold }}</td>
                        <td style="text-align:center; font-weight: 700;">{{ item.end }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        {% if low_stocks %}
        <div class="section-heading" style="color: var(--brand-red);">Critical Stock Warnings</div>
        <div class="warning-box">
            {% for cat, items in low_stocks.items() %}
                {% for item in items %}
                <div class="warning-item">
                    <span>{{ item.name }} ({{ item.flavor }})</span>
                    <span>{{ item.qty }} UNITS LEFT</span>
                </div>
                {% endfor %}
            {% endfor %}
        </div>
        {% endif %}

        <div class="doc-footer">
            <span>Auth: {{ now }}</span>
            <span>F.L.E.X System &bull; Inventory Record</span>
        </div>
    </div>
</div>

<script>
async function downloadReportImage() {
    const reportArea = document.getElementById('report-capture-area');
    const downloadBtn = document.querySelector('.btn-img');
    
    downloadBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>...';
    downloadBtn.disabled = true;

    try {
        const canvas = await html2canvas(reportArea, {
            scale: 3, 
            useCORS: true,
            backgroundColor: "#ffffff",
        });

        const link = document.createElement('a');
        link.href = canvas.toDataURL("image/png", 1.0);
        link.download = `FLEX_Report_{{ date }}.png`;
        link.click();
    } catch (err) {
        alert("Export failed.");
    } finally {
        downloadBtn.innerHTML = '<i class="fas fa-image"></i> IMAGE';
        downloadBtn.disabled = false;
    }
}
</script>
{% endblock %}
"""

TEMPLATES["sales.html"] = """
{% extends "base.html" %}

{% block content %}
<style>
    *, *::before, *::after { box-sizing: border-box; }

    :root {
        --brand:#705194; --brand-light:#f3eeff; --green:#10b981; --red:#ef4444;
        --orange:#f59e0b; --blue:#3b82f6;
        --grad:linear-gradient(135deg,#705194,#9b6fc4);
        --surface:#ffffff;
        --bg:#f8f7ff;
        --border:#e8e4f0;
        --text:#1e293b;
        --muted:#64748b;
        --radius:16px;
        --radius-sm:10px;
        --shadow:0 2px 10px rgba(112,81,148,.05);
    }

    body { background: var(--bg); }
    .pg { max-width: 900px; margin: 0 auto; padding: 16px; }

    /* PAGE HEADER */
    .pg-header { margin-bottom: 20px; }
    .pg-header h1 { font-size:1.7rem; font-weight:800; color:var(--text); margin:0; letter-spacing:-0.5px; }
    .pg-header p { color:var(--muted); margin:4px 0 0; font-size:0.88rem; }

    /* CARD */
    .card { background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow); border: 1px solid var(--border); margin-bottom: 20px; overflow: hidden; }
    .card-head { padding: 14px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 10px; border-left: 4px solid var(--brand); }
    .card-head .ico { background: var(--grad); color: white; width: 32px; height: 32px; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; flex-shrink: 0; }
    .card-head strong { color: var(--text); font-size: 0.9rem; }

    /* FORM BODY */
    .form-body { padding: 20px; }

    .selected-badge {
        background: #f0fdf4; border: 1.5px solid #6ee7b7; border-radius: var(--radius-sm);
        padding: 10px 14px; font-size: 0.82rem; color: #065f46; font-weight: 700;
        display: none; align-items: center; gap: 8px; margin-bottom: 15px;
    }
    .selected-badge.show { display: flex; }

    .fields-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
    @media (max-width: 450px) { .fields-row { grid-template-columns: 1fr; } }

    .field { display: flex; flex-direction: column; gap: 5px; }
    .field label { font-size: 0.6rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.6px; color: var(--muted); }
    .field input { padding: 10px 12px; background: var(--bg); border: 1.5px solid var(--border); border-radius: var(--radius-sm); font-size: 0.9rem; color: var(--text); width: 100%; }
    .field input:focus { outline: none; border-color: var(--brand); background: white; }

    .total-box {
        background: linear-gradient(135deg, #ede9fe, var(--brand-light));
        border: 1.5px solid #c4b5fd;
        border-radius: var(--radius-sm);
        padding: 12px;
        font-size: 1.3rem;
        font-weight: 900;
        color: #4c1d95;
        text-align: center;
        display: flex; align-items: center; justify-content: center;
    }

    /* Search results */
    .search-wrap { position: relative; }
    .search-results {
        position: absolute; top: 100%; left: 0; right: 0;
        background: white; border-radius: var(--radius-sm);
        box-shadow: 0 10px 30px rgba(0,0,0,0.12);
        max-height: 200px; overflow-y: auto;
        z-index: 200; display: none; margin-top: 4px; border: 1.5px solid var(--border);
    }
    .s-item { padding: 10px 14px; cursor: pointer; border-bottom: 1px solid var(--bg); }
    .s-item:hover { background: var(--brand-light); }
    .s-item strong { display: block; font-size: 0.85rem; color: var(--text); }
    .s-item small { font-size: 0.72rem; color: var(--muted); }

    .form-footer { display: flex; gap: 10px; margin-top: 20px; }
    .btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; padding: 0 20px; height: 46px; border-radius: var(--radius-sm); font-weight: 700; font-size: 0.88rem; cursor: pointer; border: none; transition: 0.2s; }
    .btn-primary { background: var(--grad); color: white; flex: 1; }
    .btn-primary:disabled { opacity: 0.45; pointer-events: none; }
    .btn-ghost { background: var(--bg); color: var(--muted); }

    /* HISTORY */
    .log-table { width: 100%; border-collapse: collapse; }
    .log-table th { text-align: left; padding: 10px 14px; font-size: 0.6rem; text-transform: uppercase; color: var(--muted); background: var(--bg); border-bottom: 1px solid var(--border); }
    .log-table td { padding: 11px 14px; border-bottom: 1px solid var(--bg); font-size: 0.83rem; }
    .log-table tr:hover td { background: #faf9ff; }

    .toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%) translateY(80px); background: #1a1a2e; color: white; padding: 12px 22px; border-radius: 50px; font-size: 0.83rem; font-weight: 600; box-shadow: 0 8px 30px rgba(0,0,0,0.2); z-index: 9999; opacity: 0; transition: all 0.3s ease; }
    .toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
</style>

<div class="pg">
    <div class="pg-header">
        <h1>Sales</h1>
        <p>Search for a product to record a transaction.</p>
    </div>

    <!-- TRANSACTION CARD -->
    <div class="card">
        <div class="card-head">
            <div class="ico"><i class="fas fa-shopping-cart"></i></div>
            <strong>New Transaction</strong>
        </div>

        <form method="POST" id="saleForm" autocomplete="off">
            <div class="form-body">
                <!-- Selected Product Badge -->
                <div class="selected-badge" id="selectedBadge">
                    <i class="fas fa-check-circle"></i>
                    <span id="badgeText">Product selected</span>
                </div>

                <!-- Search Field -->
                <div class="field search-wrap">
                    <label>Search Product Name</label>
                    <input type="text" id="productSearch" placeholder="Type product name or flavor..." oninput="filterProducts()">
                    <input type="hidden" name="product_key" id="hiddenKey" required>
                    <div id="searchResults" class="search-results"></div>
                </div>

                <!-- Chillax Infinite Set Variant Picker -->
                <div id="chillaxPicker" style="display:none; margin-top:14px;">
                    <!-- Step 1: Type -->
                    <div id="chillaxStep1">
                        <div style="font-size:0.7rem;font-weight:800;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);margin-bottom:8px;">Choose Variant</div>
                        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
                            <button type="button" class="chillax-variant-btn" id="btnPodDevice"
                                onclick="chillaxSelectType('pod_device')"
                                style="padding:14px 10px;border-radius:12px;border:2px solid var(--border);background:white;cursor:pointer;font-weight:700;font-size:0.88rem;transition:.2s;display:flex;flex-direction:column;align-items:center;gap:4px;">
                                <span style="font-size:1.3rem;">📦</span>
                                <span>Pod &amp; Device</span>
                                <span style="color:var(--brand);font-size:1rem;font-weight:900;">₱600</span>
                            </button>
                            <button type="button" class="chillax-variant-btn" id="btnPod"
                                onclick="chillaxSelectType('pod')"
                                style="padding:14px 10px;border-radius:12px;border:2px solid var(--border);background:white;cursor:pointer;font-weight:700;font-size:0.88rem;transition:.2s;display:flex;flex-direction:column;align-items:center;gap:4px;">
                                <span style="font-size:1.3rem;">🫧</span>
                                <span>Pod Only</span>
                                <span style="color:var(--brand);font-size:1rem;font-weight:900;">₱450</span>
                            </button>
                        </div>
                    </div>
                    <!-- Step 2: Flavor -->
                    <div id="chillaxStep2" style="display:none; margin-top:12px;">
                        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
                            <button type="button" onclick="chillaxBackToStep1()"
                                style="background:none;border:none;cursor:pointer;color:var(--muted);font-size:0.8rem;display:flex;align-items:center;gap:4px;padding:0;">
                                <i class="fas fa-chevron-left"></i> Back
                            </button>
                            <div style="font-size:0.7rem;font-weight:800;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);">Choose Flavor</div>
                            <span id="chillaxVariantLabel" style="font-size:0.7rem;font-weight:700;color:var(--brand);background:var(--brand-light);padding:2px 8px;border-radius:20px;"></span>
                        </div>
                        <div id="chillaxFlavorList" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;max-height:260px;overflow-y:auto;"></div>
                    </div>
                </div>

                <!-- Qty, Discount, and Total Row -->
                <div class="fields-row">
                    <div class="field">
                        <label>Quantity</label>
                        <input type="number" name="quantity" id="qtyInput" value="" min="1" oninput="calcTotal()">
                    </div>
                    <div class="field">
                        <label>Discount % <span style="font-size:0.72rem;color:var(--muted);font-weight:400;">(additional)</span></label>
                        <input type="number" name="manual_discount" id="manualDiscount" value="0" min="0" max="100" step="0.01" oninput="calcTotal()" style="border: 1.5px solid #f59e0b; background: #fffbeb;">
                    </div>
                    <div class="field">
                        <label>Total Price</label>
                        <div class="total-box" id="totalBox">₱ 0.00</div>
                    </div>
                </div>

                <div class="form-footer">
                    <button type="button" class="btn btn-ghost" onclick="clearSale()">Clear</button>
                    <button type="submit" class="btn btn-primary" id="saleBtn" disabled>
                        <i class="fas fa-check-circle"></i> Complete Sale
                    </button>
                </div>
            </div>
        </form>
    </div>

    <!-- RECENT SALES -->
    <div class="card">
        <div class="card-head">
            <div class="ico"><i class="fas fa-history"></i></div>
            <strong>Recent History</strong>
        </div>
        <div style="overflow-x: auto;">
            <table class="log-table">
                <thead>
                    <tr><th>Time</th><th>Product</th><th>Qty</th><th>Total</th></tr>
                </thead>
                <tbody>
                    {% for log in logs %}
                    <tr>
                        <td style="color:var(--muted); font-size:0.75rem;">{{ log.date.strftime('%H:%M') }}</td>
                        <td>
                            <strong>{{ log.name }}</strong><br>
                            <small style="color:var(--brand);">{{ log.flavor }}</small>
                        </td>
                        <td>{{ log.qty }}</td>
                        <td style="color:var(--green); font-weight:800;">₱{{ "{:,.2f}".format(log.qty * log.price) }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>

<div class="toast" id="toast"></div>

<script>
const productsData = {{ products|tojson }};

// ── Chillax Infinite Set: codename → flavor display name ──────────────────
const CHILLAX_FLAVOR_MAP = {
    'thunder blaze':    'Gatorade',
    'very mellow':      'Watermelon',
    'violet fusion':    'Taro Ice Cream',
    'silver dynasty':   'Menthol Ice',
    'cosmic crush':     'Grape',
    'pink harmony':     'Juice Strawberry',
    'rustic haze':      'Classic Tobacco',
    'crystal wink':     'Bubblegum',
    'click kick':       'Sour Apple',
    'twilight willow':  'Blackcurrant',
};

// Emoji per flavor for extra flair
const CHILLAX_FLAVOR_EMOJI = {
    'thunder blaze':'⚡','very mellow':'🍉','violet fusion':'🍦',
    'silver dynasty':'❄️','cosmic crush':'🍇','pink harmony':'🍓',
    'rustic haze':'🚬','crystal wink':'🫧','click kick':'🍏','twilight willow':'🫐',
};

let chillaxSelectedType = null; // 'pod_device' | 'pod'

function isChillaxQuery(q) {
    return q.length >= 3 && 'chillax infinite set'.includes(q.toLowerCase().trim()) ||
           q.toLowerCase().includes('chillax');
}

function getChillaxProducts() {
    return Object.entries(productsData).filter(([id, p]) =>
        p.name.toLowerCase().includes('chillax') ||
        (p.name.toLowerCase().includes('infinite') && p.name.toLowerCase().includes('set'))
    );
}

function showToast(msg, color = '#10b981') {
    const t = document.getElementById('toast');
    t.textContent = msg; t.style.borderBottom = `3px solid ${color}`;
    t.className = 'toast show';
    setTimeout(() => { t.className = 'toast'; }, 2500);
}

function selectItem(id, label, price, stock, discount) {
    const productDiscount = discount || 0;
    const finalPrice = price * (1 - productDiscount / 100);
    document.getElementById('hiddenKey').value = id;
    document.getElementById('productSearch').value = label;
    document.getElementById('searchResults').style.display = 'none';
    document.getElementById('chillaxPicker').style.display = 'none';

    const discountNote = productDiscount > 0 ? ` — ${productDiscount}% OFF → ₱${finalPrice.toLocaleString(undefined,{minimumFractionDigits:2})}` : '';
    document.getElementById('badgeText').textContent = `${label} (In Stock: ${stock})${discountNote}`;
    document.getElementById('selectedBadge').classList.add('show');

    document.getElementById('qtyInput').dataset.basePrice = price;
    document.getElementById('qtyInput').dataset.productDiscount = productDiscount;
    document.getElementById('qtyInput').max = stock;
    document.getElementById('qtyInput').value = 1;
    document.getElementById('manualDiscount').value = 0;
    document.getElementById('saleBtn').disabled = false;
    calcTotal();
}

function filterProducts() {
    const q = document.getElementById('productSearch').value.toLowerCase().trim();
    const div = document.getElementById('searchResults');
    const picker = document.getElementById('chillaxPicker');
    document.getElementById('saleBtn').disabled = true;

    if (q.length < 1) {
        div.style.display = 'none';
        picker.style.display = 'none';
        return;
    }

    // ── Special Chillax Infinite Set flow ─────────────────────────────────
    if (isChillaxQuery(q)) {
        div.style.display = 'none';
        picker.style.display = 'block';
        // Reset to step 1 each time the user types
        document.getElementById('chillaxStep1').style.display = 'block';
        document.getElementById('chillaxStep2').style.display = 'none';
        chillaxSelectedType = null;
        // Reset variant button highlights
        document.querySelectorAll('.chillax-variant-btn').forEach(b => {
            b.style.borderColor = 'var(--border)';
            b.style.background = 'white';
            b.style.color = 'var(--text)';
        });
        return;
    }

    // ── Normal product search ──────────────────────────────────────────────
    picker.style.display = 'none';
    const matches = Object.entries(productsData).filter(([id, p]) =>
        p.name.toLowerCase().includes(q) || (p.flavor||'').toLowerCase().includes(q)
    );

    div.innerHTML = matches.map(([id, p]) => `
        <div class="s-item" onclick="selectItem('${id}','${p.name} - ${p.flavor}',${p.price},${p.qty},${p.discount||0})">
            <strong>${p.name} <span style="color:var(--brand)">${p.flavor||''}</span></strong>
            <small>Stock: ${p.qty} | ₱${p.price.toLocaleString()}${p.discount > 0 ? ` <span style="color:#f59e0b;font-weight:700;">(-${p.discount}%)</span>` : ''}</small>
        </div>
    `).join('');
    div.style.display = matches.length ? 'block' : 'none';
}

// Step 1 → Step 2: user picked Pod & Device or Pod
function chillaxSelectType(type) {
    chillaxSelectedType = type;
    const targetPrice = type === 'pod_device' ? 600 : 450;
    const label = type === 'pod_device' ? 'Pod & Device — ₱600' : 'Pod Only — ₱450';

    // Highlight selected button
    document.querySelectorAll('.chillax-variant-btn').forEach(b => {
        b.style.borderColor = 'var(--border)';
        b.style.background = 'white';
        b.style.color = 'var(--text)';
    });
    const activeBtn = document.getElementById(type === 'pod_device' ? 'btnPodDevice' : 'btnPod');
    activeBtn.style.borderColor = 'var(--brand)';
    activeBtn.style.background = 'var(--brand-light)';
    activeBtn.style.color = 'var(--brand)';

    // Build flavor list
    // Filter Chillax products by price matching target (or show all if no price match — fallback)
    let chillaxProds = getChillaxProducts();
    let priceMatched = chillaxProds.filter(([id, p]) => Math.round(p.price) === targetPrice);
    const flavors = priceMatched.length ? priceMatched : chillaxProds;

    const flavorList = document.getElementById('chillaxFlavorList');
    flavorList.innerHTML = flavors.map(([id, p]) => {
        const codeKey = (p.flavor || '').toLowerCase().trim();
        const flavorName = CHILLAX_FLAVOR_MAP[codeKey] || p.flavor;
        const emoji = CHILLAX_FLAVOR_EMOJI[codeKey] || '🌿';
        const codename = p.flavor || '';
        const safeLabel = `Chillax Infinite Set (${type === 'pod_device' ? 'Pod & Device' : 'Pod'}) - ${codename}`;
        return `
        <div onclick="selectItem('${id}','${safeLabel.replace(/'/g,"\\'")}',${p.price},${p.qty},${p.discount||0})"
             style="padding:10px 12px;border-radius:10px;border:1.5px solid var(--border);background:white;cursor:pointer;transition:.2s;"
             onmouseover="this.style.borderColor='var(--brand)';this.style.background='var(--brand-light)';"
             onmouseout="this.style.borderColor='var(--border)';this.style.background='white';">
            <div style="font-size:1.1rem;margin-bottom:2px;">${emoji}</div>
            <div style="font-size:0.78rem;font-weight:800;color:var(--text);">${codename}</div>
            <div style="font-size:0.7rem;color:var(--brand);font-weight:600;">${flavorName}</div>
            <div style="font-size:0.68rem;color:var(--muted);margin-top:2px;">Stock: ${p.qty}</div>
        </div>`;
    }).join('');

    document.getElementById('chillaxVariantLabel').textContent = label;
    document.getElementById('chillaxStep1').style.display = 'none';
    document.getElementById('chillaxStep2').style.display = 'block';
}

function chillaxBackToStep1() {
    document.getElementById('chillaxStep1').style.display = 'block';
    document.getElementById('chillaxStep2').style.display = 'none';
    chillaxSelectedType = null;
}

function calcTotal() {
    const qty = parseInt(document.getElementById('qtyInput').value) || 0;
    const basePrice = parseFloat(document.getElementById('qtyInput').dataset.basePrice) || 0;
    const productDiscount = parseFloat(document.getElementById('qtyInput').dataset.productDiscount) || 0;
    const manualDiscount = parseFloat(document.getElementById('manualDiscount').value) || 0;
    const totalDiscount = Math.min(productDiscount + manualDiscount, 100);
    const finalPrice = basePrice * (1 - totalDiscount / 100);

    let label = `₱ ${(qty * finalPrice).toLocaleString(undefined,{minimumFractionDigits:2})}`;
    if (totalDiscount > 0) {
        label += ` <span style="font-size:0.75rem;color:#f59e0b;font-weight:700;">(${totalDiscount.toFixed(1)}% OFF)</span>`;
    }
    document.getElementById('totalBox').innerHTML = label;
}

function clearSale() {
    document.getElementById('hiddenKey').value = '';
    document.getElementById('productSearch').value = '';
    document.getElementById('qtyInput').value = 1;
    document.getElementById('qtyInput').dataset.basePrice = 0;
    document.getElementById('qtyInput').dataset.productDiscount = 0;
    document.getElementById('manualDiscount').value = 0;
    document.getElementById('totalBox').innerHTML = '₱ 0.00';
    document.getElementById('selectedBadge').classList.remove('show');
    document.getElementById('saleBtn').disabled = true;
    document.getElementById('chillaxPicker').style.display = 'none';
    document.getElementById('chillaxStep1').style.display = 'block';
    document.getElementById('chillaxStep2').style.display = 'none';
}

window.addEventListener('click', e => {
    if (!e.target.closest('#productSearch') && !e.target.closest('#chillaxPicker')) {
        document.getElementById('searchResults').style.display = 'none';
    }
});
</script>
{% endblock %}
"""

TEMPLATES["analytics.html"] = """
{% extends 'base.html' %}
{% block content %}
<style>
:root {
    --brand:#705194; --brand-light:#f3eeff; --green:#10b981; --red:#ef4444;
    --orange:#f59e0b; --blue:#3b82f6;
    --grad:linear-gradient(135deg,#705194,#9b6fc4);
    --radius:16px; --radius-sm:10px;
    --border:#e8e4f0; --text:#1e293b; --muted:#64748b; --bg:#f8f7ff;
}
*{box-sizing:border-box;}
.pg{max-width:1100px;margin:0 auto;padding:0 0 60px;}
.pg-header{margin-bottom:28px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;}
.pg-header h1{font-size:1.7rem;font-weight:800;color:var(--text);}
.pg-header p{color:var(--muted);font-size:0.9rem;margin-top:4px;}
.period-tabs{display:flex;gap:6px;}
.period-tab{padding:8px 16px;border-radius:50px;border:1.5px solid var(--border);background:white;font-size:0.82rem;font-weight:700;color:var(--muted);cursor:pointer;transition:.2s;}
.period-tab.active,.period-tab:hover{background:var(--grad);color:white;border-color:transparent;}

/* KPI CARDS */
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px;}
.kpi-card{background:white;border-radius:var(--radius);border:1.5px solid var(--border);padding:20px 22px;box-shadow:0 2px 10px rgba(112,81,148,.05);}
.kpi-card .kpi-label{font-size:0.72rem;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:700;margin-bottom:8px;}
.kpi-card .kpi-val{font-size:1.65rem;font-weight:800;color:var(--text);line-height:1;}
.kpi-card .kpi-sub{font-size:0.78rem;color:var(--muted);margin-top:6px;}
.kpi-card .kpi-ico{width:38px;height:38px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:1rem;margin-bottom:12px;}
.kpi-ico.purple{background:#f3eeff;color:var(--brand);}
.kpi-ico.green{background:#ecfdf5;color:var(--green);}
.kpi-ico.orange{background:#fffbeb;color:var(--orange);}
.kpi-ico.blue{background:#eff6ff;color:var(--blue);}

/* CHART CARDS */
.chart-row{display:grid;gap:20px;margin-bottom:20px;}
.chart-row.cols-2{grid-template-columns:1fr 1fr;}
.chart-row.cols-3{grid-template-columns:2fr 1fr;}
.chart-row.cols-1{grid-template-columns:1fr;}
.chart-card{background:white;border-radius:var(--radius);border:1.5px solid var(--border);box-shadow:0 2px 10px rgba(112,81,148,.05);overflow:hidden;}
.chart-head{display:flex;align-items:center;justify-content:space-between;padding:18px 22px 0;gap:10px;}
.chart-head-left{display:flex;align-items:center;gap:12px;}
.chart-ico{width:36px;height:36px;background:var(--grad);border-radius:9px;display:flex;align-items:center;justify-content:center;color:white;font-size:.9rem;flex-shrink:0;}
.chart-title{font-size:.95rem;font-weight:700;color:var(--text);}
.chart-sub{font-size:.76rem;color:var(--muted);}
.chart-body{padding:18px 22px 22px;}
.chart-canvas-wrap{position:relative;height:220px;}
.chart-canvas-wrap.tall{height:280px;}
.chart-canvas-wrap.short{height:160px;}

/* TABLES */
.rank-table{width:100%;border-collapse:collapse;}
.rank-table th{text-align:left;padding:9px 12px;font-size:.65rem;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);background:var(--bg);border-bottom:1.5px solid var(--border);}
.rank-table td{padding:10px 12px;border-bottom:1px solid var(--bg);font-size:.83rem;vertical-align:middle;}
.rank-table tr:last-child td{border-bottom:none;}
.rank-table tr:hover td{background:#faf9ff;}
.rank-num{width:26px;height:26px;border-radius:50%;background:var(--bg);font-weight:800;font-size:.75rem;color:var(--brand);display:inline-flex;align-items:center;justify-content:center;}
.rank-num.gold{background:#fef3c7;color:#b45309;}
.rank-num.silver{background:#f1f5f9;color:#475569;}
.rank-num.bronze{background:#fdf4ec;color:#c2410c;}
.badge-pill{display:inline-block;padding:2px 10px;border-radius:50px;font-size:.72rem;font-weight:700;}
.badge-green{background:#ecfdf5;color:#059669;}
.badge-orange{background:#fffbeb;color:#b45309;}
.badge-red{background:#fff1f2;color:#e11d48;}
.bar-mini{height:6px;border-radius:3px;background:var(--grad);margin-top:4px;}
.perf-star{color:#f59e0b;}

/* CATEGORY PILLS */
.cat-list{display:flex;flex-direction:column;gap:12px;padding:4px 0;}
.cat-item{}
.cat-item-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;}
.cat-item-name{font-size:.85rem;font-weight:700;color:var(--text);}
.cat-item-pct{font-size:.78rem;font-weight:700;color:var(--brand);}
.cat-bar-bg{height:8px;background:var(--bg);border-radius:4px;overflow:hidden;}
.cat-bar-fill{height:100%;border-radius:4px;background:var(--grad);}
.cat-stats{display:flex;gap:12px;margin-top:4px;}
.cat-stat{font-size:.72rem;color:var(--muted);}
.cat-stat span{font-weight:700;color:var(--text);}

/* TOGGLE TABS */
.tab-toggle{display:flex;gap:4px;background:var(--bg);border-radius:8px;padding:3px;}
.tab-btn{padding:5px 12px;border-radius:6px;border:none;background:transparent;font-size:.78rem;font-weight:700;color:var(--muted);cursor:pointer;transition:.15s;}
.tab-btn.active{background:white;color:var(--brand);box-shadow:0 1px 4px rgba(0,0,0,.08);}

@media(max-width:768px){
    .kpi-grid{grid-template-columns:repeat(2,1fr);}
    .chart-row.cols-2,.chart-row.cols-3{grid-template-columns:1fr;}
}
@media(max-width:480px){.kpi-grid{grid-template-columns:1fr 1fr;}.kpi-card .kpi-val{font-size:1.3rem;}}
</style>

<div class="pg">
    <div class="pg-header">
        <div>
            <h1><i class="fas fa-chart-line" style="color:var(--brand);margin-right:10px;"></i>Analytics</h1>
            <p>Sales trends, top performers, and business insights</p>
        </div>
    </div>

    <!-- KPI SUMMARY -->
    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-ico purple"><i class="fas fa-peso-sign"></i></div>
            <div class="kpi-label">Total Revenue</div>
            <div class="kpi-val">₱{{ "{:,.0f}".format(total_revenue) }}</div>
            <div class="kpi-sub">All-time gross sales</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-ico green"><i class="fas fa-arrow-trend-up"></i></div>
            <div class="kpi-label">Total Profit</div>
            <div class="kpi-val">₱{{ "{:,.0f}".format(total_profit) }}</div>
            <div class="kpi-sub">Revenue minus cost</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-ico orange"><i class="fas fa-box-open"></i></div>
            <div class="kpi-label">Units Sold</div>
            <div class="kpi-val">{{ "{:,}".format(total_units_sold) }}</div>
            <div class="kpi-sub">Total items moved</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-ico blue"><i class="fas fa-receipt"></i></div>
            <div class="kpi-label">Transactions</div>
            <div class="kpi-val">{{ "{:,}".format(total_transactions) }}</div>
            <div class="kpi-sub">Avg margin: {{ avg_margin }}%</div>
        </div>
    </div>

    <!-- SALES TREND (30 days) -->
    <div class="chart-row cols-1">
        <div class="chart-card">
            <div class="chart-head">
                <div class="chart-head-left">
                    <div class="chart-ico"><i class="fas fa-chart-area"></i></div>
                    <div>
                        <div class="chart-title">Sales Trend — Last 30 Days</div>
                        <div class="chart-sub">Daily revenue and units sold</div>
                    </div>
                </div>
                <div class="tab-toggle">
                    <button class="tab-btn active" onclick="switchTrend('revenue',this)">Revenue</button>
                    <button class="tab-btn" onclick="switchTrend('units',this)">Units</button>
                </div>
            </div>
            <div class="chart-body">
                <div class="chart-canvas-wrap tall"><canvas id="trendChart"></canvas></div>
            </div>
        </div>
    </div>

    <!-- MONTHLY + HOURLY -->
    <div class="chart-row cols-2">
        <div class="chart-card">
            <div class="chart-head">
                <div class="chart-head-left">
                    <div class="chart-ico"><i class="fas fa-calendar-days"></i></div>
                    <div>
                        <div class="chart-title">Monthly Revenue vs Profit</div>
                        <div class="chart-sub">Last 6 months comparison</div>
                    </div>
                </div>
            </div>
            <div class="chart-body">
                <div class="chart-canvas-wrap"><canvas id="monthlyChart"></canvas></div>
            </div>
        </div>
        <div class="chart-card">
            <div class="chart-head">
                <div class="chart-head-left">
                    <div class="chart-ico"><i class="fas fa-clock"></i></div>
                    <div>
                        <div class="chart-title">Peak Sales Hours</div>
                        <div class="chart-sub">Units sold by hour of day</div>
                    </div>
                </div>
            </div>
            <div class="chart-body">
                <div class="chart-canvas-wrap"><canvas id="hourlyChart"></canvas></div>
            </div>
        </div>
    </div>

    <!-- TOP ITEMS + CATEGORY -->
    <div class="chart-row cols-3">
        <div class="chart-card">
            <div class="chart-head">
                <div class="chart-head-left">
                    <div class="chart-ico"><i class="fas fa-trophy"></i></div>
                    <div>
                        <div class="chart-title">Top Items</div>
                        <div class="chart-sub">Best selling products</div>
                    </div>
                </div>
                <div class="tab-toggle">
                    <button class="tab-btn active" id="topRevBtn" onclick="showTopTab('revenue')">Revenue</button>
                    <button class="tab-btn" id="topUnitBtn" onclick="showTopTab('units')">Units</button>
                </div>
            </div>
            <div class="chart-body" style="padding-top:10px;">
                <!-- By Revenue -->
                <div id="topRevTable">
                <table class="rank-table">
                    <thead><tr><th>#</th><th>Product</th><th>Revenue</th><th>Units</th></tr></thead>
                    <tbody>
                    {% for item in top_by_revenue %}
                    <tr>
                        <td><span class="rank-num {{ 'gold' if loop.index0==0 else 'silver' if loop.index0==1 else 'bronze' if loop.index0==2 else '' }}">{{ loop.index }}</span></td>
                        <td>
                            <strong style="font-size:.83rem;">{{ item.name }}</strong>
                            {% if item.flavor %}<br><small style="color:var(--brand);">{{ item.flavor }}</small>{% endif %}
                        </td>
                        <td style="font-weight:800;color:var(--green);">₱{{ "{:,.0f}".format(item.revenue) }}</td>
                        <td style="color:var(--muted);">{{ item.units }}</td>
                    </tr>
                    {% endfor %}
                    {% if not top_by_revenue %}<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:24px;">No sales data yet</td></tr>{% endif %}
                    </tbody>
                </table>
                </div>
                <!-- By Units -->
                <div id="topUnitTable" style="display:none;">
                <table class="rank-table">
                    <thead><tr><th>#</th><th>Product</th><th>Units</th><th>Revenue</th></tr></thead>
                    <tbody>
                    {% for item in top_by_units %}
                    <tr>
                        <td><span class="rank-num {{ 'gold' if loop.index0==0 else 'silver' if loop.index0==1 else 'bronze' if loop.index0==2 else '' }}">{{ loop.index }}</span></td>
                        <td>
                            <strong style="font-size:.83rem;">{{ item.name }}</strong>
                            {% if item.flavor %}<br><small style="color:var(--brand);">{{ item.flavor }}</small>{% endif %}
                        </td>
                        <td style="font-weight:800;color:var(--brand);">{{ item.units }}</td>
                        <td style="color:var(--muted);">₱{{ "{:,.0f}".format(item.revenue) }}</td>
                    </tr>
                    {% endfor %}
                    {% if not top_by_units %}<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:24px;">No sales data yet</td></tr>{% endif %}
                    </tbody>
                </table>
                </div>
            </div>
        </div>

        <div class="chart-card">
            <div class="chart-head">
                <div class="chart-head-left">
                    <div class="chart-ico"><i class="fas fa-layer-group"></i></div>
                    <div>
                        <div class="chart-title">Category Performance</div>
                        <div class="chart-sub">Revenue share by type</div>
                    </div>
                </div>
            </div>
            <div class="chart-body" style="padding-top:12px;">
                <div class="chart-canvas-wrap short"><canvas id="catChart"></canvas></div>
                <div class="cat-list" style="margin-top:16px;">
                    {% for c in cat_perf %}
                    <div class="cat-item">
                        <div class="cat-item-head">
                            <div class="cat-item-name">{{ c.name }}</div>
                            <div class="cat-item-pct">{{ c.pct }}%</div>
                        </div>
                        <div class="cat-bar-bg"><div class="cat-bar-fill" style="width:{{ c.pct }}%;"></div></div>
                        <div class="cat-stats">
                            <div class="cat-stat">Revenue: <span>₱{{ "{:,.0f}".format(c.revenue) }}</span></div>
                            <div class="cat-stat">Units: <span>{{ c.units }}</span></div>
                            <div class="cat-stat">Profit: <span>₱{{ "{:,.0f}".format(c.profit) }}</span></div>
                        </div>
                    </div>
                    {% endfor %}
                    {% if not cat_perf %}<p style="color:var(--muted);font-size:.85rem;text-align:center;padding:16px 0;">No category data yet</p>{% endif %}
                </div>
            </div>
        </div>
    </div>

    <!-- HIGH PERFORMERS -->
    <div class="chart-row cols-1">
        <div class="chart-card">
            <div class="chart-head">
                <div class="chart-head-left">
                    <div class="chart-ico"><i class="fas fa-star"></i></div>
                    <div>
                        <div class="chart-title">High Performance Products</div>
                        <div class="chart-sub">Products with above-average profit margins (avg: {{ avg_margin }}%)</div>
                    </div>
                </div>
            </div>
            <div class="chart-body" style="padding-top:10px;">
                <table class="rank-table">
                    <thead><tr><th>#</th><th>Product</th><th>Units Sold</th><th>Revenue</th><th>Profit</th><th>Margin</th><th>Rating</th></tr></thead>
                    <tbody>
                    {% for p in high_performers %}
                    <tr>
                        <td><span class="rank-num {{ 'gold' if loop.index0==0 else 'silver' if loop.index0==1 else 'bronze' if loop.index0==2 else '' }}">{{ loop.index }}</span></td>
                        <td>
                            <strong style="font-size:.83rem;">{{ p.name }}</strong>
                            {% if p.flavor %}<br><small style="color:var(--brand);">{{ p.flavor }}</small>{% endif %}
                        </td>
                        <td>{{ p.units }}</td>
                        <td style="font-weight:700;color:var(--text);">₱{{ "{:,.0f}".format(p.revenue) }}</td>
                        <td style="font-weight:700;color:var(--green);">₱{{ "{:,.0f}".format(p.profit) }}</td>
                        <td>
                            <span class="badge-pill {{ 'badge-green' if p.margin >= 40 else 'badge-orange' if p.margin >= 20 else 'badge-red' }}">
                                {{ p.margin }}%
                            </span>
                        </td>
                        <td>
                            {% set stars = 5 if p.margin >= 50 else 4 if p.margin >= 35 else 3 if p.margin >= 20 else 2 %}
                            {% for _ in range(stars) %}<i class="fas fa-star perf-star" style="font-size:.75rem;"></i>{% endfor %}
                            {% for _ in range(5 - stars) %}<i class="far fa-star perf-star" style="font-size:.75rem;opacity:.3;"></i>{% endfor %}
                        </td>
                    </tr>
                    {% endfor %}
                    {% if not high_performers %}<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px;">No performance data yet. Record some sales first.</td></tr>{% endif %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script>
const trendLabels   = {{ trend_labels|tojson }};
const trendRevenue  = {{ trend_revenue|tojson }};
const trendUnits    = {{ trend_units|tojson }};
const monthlyLabels = {{ monthly_labels|tojson }};
const monthlyRev    = {{ monthly_revenue|tojson }};
const monthlyProfit = {{ monthly_profit|tojson }};
const hourlyLabels  = {{ hourly_labels|tojson }};
const hourlyValues  = {{ hourly_values|tojson }};
const catLabels     = {{ cat_perf|map(attribute='name')|list|tojson }};
const catRevenue    = {{ cat_perf|map(attribute='revenue')|list|tojson }};

Chart.defaults.font.family = "'Outfit','Inter',sans-serif";
Chart.defaults.color = '#64748b';

const COLORS = ['#705194','#9b6fc4','#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6','#06b6d4','#ec4899','#14b8a6'];

// --- TREND CHART ---
const trendCtx = document.getElementById('trendChart').getContext('2d');
const gradRev = trendCtx.createLinearGradient(0, 0, 0, 280);
gradRev.addColorStop(0, 'rgba(112,81,148,0.25)');
gradRev.addColorStop(1, 'rgba(112,81,148,0)');

let trendChart = new Chart(trendCtx, {
    type: 'line',
    data: {
        labels: trendLabels,
        datasets: [{
            label: 'Revenue (₱)',
            data: trendRevenue,
            borderColor: '#705194',
            backgroundColor: gradRev,
            borderWidth: 2.5,
            pointRadius: 0,
            pointHoverRadius: 5,
            fill: true,
            tension: 0.4
        }]
    },
    options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: {
            callbacks: { label: ctx => ' ₱' + ctx.raw.toLocaleString() }
        }},
        scales: {
            x: { grid: { display: false }, ticks: { maxTicksLimit: 10, font: { size: 11 } } },
            y: { grid: { color: '#f1f0f8' }, ticks: { callback: v => '₱' + v.toLocaleString() } }
        }
    }
});

function switchTrend(type, btn) {
    document.querySelectorAll('.period-tab, .tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const isRev = type === 'revenue';
    trendChart.data.datasets[0].data = isRev ? trendRevenue : trendUnits;
    trendChart.data.datasets[0].label = isRev ? 'Revenue (₱)' : 'Units Sold';
    trendChart.options.plugins.tooltip.callbacks.label = ctx =>
        isRev ? ' ₱' + ctx.raw.toLocaleString() : ' ' + ctx.raw + ' units';
    trendChart.options.scales.y.ticks.callback = v => isRev ? '₱' + v.toLocaleString() : v;
    trendChart.update();
}

// --- MONTHLY CHART ---
new Chart(document.getElementById('monthlyChart').getContext('2d'), {
    type: 'bar',
    data: {
        labels: monthlyLabels,
        datasets: [
            { label: 'Revenue', data: monthlyRev, backgroundColor: 'rgba(112,81,148,0.8)', borderRadius: 6 },
            { label: 'Profit',  data: monthlyProfit, backgroundColor: 'rgba(16,185,129,0.7)', borderRadius: 6 }
        ]
    },
    options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
            tooltip: { callbacks: { label: ctx => ' ₱' + ctx.raw.toLocaleString() } }
        },
        scales: {
            x: { grid: { display: false } },
            y: { grid: { color: '#f1f0f8' }, ticks: { callback: v => '₱' + v.toLocaleString() } }
        }
    }
});

// --- HOURLY CHART ---
new Chart(document.getElementById('hourlyChart').getContext('2d'), {
    type: 'bar',
    data: {
        labels: hourlyLabels,
        datasets: [{
            label: 'Units Sold',
            data: hourlyValues,
            backgroundColor: hourlyValues.map((v, i) => {
                const max = Math.max(...hourlyValues);
                return v === max ? 'rgba(245,158,11,0.85)' : 'rgba(112,81,148,0.5)';
            }),
            borderRadius: 4
        }]
    },
    options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
            x: { grid: { display: false }, ticks: { maxTicksLimit: 8, font: { size: 10 } } },
            y: { grid: { color: '#f1f0f8' }, ticks: { stepSize: 1 } }
        }
    }
});

// --- CATEGORY DONUT ---
new Chart(document.getElementById('catChart').getContext('2d'), {
    type: 'doughnut',
    data: {
        labels: catLabels,
        datasets: [{ data: catRevenue, backgroundColor: COLORS, borderWidth: 2, borderColor: 'white', hoverOffset: 6 }]
    },
    options: {
        responsive: true, maintainAspectRatio: false,
        cutout: '65%',
        plugins: {
            legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 11 }, padding: 10 } },
            tooltip: { callbacks: { label: ctx => ' ₱' + ctx.raw.toLocaleString() } }
        }
    }
});

// --- TOP ITEMS TAB TOGGLE ---
function showTopTab(tab) {
    document.getElementById('topRevTable').style.display = tab === 'revenue' ? '' : 'none';
    document.getElementById('topUnitTable').style.display = tab === 'units' ? '' : 'none';
    document.getElementById('topRevBtn').classList.toggle('active', tab === 'revenue');
    document.getElementById('topUnitBtn').classList.toggle('active', tab === 'units');
}
</script>
{% endblock %}

"""

TEMPLATES["settings.html"] = """
{% extends "base.html" %}
{% block content %}
<style>
    *, *::before, *::after { box-sizing: border-box; }
    :root {
        --brand:#705194; --brand-light:#f3eeff; --green:#10b981; --red:#ef4444;
        --orange:#f59e0b; --grad:linear-gradient(135deg,#705194,#9b6fc4);
        --surface:#ffffff; --bg:#f8f7ff; --border:#e8e4f0;
        --text:#1e293b; --muted:#64748b;
        --radius:16px; --radius-sm:10px;
        --shadow:0 2px 10px rgba(112,81,148,.06);
    }
    body { background: var(--bg); }
    .pg { max-width: 720px; margin: 0 auto; padding: 16px 16px 60px; }
    .pg-header { margin-bottom: 24px; }
    .pg-header h1 { font-size:1.7rem; font-weight:800; color:var(--text); margin:0; }
    .pg-header p { color:var(--muted); margin:4px 0 0; font-size:0.88rem; }

    .section-label {
        font-size: 0.68rem; font-weight: 800; text-transform: uppercase;
        letter-spacing: 1px; color: var(--muted); margin: 24px 0 10px;
    }

    .card { background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow); border: 1px solid var(--border); margin-bottom: 16px; overflow: hidden; }
    .card-head { padding: 14px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 10px; border-left: 4px solid var(--brand); }
    .card-head .ico { background: var(--grad); color: white; width: 32px; height: 32px; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 0.8rem; flex-shrink: 0; }
    .card-head strong { font-size: 0.92rem; color: var(--text); }
    .card-head small { font-size: 0.75rem; color: var(--muted); margin-left: auto; }
    .card-body { padding: 20px; }

    .field { display: flex; flex-direction: column; gap: 5px; margin-bottom: 14px; }
    .field:last-of-type { margin-bottom: 0; }
    .field label { font-size: 0.68rem; font-weight: 800; text-transform: uppercase; letter-spacing: .5px; color: var(--muted); }
    .field input { padding: 10px 12px; background: var(--bg); border: 1.5px solid var(--border); border-radius: var(--radius-sm); font-size: 0.9rem; color: var(--text); width: 100%; }
    .field input:focus { outline: none; border-color: var(--brand); background: white; }

    .btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; padding: 0 20px; height: 42px; border-radius: var(--radius-sm); font-weight: 700; font-size: 0.85rem; cursor: pointer; border: none; transition: .2s; }
    .btn-primary { background: var(--grad); color: white; }
    .btn-primary:hover { opacity: .88; }
    .btn-danger { background: #fee2e2; color: #b91c1c; }
    .btn-danger:hover { background: #fecaca; }

    .alert { padding: 12px 16px; border-radius: var(--radius-sm); font-size: 0.85rem; font-weight: 600; margin-bottom: 20px; display: flex; align-items: center; gap: 10px; }
    .alert-success { background: #f0fdf4; border: 1.5px solid #6ee7b7; color: #065f46; }
    .alert-danger  { background: #fef2f2; border: 1.5px solid #fca5a5; color: #991b1b; }

    .stat-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
    .stat-box { background: var(--bg); border-radius: var(--radius-sm); padding: 14px; text-align: center; border: 1px solid var(--border); }
    .stat-box .num { font-size: 1.5rem; font-weight: 900; color: var(--brand); }
    .stat-box .lbl { font-size: 0.68rem; color: var(--muted); text-transform: uppercase; font-weight: 700; letter-spacing: .5px; margin-top: 2px; }

    .danger-zone { border-color: #fca5a5 !important; }
    .danger-zone .card-head { border-left-color: var(--red); }
    .danger-zone .card-head .ico { background: linear-gradient(135deg,#ef4444,#dc2626); }

    .backup-zone .card-head { border-left-color: #3b82f6; }
    .backup-zone .card-head .ico { background: linear-gradient(135deg,#3b82f6,#6366f1); }

    .restore-drop {
        border: 2px dashed var(--border); border-radius: var(--radius-sm);
        padding: 22px; text-align: center; cursor: pointer;
        transition: .2s; background: var(--bg); position: relative;
    }
    .restore-drop:hover, .restore-drop.dragover { border-color: var(--brand); background: var(--brand-light); }
    .restore-drop input[type=file] { position:absolute; inset:0; opacity:0; cursor:pointer; width:100%; }
    .restore-drop .drop-icon { font-size: 1.6rem; color: var(--muted); margin-bottom: 6px; }
    .restore-drop .drop-label { font-size: 0.82rem; color: var(--muted); font-weight: 600; }
    .restore-drop .drop-name { font-size: 0.82rem; color: var(--brand); font-weight: 700; margin-top: 4px; display:none; }

    .btn-blue { background: linear-gradient(135deg,#3b82f6,#6366f1); color: white; }
    .btn-blue:hover { opacity: .88; }

    .mode-toggle { display:flex; gap:0; border: 1.5px solid var(--border); border-radius: var(--radius-sm); overflow:hidden; margin-bottom:14px; }
    .mode-toggle label { flex:1; text-align:center; padding:9px 6px; font-size:0.78rem; font-weight:700; cursor:pointer; color:var(--muted); transition:.15s; }
    .mode-toggle input[type=radio] { display:none; }
    .mode-toggle input:checked + label { background: var(--grad); color:white; }

    .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    @media (max-width: 480px) { .two-col { grid-template-columns: 1fr; } .stat-row { grid-template-columns: 1fr 1fr; } }
</style>

<div class="pg">
    <div class="pg-header">
        <h1><i class="fas fa-gear" style="color:var(--brand);margin-right:8px;"></i>Settings</h1>
        <p>Manage account credentials and system data.</p>
    </div>

    {% if msg %}
    <div class="alert alert-{{ msg_type }}">
        <i class="fas {{ 'fa-check-circle' if msg_type == 'success' else 'fa-triangle-exclamation' }}"></i>
        {{ msg }}
    </div>
    {% endif %}

    <!-- DB OVERVIEW -->
    <div class="section-label">System Overview</div>
    <div class="stat-row" style="margin-bottom:24px;">
        <div class="stat-box">
            <div class="num">{{ total_products }}</div>
            <div class="lbl">Products</div>
        </div>
        <div class="stat-box">
            <div class="num">{{ total_sales_logs }}</div>
            <div class="lbl">Sales Logs</div>
        </div>
        <div class="stat-box">
            <div class="num">{{ total_stockin_logs }}</div>
            <div class="lbl">Stock-In Logs</div>
        </div>
    </div>

    <!-- CHANGE PASSWORD -->
    <div class="section-label">Account Security</div>
    <div class="card">
        <div class="card-head">
            <div class="ico"><i class="fas fa-lock"></i></div>
            <strong>Change Password</strong>
            <small>Current user: <strong>{{ admin_user }}</strong></small>
        </div>
        <div class="card-body">
            <form method="POST">
                <input type="hidden" name="action" value="change_password">
                <div class="field">
                    <label>Current Password</label>
                    <input type="password" name="current_password" placeholder="Enter current password" required>
                </div>
                <div class="two-col">
                    <div class="field">
                        <label>New Password</label>
                        <input type="password" name="new_password" placeholder="New password" required>
                    </div>
                    <div class="field">
                        <label>Confirm New Password</label>
                        <input type="password" name="confirm_password" placeholder="Repeat new password" required>
                    </div>
                </div>
                <button type="submit" class="btn btn-primary" style="margin-top:6px;">
                    <i class="fas fa-key"></i> Update Password
                </button>
            </form>
        </div>
    </div>

    <!-- CHANGE USERNAME -->
    <div class="card">
        <div class="card-head">
            <div class="ico"><i class="fas fa-user-pen"></i></div>
            <strong>Change Username</strong>
        </div>
        <div class="card-body">
            <form method="POST">
                <input type="hidden" name="action" value="change_username">
                <div class="two-col">
                    <div class="field">
                        <label>New Username</label>
                        <input type="text" name="new_username" placeholder="Enter new username" required>
                    </div>
                    <div class="field">
                        <label>Confirm with Password</label>
                        <input type="password" name="password_for_user" placeholder="Current password" required>
                    </div>
                </div>
                <button type="submit" class="btn btn-primary" style="margin-top:6px;">
                    <i class="fas fa-user-check"></i> Update Username
                </button>
            </form>
        </div>
    </div>

    <!-- BACKUP & RESTORE -->
    <div class="section-label">Backup &amp; Restore</div>
    <div class="card backup-zone">
        <div class="card-head">
            <div class="ico"><i class="fas fa-database"></i></div>
            <strong>Backup &amp; Restore</strong>
            <small>All products &amp; logs</small>
        </div>
        <div class="card-body">

            <!-- BACKUP -->
            <div style="margin-bottom:20px;">
                <div style="font-size:0.78rem;color:var(--muted);margin-bottom:12px;line-height:1.5;">
                    Download a complete snapshot of your database — all products, sales logs, and stock-in logs — as a <strong>.json</strong> file.
                </div>
                <a href="/settings/backup" class="btn btn-blue" style="text-decoration:none;">
                    <i class="fas fa-download"></i> Download Backup
                </a>
                <span style="font-size:0.72rem;color:var(--muted);margin-left:12px;">
                    {{ total_products }} products · {{ total_sales_logs }} sales · {{ total_stockin_logs }} stock-in records
                </span>
            </div>

            <div style="height:1px;background:var(--border);margin-bottom:20px;"></div>

            <!-- RESTORE -->
            <div style="font-size:0.78rem;color:var(--muted);margin-bottom:12px;line-height:1.5;">
                Upload a <strong>.json</strong> backup file to restore data. Choose a restore mode:
            </div>

            <form method="POST" action="/settings/restore" enctype="multipart/form-data"
                  onsubmit="return confirmRestore(this);">

                <!-- Mode Toggle -->
                <div style="font-size:0.68rem;font-weight:800;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);margin-bottom:6px;">Restore Mode</div>
                <div class="mode-toggle" style="margin-bottom:14px;">
                    <input type="radio" name="restore_mode" id="modeMerge" value="merge" checked>
                    <label for="modeMerge"><i class="fas fa-code-merge"></i> Merge <span style="font-weight:400;font-size:0.7rem;display:block;">Add new records only</span></label>
                    <input type="radio" name="restore_mode" id="modeOverwrite" value="overwrite">
                    <label for="modeOverwrite"><i class="fas fa-rotate"></i> Overwrite <span style="font-weight:400;font-size:0.7rem;display:block;">Replace all existing data</span></label>
                </div>

                <!-- File Drop Zone -->
                <div class="restore-drop" id="dropZone" style="margin-bottom:14px;">
                    <input type="file" name="restore_file" accept=".json" id="restoreFile"
                           onchange="updateDropLabel(this)">
                    <div class="drop-icon"><i class="fas fa-file-arrow-up"></i></div>
                    <div class="drop-label">Click or drag a backup .json file here</div>
                    <div class="drop-name" id="dropName"></div>
                </div>

                <!-- Password -->
                <div class="field" style="margin-bottom:14px;">
                    <label>Password to confirm restore</label>
                    <input type="password" name="restore_password" placeholder="Enter your password" required>
                </div>

                <button type="submit" class="btn btn-blue">
                    <i class="fas fa-upload"></i> Restore from Backup
                </button>
            </form>
        </div>
    </div>

    <!-- DANGER ZONE -->
    <div class="section-label" style="color:#b91c1c;">Danger Zone</div>
    <div class="card danger-zone">
        <div class="card-head">
            <div class="ico"><i class="fas fa-triangle-exclamation"></i></div>
            <strong>Clear Logs</strong>
            <small style="color:#b91c1c;">Irreversible actions</small>
        </div>
        <div class="card-body">
            <p style="font-size:0.82rem;color:var(--muted);margin-bottom:16px;">
                These actions permanently delete log records. Product inventory is not affected.
            </p>

            <form method="POST" style="margin-bottom:16px;" onsubmit="return confirm('Clear ALL sales logs? This cannot be undone.');">
                <input type="hidden" name="action" value="clear_sales_log">
                <div style="display:flex;gap:10px;align-items:flex-end;">
                    <div class="field" style="flex:1;margin:0;">
                        <label>Password to confirm</label>
                        <input type="password" name="danger_password" placeholder="Enter password" required>
                    </div>
                    <button type="submit" class="btn btn-danger">
                        <i class="fas fa-trash"></i> Clear Sales Log ({{ total_sales_logs }} entries)
                    </button>
                </div>
            </form>

            <form method="POST" onsubmit="return confirm('Clear ALL stock-in logs? This cannot be undone.');">
                <input type="hidden" name="action" value="clear_stock_in_log">
                <div style="display:flex;gap:10px;align-items:flex-end;">
                    <div class="field" style="flex:1;margin:0;">
                        <label>Password to confirm</label>
                        <input type="password" name="danger_password2" placeholder="Enter password" required>
                    </div>
                    <button type="submit" class="btn btn-danger">
                        <i class="fas fa-trash"></i> Clear Stock-In Log ({{ total_stockin_logs }} entries)
                    </button>
                </div>
            </form>
        </div>
    </div>
</div>

<script>
function updateDropLabel(input) {
    const name = input.files[0]?.name || '';
    const nameEl = document.getElementById('dropName');
    const labelEl = document.querySelector('.drop-label');
    if (name) {
        nameEl.textContent = '📄 ' + name;
        nameEl.style.display = 'block';
        labelEl.style.display = 'none';
        document.querySelector('.drop-icon').style.display = 'none';
    }
}

function confirmRestore(form) {
    const mode = form.restore_mode.value;
    const file = document.getElementById('restoreFile').files[0];
    if (!file) { alert('Please select a backup file first.'); return false; }
    if (mode === 'overwrite') {
        return confirm('⚠️ OVERWRITE MODE: This will DELETE all current products and logs before restoring. Are you absolutely sure?');
    }
    return confirm('Restore (merge) from backup? New records will be added without removing existing data.');
}

const dz = document.getElementById('dropZone');
['dragover','dragenter'].forEach(e => dz.addEventListener(e, ev => { ev.preventDefault(); dz.classList.add('dragover'); }));
['dragleave','drop'].forEach(e => dz.addEventListener(e, () => dz.classList.remove('dragover')));
</script>
{% endblock %}
"""

# --- 7. ASSIGN DICTLOADER TO JINJA WORKFLOW ---
app.jinja_loader = DictLoader(TEMPLATES)

# --- 8. DATABASE AUTO INITIALIZE ---
# Must run at module level (not just __main__) for Vercel serverless cold starts
try:
    migrate = Migrate(app, db)
except Exception:
    pass  # Flask-Migrate optional; skip if it causes issues in serverless

with app.app_context():
    db.create_all()
    # Safe migration: add 'discount' column to existing product table if missing
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("ALTER TABLE product ADD COLUMN IF NOT EXISTS discount FLOAT DEFAULT 0.0"))
            conn.commit()
    except Exception:
        pass  # Column already exists or DB doesn't support IF NOT EXISTS — safe to ignore

# --- 9. LOCAL DEV SERVER ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=os.environ.get('FLASK_DEBUG', 'false').lower() == 'true')