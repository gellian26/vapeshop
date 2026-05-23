from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, extract, desc
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from collections import defaultdict
import os
import uuid
import time

# --- 1. INITIALIZE APP & DATABASE ---
app = Flask(__name__)
app.secret_key = "flex_vape_final_unified_key"

# --- SQLITE CONFIGURATION ---
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'flex_vape.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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
    price = db.Column(db.Float)
    cost = db.Column(db.Float)

# --- 3. LOGIN PROTECTION ---
ADMIN_USER = "flexinventory"
ADMIN_PASS = "flexsystem"

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
        "flavor": p.flavor,
        "type": p.type,
        "qty": p.qty,
        "price": p.price,
        "image": p.image
    } for p in products}

# --- 5. ROUTES ---

@app.route('/')
def dashboard():
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    
    # Dynamic Day and Month names
    day_name = now.strftime('%A')
    month_name = now.strftime('%B')

    # Sales Today (Auto-resets at midnight)
    sales_today_count = StockOutLog.query.filter(func.date(StockOutLog.date) == today_str).count()
    
    # Monthly Revenue (Auto-resets on the 1st)
    rev_month = db.session.query(func.sum(StockOutLog.qty * StockOutLog.price)).filter(
        extract('month', StockOutLog.date) == now.month,
        extract('year', StockOutLog.date) == now.year
    ).scalar() or 0

    products_all = Product.query.all()
    total_qty = sum(p.qty for p in products_all)
    low_stock_count = Product.query.filter(Product.qty < 5).count()

    # Chart Trends
    months_labels, sales_trend, purchase_trend = [], [], []
    for i in range(5, -1, -1):
        target_date = now - timedelta(days=i*30)
        months_labels.append(target_date.strftime("%b"))
        s_val = db.session.query(func.sum(StockOutLog.qty)).filter(extract('month', StockOutLog.date) == target_date.month).scalar() or 0
        p_val = db.session.query(func.sum(StockInLog.qty)).filter(extract('month', StockInLog.date) == target_date.month).scalar() or 0
        sales_trend.append(int(s_val))
        purchase_trend.append(int(p_val))

    top_selling = db.session.query(StockOutLog.name, func.sum(StockOutLog.qty).label('total')).group_by(StockOutLog.name).order_by(desc('total')).limit(5).all()

    total_sales_all = db.session.query(func.sum(StockOutLog.qty)).scalar() or 1
    cat_sales = db.session.query(StockOutLog.category, func.sum(StockOutLog.qty)).group_by(StockOutLog.category).all()
    category_progress = [{'name': c[0].capitalize() if c[0] else "Other", 'percentage': round((c[1]/total_sales_all)*100)} for c in cat_sales]

    stats = {
        'total_qty': total_qty, 
        'low_stock': low_stock_count,
        'revenue_month': f"₱{rev_month:,.2f}", 
        'sales_today_count': sales_today_count,
        'day_name': day_name,
        'month_name': month_name,
        'bar_labels': months_labels, 
        'bar_sales': sales_trend, 
        'bar_purchases': purchase_trend,
        'pie_labels': [item[0] for item in top_selling], 
        'pie_values': [int(item[1]) for item in top_selling],
        'stock_alerts': Product.query.filter(Product.qty < 10).order_by(Product.qty.asc()).limit(5).all(),
        'cat_progress': category_progress
    }
    return render_template('dashboard.html', stats=stats)

@app.route('/history')
def history():
    # 1. Daily Revenue
    daily_history = db.session.query(
        func.date(StockOutLog.date).label('day'),
        func.count(StockOutLog.id).label('count'),
        func.sum(StockOutLog.qty * StockOutLog.price).label('revenue')
    ).group_by(func.date(StockOutLog.date)).order_by(desc('day')).limit(60).all()

    # 2. Monthly Revenue
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
    categories = ['pods', 'juice', 'disposable', 'device', 'cartridge']
    return render_template('inventory.html', products=get_products_dict(), categories=categories)

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
            p = Product.query.get(p_id)
            if p: db.session.delete(p); db.session.commit()
            return redirect(url_for('products'))

        name = request.form.get('name')
        price = float(request.form.get('price') or 0)
        barcode = request.form.get('barcode', '').strip() or str(int(time.time()))

        if p_id:
            p = Product.query.get(p_id)
            p.name, p.price, p.barcode = name, price, barcode
            p.flavor, p.type, p.version, p.mg = request.form.get('flavor'), request.form.get('type'), request.form.get('version'), request.form.get('mg')
            p.cost = float(request.form.get('cost') or 0)
        else:
            new_p = Product(name=name, price=price, barcode=barcode, qty=int(request.form.get('quantity') or 0), type=request.form.get('type'), flavor=request.form.get('flavor'), cost=float(request.form.get('cost') or 0), version=request.form.get('version'), mg=request.form.get('mg'))
            db.session.add(new_p)
        db.session.commit()
        return redirect(url_for('products'))
    return render_template('products.html', products=get_products_dict())

@app.route('/sales', methods=['GET', 'POST'])
def sales():
    if request.method == 'POST':
        p_id = request.form.get('product_key')
        qty = int(request.form.get('quantity') or 0)
        p = Product.query.get(p_id)
        if p and p.qty >= qty:
            p.qty -= qty
            db.session.add(StockOutLog(name=p.name, flavor=p.flavor, category=p.type, qty=qty, price=p.price, cost=p.cost))
            db.session.commit()
            flash("Sale recorded!", "success")
        else:
            flash("Insufficient stock!", "danger")
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

    return render_template('reports.html', movement=movement, revenue=sum(l.qty*l.price for l in logs_out), sales_count=len(logs_out), date=today.strftime("%B %d, %Y"), now=datetime.now().strftime("%H:%M"), period=period, report_label="Inventory Audit")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USER and password == ADMIN_PASS:
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        else:
            flash("Incorrect password or username", "danger")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    with app.app_context(): db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)