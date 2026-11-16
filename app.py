"""SYNBYTE — multi-page Flask campus food ordering application.
Payment: Razorpay TEST integration, or explicitly labelled LOCAL DEMO SIMULATOR.
This repository is a local educational prototype, not approved for live payments.
"""
import os, re, json, csv, io, hmac, hashlib, secrets, sqlite3, base64
from datetime import datetime, timezone, timedelta
from pathlib import Path
from functools import wraps
from contextlib import contextmanager
from urllib.parse import urlparse

import requests
from flask import (Flask, render_template, request, redirect, url_for, flash,
                   session, g, abort, jsonify, send_from_directory)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE = Path(__file__).resolve().parent
INSTANCE = BASE / 'instance'
INSTANCE.mkdir(exist_ok=True)
SECRET_PATH = INSTANCE / '.secret'
if 'SECRET_KEY' in os.environ:
    secret = os.environ['SECRET_KEY']
else:
    if not SECRET_PATH.exists():
        SECRET_PATH.write_text(secrets.token_hex(32), encoding='utf-8')
    secret = SECRET_PATH.read_text(encoding='utf-8').strip()
app = Flask(__name__)
app.config.update(SECRET_KEY=secret, MAX_CONTENT_LENGTH=5*1024*1024,
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE','0')=='1')
DB_PATH = Path(os.environ.get('SYNBYTE_DB', str(INSTANCE / 'synbyte.sqlite3')))
PAYMENT_PROVIDER = os.environ.get('PAYMENT_PROVIDER', 'demo').lower()
RZ_KEY = os.environ.get('RAZORPAY_KEY_ID', '')
RZ_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', '')
RZ_WEBHOOK = os.environ.get('RAZORPAY_WEBHOOK_SECRET', '')
if PAYMENT_PROVIDER not in {'demo', 'razorpay_test'}:
    raise RuntimeError('PAYMENT_PROVIDER must be demo or razorpay_test')
if PAYMENT_PROVIDER == 'razorpay_test' and not (RZ_KEY.startswith('rzp_test_') and RZ_SECRET):
    raise RuntimeError('Razorpay TEST keys required; live credentials are not accepted in this prototype')

CATALOG = [
 ('Pizza Point','Margherita Pizza',149,'Pizza','Traditional cheese, tomato and basil'),
 ('Pizza Point','Farmhouse Pizza',199,'Pizza','Mixed vegetables on a cheesy crust'),
 ('Pizza Point','Cheese Burst Pizza',249,'Pizza','Rich cheese-filled crust'),
 ('Pizza Point','Paneer Tikka Pizza',229,'Pizza','Paneer tikka, onion and peppers'),
 ('Pizza Point','Corn & Cheese Pizza',179,'Pizza','Sweet corn and golden cheese'),
 ('Burger Hub','Classic Veg Burger',89,'Burgers','Vegetable patty and crisp lettuce'),
 ('Burger Hub','Double Cheese Burger',139,'Burgers','Double cheesy goodness'),
 ('Burger Hub','Crispy Paneer Burger',129,'Burgers','Spiced crispy paneer fillet'),
 ('Burger Hub','Aloo Tikki Burger',79,'Burgers','Classic seasoned potato patty'),
 ('Burger Hub','Spicy Mexican Burger',159,'Burgers','Spicy salsa and creamy sauce'),
 ('Momo Station','Steamed Veg Momos',79,'Momos','Soft steamed vegetable dumplings'),
 ('Momo Station','Fried Momos',99,'Momos','Crispy fried dumplings'),
 ('Momo Station','Tandoori Momos',119,'Momos','Smoky tandoori-spiced momos'),
 ('Momo Station','Paneer Momos',109,'Momos','Paneer-filled steamed dumplings'),
 ('Wok Republic','Hakka Noodles',119,'Chinese','Wok-tossed vegetables and noodles'),
 ('Wok Republic','Schezwan Noodles',129,'Chinese','Noodles tossed in spicy sauce'),
 ('Wok Republic','Veg Fried Rice',109,'Chinese','Vegetables and fragrant fried rice'),
 ('Wok Republic','Chilli Paneer',169,'Chinese','Paneer in tangy chilli sauce'),
 ('Pasta & More','White Sauce Pasta',159,'Pasta','Pasta in creamy white sauce'),
 ('Pasta & More','Red Sauce Pasta',149,'Pasta','Pasta in tomato-herb sauce'),
 ('Pasta & More','Pink Sauce Pasta',169,'Pasta','Creamy tomato-pink sauce pasta'),
 ('Sandwich Corner','Grilled Veg Sandwich',99,'Sandwiches & Snacks','Grilled multigrain veggie sandwich'),
 ('Sandwich Corner','Cheese Corn Sandwich',119,'Sandwiches & Snacks','Toasted cheese and sweetcorn'),
 ('Sandwich Corner','Loaded French Fries',99,'Sandwiches & Snacks','Fries with cheese and toppings'),
 ('Sandwich Corner','Peri Peri Fries',109,'Sandwiches & Snacks','Spicy peri-peri potato fries'),
 ('Campus Cafe','Cold Coffee',79,'Drinks','Chilled creamy coffee'),
 ('Campus Cafe','Chocolate Shake',119,'Drinks','Rich chocolate milkshake'),
 ('Campus Cafe','Masala Chai',29,'Drinks','Indian spiced hot tea'),
 ('Campus Cafe','Fresh Lime Soda',59,'Drinks','Sparkling fresh lime drink'),
 ('Sweet Lab','Chocolate Brownie',109,'Desserts','Dense fudgy chocolate brownie'),
 ('Sweet Lab','Chocolate Lava Cake',129,'Desserts','Warm molten chocolate centre'),
 ('Sweet Lab','Vanilla Ice Cream Sundae',99,'Desserts','Vanilla scoops with toppings'),
]
PHOTO_URLS = [x['url'] for x in json.loads((BASE / 'PHOTO_MANIFEST.json').read_text(encoding='utf-8'))]
CATEGORIES = ['Pizza','Burgers','Momos','Chinese','Pasta','Sandwiches & Snacks','Drinks','Desserts']

@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=20000')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS vendors(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,contact TEXT DEFAULT '',approved INTEGER NOT NULL DEFAULT 1, starter_seeded INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,email TEXT NOT NULL UNIQUE,password TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('student','vendor','admin')),vendor_id INTEGER REFERENCES vendors(id),approved INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS foods(id INTEGER PRIMARY KEY,vendor_id INTEGER NOT NULL REFERENCES vendors(id),name TEXT NOT NULL,category TEXT NOT NULL,price INTEGER NOT NULL CHECK(price>0),description TEXT NOT NULL,image TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS photo_credits(food_id INTEGER PRIMARY KEY REFERENCES foods(id),source TEXT NOT NULL,license TEXT NOT NULL,photographer TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS carts(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),food_id INTEGER NOT NULL REFERENCES foods(id),quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 20),UNIQUE(user_id,food_id));
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),vendor_id INTEGER NOT NULL REFERENCES vendors(id),total INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'Awaiting Payment',payment_state TEXT NOT NULL DEFAULT 'Pending',provider_order_id TEXT UNIQUE,provider_payment_id TEXT UNIQUE,token TEXT UNIQUE,created TEXT NOT NULL,paid_at TEXT);
        CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY,order_id INTEGER NOT NULL REFERENCES orders(id),food_id INTEGER NOT NULL REFERENCES foods(id),name TEXT NOT NULL,quantity INTEGER NOT NULL,unit_price INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY,order_id INTEGER NOT NULL REFERENCES orders(id),provider TEXT NOT NULL,reference TEXT UNIQUE,event TEXT NOT NULL,verified INTEGER NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payment_events(id INTEGER PRIMARY KEY,event_id TEXT NOT NULL UNIQUE,event_type TEXT NOT NULL,payload_hash TEXT NOT NULL,received TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS status_history(id INTEGER PRIMARY KEY,order_id INTEGER NOT NULL REFERENCES orders(id),status TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),title TEXT NOT NULL,body TEXT NOT NULL,href TEXT NOT NULL,read_at TEXT,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER NOT NULL REFERENCES users(id),food_id INTEGER NOT NULL REFERENCES foods(id),PRIMARY KEY(user_id,food_id));
        CREATE TABLE IF NOT EXISTS password_resets(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),expires TEXT NOT NULL);
        ''')
        # Upgrade old local database schema without disturbing saved accounts and orders.
        cols = {row['name'] for row in c.execute('PRAGMA table_info(vendors)')}
        if 'starter_seeded' not in cols:
            c.execute('ALTER TABLE vendors ADD COLUMN starter_seeded INTEGER NOT NULL DEFAULT 0')
        for cat in CATEGORIES:
            c.execute('INSERT OR IGNORE INTO categories(name) VALUES(?)',(cat,))
        vendors=sorted(set(x[0] for x in CATALOG))
        for v in vendors:
            c.execute('INSERT OR IGNORE INTO vendors(name,approved) VALUES(?,1)',(v,))
        ids={row['name']:row['id'] for row in c.execute('SELECT id,name FROM vendors')}
        for i,(v,name,price,cat,desc) in enumerate(CATALOG,1):
            c.execute('INSERT OR IGNORE INTO foods(id,vendor_id,name,category,price,description,image) VALUES(?,?,?,?,?,?,?)',
                      (i,ids[v],name,cat,price,desc,PHOTO_URLS[i-1]))
            c.execute('INSERT OR IGNORE INTO photo_credits(food_id,source,license,photographer) VALUES(?,?,?,?)',
                      (i,'Unsplash CDN: '+PHOTO_URLS[i-1],'Unsplash License; source and exact dish must be manually reviewed','Unsplash photographer - refer to original listing'))
        if os.environ.get('SEED_DEMO','1') == '1':
            demo=[('Demo Student','student@synbyte.test','student',None),
                  ('Demo Admin','admin@synbyte.test','admin',None)]
            for v in vendors:
                email=slug(v).replace('-','')+'@synbyte.test'
                demo.append((v+' Manager',email,'vendor',ids[v]))
            for name,email,role,vid in demo:
                c.execute('INSERT OR IGNORE INTO users(name,email,password,role,vendor_id,created) VALUES(?,?,?,?,?,?)',
                          (name,email,generate_password_hash('SynbyteDemo!2026'),role,vid,stamp()))
        # Give each existing or newly imported vendor all 32 starter dishes once.
        for row in c.execute('SELECT id FROM vendors').fetchall():
            seed_vendor_catalog(c,row['id'])
        # Migrate old artwork paths to their distinct photo sources, preserving vendor uploads.
        for i,(_v,name,_price,_cat,_desc) in enumerate(CATALOG):
            c.execute('UPDATE foods SET image=? WHERE name=? AND image LIKE ?',
                      (PHOTO_URLS[i],name,'%.svg'))
            c.execute('UPDATE photo_credits SET source=?,license=? WHERE food_id IN (SELECT id FROM foods WHERE name=? AND image=?)', (PHOTO_URLS[i], 'See PHOTO_MANIFEST.json for source and license',name, PHOTO_URLS[i]))


def seed_vendor_catalog(c, vendor_id):
    '''Copy the full starter menu for one vendor exactly once; no re-seeding removed dishes.'''
    vendor=c.execute('SELECT starter_seeded FROM vendors WHERE id=?',(vendor_id,)).fetchone()
    if not vendor or vendor['starter_seeded']:return
    existing={r['name'].lower() for r in c.execute('SELECT name FROM foods WHERE vendor_id=?',(vendor_id,))}
    for i,(_v,name,price,cat,desc) in enumerate(CATALOG):
        if name.lower() in existing:continue
        cur=c.execute('INSERT INTO foods(vendor_id,name,category,price,description,image) VALUES(?,?,?,?,?,?)',
                      (vendor_id,name,cat,price,desc,PHOTO_URLS[i]))
        c.execute('INSERT OR IGNORE INTO photo_credits(food_id,source,license,photographer) VALUES(?,?,?,?)',
                  (cur.lastrowid,'Unsplash CDN: '+PHOTO_URLS[i],'Unsplash License; review photo before public release','Unsplash photographer (see original source)'))
    c.execute('UPDATE vendors SET starter_seeded=1 WHERE id=?',(vendor_id,))

def slug(name):
    return re.sub(r'[^a-z0-9]+','-',name.lower()).strip('-')

@app.before_request
def prepare():
    if '_csrf' not in session:
        session['_csrf'] = secrets.token_urlsafe(24)
    g.user = None
    if session.get('uid'):
        with db() as c:
            g.user=c.execute('SELECT * FROM users WHERE id=?',(session['uid'],)).fetchone()
    if request.method in {'POST','PUT','PATCH','DELETE'} and request.endpoint != 'razorpay_webhook':
        sent=request.form.get('_csrf') or request.headers.get('X-CSRF-Token')
        if not sent or not hmac.compare_digest(sent,session['_csrf']):
            abort(400,description='Security token missing or expired. Refresh the page and try again.')

@app.context_processor
def inject():
    return dict(user=g.get('user'),csrf=session.get('_csrf',''), categories=active_categories(), payment_provider=PAYMENT_PROVIDER,
                money=lambda p:f'₹{int(p):,}',current_year=datetime.now().year, image_url=lambda src:src if str(src).startswith(('https://','http://')) else url_for('static',filename='images/'+src))

def require(role=None):
    def deco(fn):
        @wraps(fn)
        def inner(*args,**kwargs):
            if g.user is None:
                flash('Please sign in to continue.','warning')
                return redirect(url_for('role_selection'))
            if role and g.user['role']!=role:
                abort(403)
            if g.user['role']=='vendor' and not g.user['approved']:
                abort(403,description='Vendor account is awaiting approval')
            return fn(*args,**kwargs)
        return inner
    return deco

def active_categories():
    with db() as c:
        return [r['name'] for r in c.execute('SELECT name FROM categories ORDER BY id')]

def posted_category(c):
    category=request.form.get('category','').strip()[:50]
    if category=='Other':
        category=request.form.get('custom_category','').strip()[:50]
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9 &/-]{1,49}',category):abort(400,description='Invalid category name')
    c.execute('INSERT OR IGNORE INTO categories(name) VALUES(?)',(category,))
    return category

def student_order(oid):
    with db() as c:
        o=c.execute('SELECT o.*,v.name vendor_name FROM orders o JOIN vendors v ON v.id=o.vendor_id WHERE o.id=? AND o.user_id=?',(oid,g.user['id'])).fetchone()
    if not o:abort(404)
    return o

def vendor_order(oid):
    with db() as c:
        o=c.execute('SELECT o.*,u.name consumer_name,u.email consumer_email FROM orders o JOIN users u ON u.id=o.user_id WHERE o.id=? AND o.vendor_id=? AND o.payment_state="Paid"',(oid,g.user['vendor_id'])).fetchone()
    if not o:abort(404)
    return o

def items_for(oid):
    with db() as c:return c.execute('SELECT * FROM order_items WHERE order_id=?',(oid,)).fetchall()

def cart_detail(uid):
    with db() as c:
        rows=c.execute('SELECT c.quantity,f.*,v.name vendor_name FROM carts c JOIN foods f ON f.id=c.food_id JOIN vendors v ON v.id=f.vendor_id WHERE c.user_id=? ORDER BY c.id',(uid,)).fetchall()
    return rows,sum(x['price']*x['quantity'] for x in rows)

def notify(c,uid,title,body,href):
    c.execute('INSERT INTO notifications(user_id,title,body,href,created) VALUES(?,?,?,?,?)',(uid,title,body,href,stamp()))

def fulfill_paid(c,order_id,payment_reference,provider):
    order=c.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
    if not order:raise ValueError('Unknown order')
    if order['payment_state']=='Paid':return False
    token='CX-'+secrets.token_urlsafe(13).replace('-','').replace('_','').upper()
    c.execute('UPDATE orders SET payment_state="Paid",status="Placed",token=?,provider_payment_id=?,paid_at=? WHERE id=? AND payment_state!="Paid"',
              (token,payment_reference,stamp(),order_id))
    c.execute('INSERT INTO payments(order_id,provider,reference,event,verified,created) VALUES(?,?,?,?,1,?)',
              (order_id,provider,payment_reference,'captured' if provider=='razorpay_test' else 'simulated',stamp()))
    c.execute('INSERT INTO status_history(order_id,status,created) VALUES(?,?,?)',(order_id,'Placed',stamp()))
    notify(c,order['user_id'],'Payment confirmed','Order #'+str(order_id)+' is now with the vendor.',f'/order/tracking/{order_id}')
    for vend in c.execute('SELECT id FROM users WHERE vendor_id=? AND role="vendor" AND approved=1',(order['vendor_id'],)).fetchall():
        notify(c,vend['id'],'New paid order',f'Order #{order_id} is ready for acceptance.',f'/vendor/orders/{order_id}')
    return True

@app.get('/')
def home():
    # Public homepage is strictly promotional. No menu, orders or cart data is loaded.
    if g.user is not None:
        landing = {'student':'student_dashboard','vendor':'vendor_dashboard','admin':'admin_dashboard'}
        return redirect(url_for(landing[g.user['role']]))
    return render_template('index.html',title='Crave It. Click It. Collect It.')

@app.get('/choose')
def role_selection():return render_template('role-selection.html',title='Choose your experience')

@app.get('/student/login')
def student_login():return render_template('student-login.html',title='Student login')

@app.get('/student/register')
def student_register():return render_template('student-register.html',title='Student registration')

@app.post('/student/register')
def student_register_post():
    name=request.form.get('name','').strip()[:80];email=request.form.get('email','').strip().lower()[:120]
    pwd=request.form.get('password',''); confirm=request.form.get('confirm','')
    if len(name)<2 or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email) or len(pwd)<10 or pwd!=confirm:
        flash('Please enter a valid name, email, and matching password (10+ characters).','error');return redirect(url_for('student_register'))
    try:
        with db() as c:c.execute('INSERT INTO users(name,email,password,role,created) VALUES(?,?,?,?,?)',
                                (name,email,generate_password_hash(pwd),'student',stamp()))
    except sqlite3.IntegrityError:
        flash('This email is already registered.','error');return redirect(url_for('student_register'))
    flash('Account created! You can sign in now.','success');return redirect(url_for('student_login'))

login_attempts={}
@app.post('/auth/login/<kind>')
def do_login(kind):
    if kind not in {'student','vendor','admin'}:abort(404)
    ip=request.remote_addr or 'local';now=datetime.now(timezone.utc).timestamp();key=ip+':'+kind
    attempts=[x for x in login_attempts.get(key,[]) if now-x<900]
    if len(attempts)>=12:abort(429,description='Too many login attempts. Try again later.')
    email=request.form.get('email','').strip().lower();password=request.form.get('password','')
    with db() as c:
        user=c.execute('SELECT * FROM users WHERE email=? AND role=?',(email,kind)).fetchone()
    if not user or not check_password_hash(user['password'],password):
        attempts.append(now);login_attempts[key]=attempts;flash('Invalid email or password.','error')
        return redirect(url_for({'student':'student_login','vendor':'vendor_login','admin':'admin_login'}[kind]))
    if not user['approved']:
        flash('Your account is awaiting approval.','warning');return redirect(url_for('vendor_login'))
    login_attempts.pop(key,None)
    session.clear();session.update(uid=user['id'],_csrf=secrets.token_urlsafe(24))
    return redirect(url_for({'student':'student_dashboard','vendor':'vendor_dashboard','admin':'admin_dashboard'}[kind]))

@app.post('/logout')
def logout():session.clear();flash('Signed out.','success');return redirect(url_for('home'))
@app.get('/vendor/login')
def vendor_login():return render_template('vendor-login.html',title='Vendor login')
@app.get('/vendor/register')
def vendor_register():return render_template('vendor-register.html',title='Become a vendor')
@app.post('/vendor/register')
def vendor_register_post():
    name=request.form.get('vendor_name','').strip()[:80];rep=request.form.get('name','').strip()[:80]
    email=request.form.get('email','').strip().lower()[:120];contact=request.form.get('contact','').strip()[:100]
    pwd=request.form.get('password','');confirm=request.form.get('confirm','')
    if min(len(name),len(rep))<2 or len(contact)<5 or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email) or len(pwd)<10 or pwd!=confirm:
        flash('Complete all fields and use a matching 10+ character password.','error');return redirect(url_for('vendor_register'))
    try:
        with db() as c:
            cur=c.execute('INSERT INTO vendors(name,contact,approved) VALUES(?,?,1)',(name,contact))
            vendor_id=cur.lastrowid
            c.execute('INSERT INTO users(name,email,password,role,vendor_id,approved,created) VALUES(?,?,?,?,?,1,?)',
                      (rep,email,generate_password_hash(pwd),'vendor',vendor_id,stamp()))
            seed_vendor_catalog(c,vendor_id)
    except sqlite3.IntegrityError:
        flash('Vendor name or email is already registered.','error');return redirect(url_for('vendor_register'))
    flash('Your stall is live. Sign in to manage your 32 starter dishes.','success');return redirect(url_for('vendor_login'))
@app.get('/admin/login')
def admin_login():return render_template('admin-login.html',title='Administrator sign-in')

@app.get('/forgot-password')
def forgot_password():return render_template('forgot-password.html',title='Reset your password')
@app.post('/forgot-password')
def forgot_password_post():
    email=request.form.get('email','').strip().lower()
    with db() as c:
        user=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
        if user:
            token=secrets.token_urlsafe(32)
            c.execute('INSERT INTO password_resets(token_hash,user_id,expires) VALUES(?,?,?)',
                      (hashlib.sha256(token.encode()).hexdigest(),user['id'],(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat()))
            # Development-only; no email service configured. No recovery secret leaked to the browser.
            print('LOCAL PASSWORD RESET LINK:',request.url_root.rstrip('/')+url_for('reset_password',token=token),flush=True)
    flash('If the account exists, a recovery link was generated. Local demo: check the server terminal.','success')
    return redirect(url_for('forgot_password'))
@app.get('/reset-password/<token>')
def reset_password(token):
    with db() as c:r=c.execute('SELECT * FROM password_resets WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if not r or datetime.fromisoformat(r['expires'])<datetime.now(timezone.utc):abort(404)
    return render_template('reset-password.html',title='Set new password',token=token)
@app.post('/reset-password/<token>')
def reset_password_post(token):
    pwd=request.form.get('password','');confirm=request.form.get('confirm','')
    if len(pwd)<10 or pwd!=confirm:flash('Matching passwords of 10+ characters required.','error');return redirect(url_for('reset_password',token=token))
    key=hashlib.sha256(token.encode()).hexdigest()
    with db() as c:
        row=c.execute('SELECT * FROM password_resets WHERE token_hash=?',(key,)).fetchone()
        if not row or datetime.fromisoformat(row['expires'])<datetime.now(timezone.utc):abort(404)
        c.execute('UPDATE users SET password=? WHERE id=?',(generate_password_hash(pwd),row['user_id']))
        c.execute('DELETE FROM password_resets WHERE user_id=?',(row['user_id'],))
    flash('Password updated. Please sign in.','success');return redirect(url_for('role_selection'))

@app.get('/student/dashboard')
@require('student')
def student_dashboard():
    with db() as c:
        recent=c.execute('SELECT o.*,v.name vendor_name FROM orders o JOIN vendors v ON v.id=o.vendor_id WHERE user_id=? ORDER BY o.id DESC LIMIT 4',(g.user['id'],)).fetchall()
        featured=c.execute('SELECT f.*,v.name vendor_name FROM foods f JOIN vendors v ON v.id=f.vendor_id WHERE f.active=1 AND v.approved=1 ORDER BY f.id LIMIT 8').fetchall()
        vendors=c.execute('''SELECT v.id,v.name,COUNT(f.id) dishes FROM vendors v LEFT JOIN foods f ON f.vendor_id=v.id AND f.active=1 WHERE v.approved=1 GROUP BY v.id ORDER BY v.name''').fetchall()
    return render_template('student-dashboard.html',title='Your dashboard',recent=recent,featured=featured,vendors=vendors)

@app.get('/vendors')
@require('student')
def vendor_directory():
    with db() as c:
        vendors=c.execute('''SELECT v.*, COUNT(f.id) dish_count, MIN(f.price) starting_price
                           FROM vendors v LEFT JOIN foods f ON f.vendor_id=v.id AND f.active=1
                           WHERE v.approved=1 GROUP BY v.id ORDER BY v.name''').fetchall()
    return render_template('vendor-directory.html',title='Choose your food stall',vendors=vendors)

@app.get('/vendors/<int:vendor_id>/menu')
@require('student')
def vendor_food_menu(vendor_id):
    with db() as c:
        vendor=c.execute('SELECT * FROM vendors WHERE id=? AND approved=1',(vendor_id,)).fetchone()
    if not vendor:abort(404)
    return food_menu(vendor_override=str(vendor_id))

@app.get('/menu')
@require('student')
def food_menu(vendor_override=None):
    q=request.args.get('q','').strip()[:70];category=request.args.get('category','');vendor=vendor_override or request.args.get('vendor','')
    query='SELECT f.*,v.name vendor_name FROM foods f JOIN vendors v ON v.id=f.vendor_id WHERE f.active=1 AND v.approved=1';params=[]
    if q:query+=' AND (f.name LIKE ? OR v.name LIKE ? OR f.description LIKE ?)';params.extend(['%'+q+'%']*3)
    if category:query+=' AND f.category=?';params.append(category)
    if vendor.isdigit():query+=' AND f.vendor_id=?';params.append(int(vendor))
    query+=' ORDER BY f.id'
    with db() as c:
        foods=c.execute(query,params).fetchall();vendors=c.execute('SELECT id,name FROM vendors WHERE approved=1 ORDER BY name').fetchall()
        favorites={row['food_id'] for row in c.execute('SELECT food_id FROM favorites WHERE user_id=?',(g.user['id'],))}
    return render_template('food-menu.html',title='Explore the menu',foods=foods,vendors=vendors,q=q,selected_category=category,selected_vendor=vendor,selected_vendor_name=next((v['name'] for v in vendors if str(v['id'])==vendor),'All stalls'),favorites=favorites)

@app.get('/food/<int:food_id>')
@require('student')
def food_details(food_id):
    with db() as c:
        food=c.execute('SELECT f.*,v.name vendor_name FROM foods f JOIN vendors v ON v.id=f.vendor_id WHERE f.id=? AND f.active=1',(food_id,)).fetchone()
        liked=bool(c.execute('SELECT 1 FROM favorites WHERE user_id=? AND food_id=?',(g.user['id'],food_id)).fetchone())
    if not food:abort(404)
    return render_template('food-details.html',title=food['name'],food=food,liked=liked)

@app.post('/cart/items')
@require('student')
def add_cart():
    food_id=request.form.get('food_id','');change=request.form.get('change','1')
    try:food_id=int(food_id);change=int(change)
    except ValueError:abort(400)
    if change not in {-1,0,1} and not 1<=change<=20:abort(400)
    with db() as c:
        food=c.execute('SELECT * FROM foods WHERE id=? AND active=1',(food_id,)).fetchone()
        if not food:abort(404)
        own=c.execute('SELECT c.*,f.vendor_id FROM carts c JOIN foods f ON f.id=c.food_id WHERE c.user_id=?',(g.user['id'],)).fetchall()
        if change>0 and any(x['vendor_id']!=food['vendor_id'] for x in own):
            flash('Cart supports one vendor per order. Checkout or clear the existing stall first.','warning');return redirect(url_for('cart'))
        current=next((x['quantity'] for x in own if x['food_id']==food_id),0)
        qty=min(20,max(0,current+change))
        if qty==0:c.execute('DELETE FROM carts WHERE user_id=? AND food_id=?',(g.user['id'],food_id))
        else:c.execute('INSERT INTO carts(user_id,food_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,food_id) DO UPDATE SET quantity=excluded.quantity',(g.user['id'],food_id,qty))
    return redirect(request.referrer if request.referrer and urlparse(request.referrer).netloc==request.host else url_for('cart'))
@app.post('/cart/remove')
@require('student')
def cart_remove():
    with db() as c:
        if request.form.get('all')=='1':c.execute('DELETE FROM carts WHERE user_id=?',(g.user['id'],))
        else:c.execute('DELETE FROM carts WHERE user_id=? AND food_id=?',(g.user['id'],request.form.get('food_id','')))
    return redirect(url_for('cart'))
@app.get('/cart')
@require('student')
def cart():
    rows,total=cart_detail(g.user['id']);return render_template('cart.html',title='Your cart',rows=rows,total=total)

@app.get('/checkout')
@require('student')
def checkout():
    rows,total=cart_detail(g.user['id'])
    if not rows:flash('Your cart is empty.','warning');return redirect(url_for('food_menu'))
    session['checkout_nonce']=secrets.token_urlsafe(18)
    return render_template('checkout.html',title='Review checkout',rows=rows,total=total,nonce=session['checkout_nonce'])
@app.post('/checkout/confirm')
@require('student')
def checkout_confirm():
    nonce=request.form.get('nonce','')
    if not nonce or nonce!=session.get('checkout_nonce'):
        flash('Checkout expired. Please review your cart.','warning');return redirect(url_for('checkout'))
    rows,total=cart_detail(g.user['id'])
    if not rows:abort(400)
    if len({r['vendor_id'] for r in rows})!=1:abort(400)
    with db() as c:
        cur=c.execute('INSERT INTO orders(user_id,vendor_id,total,created) VALUES(?,?,?,?)',(g.user['id'],rows[0]['vendor_id'],total,stamp()))
        oid=cur.lastrowid
        c.executemany('INSERT INTO order_items(order_id,food_id,name,quantity,unit_price) VALUES(?,?,?,?,?)',
                      [(oid,r['id'],r['name'],r['quantity'],r['price']) for r in rows])
        c.execute('INSERT INTO status_history(order_id,status,created) VALUES(?,?,?)',(oid,'Awaiting Payment',stamp()))
    session.pop('checkout_nonce',None)
    return redirect(url_for('payment',oid=oid))

@app.get('/payment/<int:oid>')
@require('student')
def payment(oid):
    order=student_order(oid)
    if order['payment_state']=='Paid':return redirect(url_for('payment_result',oid=oid))
    provider_order_id=order['provider_order_id']
    if PAYMENT_PROVIDER=='razorpay_test' and not provider_order_id:
        payload={'amount':order['total']*100,'currency':'INR','receipt':f'synbyte-{oid}', 'notes':{'synbyte_order':str(oid)}}
        try:
            r=requests.post('https://api.razorpay.com/v1/orders',json=payload,auth=(RZ_KEY,RZ_SECRET),timeout=15)
            r.raise_for_status();data=r.json();provider_order_id=data['id']
            with db() as c:
                c.execute('UPDATE orders SET provider_order_id=? WHERE id=? AND provider_order_id IS NULL',(provider_order_id,oid))
        except (requests.RequestException,KeyError,ValueError) as exc:
            app.logger.error('Test gateway order creation failed: %s',exc)
            flash('Test gateway unavailable. Please retry.','error')
    return render_template('payment.html',title='Secure test payment',order=order,items=items_for(oid),provider_order_id=provider_order_id,razorpay_key=RZ_KEY)

@app.post('/payment/<int:oid>/demo')
@require('student')
def demo_payment(oid):
    if PAYMENT_PROVIDER!='demo':abort(404)
    order=student_order(oid)
    if order['payment_state']=='Paid':return redirect(url_for('payment_result',oid=oid))
    action=request.form.get('action')
    if action=='fail':
        with db() as c:
            c.execute('UPDATE orders SET payment_state="Failed" WHERE id=?',(oid,))
            notify(c,g.user['id'],'Demo payment failed','Try your simulated checkout again.',f'/payment/{oid}')
    elif action=='success':
        # The local simulator creates a signed server-side test event; never a claim of real payment.
        reference='DEMO-'+secrets.token_hex(10).upper()
        msg=f'{oid}|{reference}|{order["total"]}'.encode()
        sig=hmac.new(app.secret_key.encode(),msg,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig,hmac.new(app.secret_key.encode(),msg,hashlib.sha256).hexdigest()):abort(400)
        with db() as c:
            fulfill_paid(c,oid,reference,'local_demo_simulation')
            c.execute('DELETE FROM carts WHERE user_id=?',(g.user['id'],))
    else:abort(400)
    return redirect(url_for('payment_result',oid=oid))

@app.post('/payment/<int:oid>/verify')
@require('student')
def verify_razorpay(oid):
    if PAYMENT_PROVIDER!='razorpay_test':abort(404)
    order=student_order(oid)
    if order['payment_state']=='Paid':return redirect(url_for('payment_result',oid=oid))
    pid=request.form.get('razorpay_payment_id','');oid_provider=request.form.get('razorpay_order_id','');sig=request.form.get('razorpay_signature','')
    if not (pid and oid_provider and sig and oid_provider==order['provider_order_id']):abort(400)
    expected=hmac.new(RZ_SECRET.encode(),f'{oid_provider}|{pid}'.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,sig):abort(400,description='Invalid gateway signature')
    try:
        r=requests.get('https://api.razorpay.com/v1/payments/'+pid,auth=(RZ_KEY,RZ_SECRET),timeout=15)
        r.raise_for_status();payment_data=r.json()
    except (requests.RequestException,ValueError):abort(503,description='Could not verify gateway payment')
    if payment_data.get('order_id')!=oid_provider or payment_data.get('status')!='captured' or payment_data.get('amount')!=order['total']*100 or payment_data.get('currency')!='INR':
        flash('Payment not yet captured. Please retry after confirmation.','warning');return redirect(url_for('payment_result',oid=oid))
    with db() as c:
        fulfill_paid(c,oid,pid,'razorpay_test');c.execute('DELETE FROM carts WHERE user_id=?',(g.user['id'],))
    return redirect(url_for('payment_result',oid=oid))

@app.post('/api/razorpay/webhook')
def razorpay_webhook():
    if PAYMENT_PROVIDER!='razorpay_test' or not RZ_WEBHOOK:abort(404)
    raw=request.get_data();signature=request.headers.get('X-Razorpay-Signature','')
    expected=hmac.new(RZ_WEBHOOK.encode(),raw,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,signature):abort(401)
    event=request.get_json(silent=True) or {};event_id=request.headers.get('x-razorpay-event-id','')
    if not event_id:abort(400)
    typ=event.get('event','');ent=event.get('payload',{}).get('payment',{}).get('entity',{})
    with db() as c:
        exists=c.execute('SELECT 1 FROM payment_events WHERE event_id=?',(event_id,)).fetchone()
        if exists:return jsonify(ok=True,duplicate=True)
        c.execute('INSERT INTO payment_events(event_id,event_type,payload_hash,received) VALUES(?,?,?,?)',
                  (event_id,typ,hashlib.sha256(raw).hexdigest(),stamp()))
        if typ=='payment.captured':
            oid_provider=ent.get('order_id');pid=ent.get('id')
            order=c.execute('SELECT * FROM orders WHERE provider_order_id=?',(oid_provider,)).fetchone()
            if order and pid and ent.get('amount')==order['total']*100 and ent.get('currency')=='INR' and ent.get('status')=='captured':
                fulfill_paid(c,order['id'],pid,'razorpay_test')
                c.execute('DELETE FROM carts WHERE user_id=?',(order['user_id'],))
    return jsonify(ok=True)

@app.get('/payment/result/<int:oid>')
@require('student')
def payment_result(oid):return render_template('payment-result.html',title='Payment result',order=student_order(oid))
@app.get('/order/confirmation/<int:oid>')
@require('student')
def order_confirmation(oid):
    o=student_order(oid)
    if o['payment_state']!='Paid':return redirect(url_for('payment',oid=oid))
    return render_template('order-confirmation.html',title='Order confirmed',order=o,items=items_for(oid))
@app.get('/order/token/<int:oid>')
@require('student')
def pickup_token(oid):
    o=student_order(oid)
    if o['payment_state']!='Paid':abort(403)
    qr=None
    try:
        import qrcode
        im=qrcode.make(f'SYNBYTE|{o["id"]}|{o["token"]}')
        stream=io.BytesIO();im.save(stream,format='PNG')
        qr='data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()
    except ImportError:pass
    return render_template('pickup-token.html',title='Pickup token',order=o,qr=qr)
@app.get('/order/tracking/<int:oid>')
@require('student')
def order_tracking(oid):
    o=student_order(oid)
    with db() as c:hist=c.execute('SELECT * FROM status_history WHERE order_id=? ORDER BY id',(oid,)).fetchall()
    return render_template('order-tracking.html',title='Order tracking',order=o,history=hist)
@app.get('/orders')
@require('student')
def my_orders():
    with db() as c:orders=c.execute('SELECT o.*,v.name vendor_name FROM orders o JOIN vendors v ON v.id=o.vendor_id WHERE user_id=? ORDER BY o.id DESC',(g.user['id'],)).fetchall()
    return render_template('my-orders.html',title='My orders',orders=orders)
@app.get('/notifications')
@require('student')
def notifications():return render_template('notifications.html',title='Your notifications',messages=read_notifications(g.user['id']))

def read_notifications(uid):
    with db() as c:return c.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100',(uid,)).fetchall()
@app.get('/api/notifications')
@require()
def api_notifications():
    rows=read_notifications(g.user['id'])
    return jsonify([dict(x) for x in rows])
@app.get('/api/orders/<int:oid>/status')
@require('student')
def api_order_status(oid):
    o=student_order(oid);return jsonify(status=o['status'],paid=o['payment_state']=='Paid')
@app.post('/notifications/read')
@require()
def mark_read():
    nid=request.form.get('id','')
    with db() as c:
        if nid=='all':c.execute('UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL',(stamp(),g.user['id']))
        else:c.execute('UPDATE notifications SET read_at=? WHERE id=? AND user_id=?',(stamp(),nid,g.user['id']))
    return redirect(request.referrer if request.referrer and urlparse(request.referrer).netloc==request.host else url_for('vendor_notifications' if g.user['role']=='vendor' else 'notifications' if g.user['role']=='student' else 'admin_dashboard'))
@app.route('/student/profile',methods=['GET','POST'])
@require('student')
def student_profile():
    if request.method=='POST':
        name=request.form.get('name','').strip()[:80]
        if len(name)<2:flash('Enter a valid name.','error')
        else:
            with db() as c:c.execute('UPDATE users SET name=? WHERE id=?',(name,g.user['id']))
            flash('Profile saved.','success')
        return redirect(url_for('student_profile'))
    return render_template('student-profile.html',title='Your profile')
@app.get('/favorites')
@require('student')
def favorites():
    with db() as c:foods=c.execute('SELECT f.*,v.name vendor_name FROM favorites fa JOIN foods f ON f.id=fa.food_id JOIN vendors v ON v.id=f.vendor_id WHERE fa.user_id=?',(g.user['id'],)).fetchall()
    return render_template('favorites.html',title='Your favorites',foods=foods)
@app.post('/favorites/<int:food_id>/toggle')
@require('student')
def toggle_favorite(food_id):
    with db() as c:
        row=c.execute('SELECT 1 FROM favorites WHERE user_id=? AND food_id=?',(g.user['id'],food_id)).fetchone()
        if row:c.execute('DELETE FROM favorites WHERE user_id=? AND food_id=?',(g.user['id'],food_id))
        else:c.execute('INSERT OR IGNORE INTO favorites(user_id,food_id) VALUES(?,?)',(g.user['id'],food_id))
    return redirect(request.referrer if request.referrer and urlparse(request.referrer).netloc==request.host else url_for('favorites'))

# ---- Vendor: every management task has a separate route and HTML template ----
@app.get('/vendor/dashboard')
@require('vendor')
def vendor_dashboard():
    with db() as c:
        counts={r['status']:r['n'] for r in c.execute('SELECT status,COUNT(*) n FROM orders WHERE vendor_id=? AND payment_state="Paid" GROUP BY status',(g.user['vendor_id'],))}
        recent=c.execute('SELECT o.*,u.name consumer_name FROM orders o JOIN users u ON u.id=o.user_id WHERE o.vendor_id=? AND o.payment_state="Paid" ORDER BY o.id DESC LIMIT 8',(g.user['vendor_id'],)).fetchall()
        revenue=c.execute('SELECT COALESCE(SUM(total),0) total FROM orders WHERE vendor_id=? AND payment_state="Paid"',(g.user['vendor_id'],)).fetchone()['total']
    return render_template('vendor-dashboard.html',title='Vendor workspace',counts=counts,recent=recent,revenue=revenue)
@app.get('/vendor/orders')
@require('vendor')
def vendor_orders():
    with db() as c:orders=c.execute('SELECT o.*,u.name consumer_name FROM orders o JOIN users u ON u.id=o.user_id WHERE o.vendor_id=? AND o.payment_state="Paid" AND o.status NOT IN ("Collected","Cancelled") ORDER BY o.id DESC',(g.user['vendor_id'],)).fetchall()
    return render_template('vendor-orders.html',title='Incoming orders',orders=orders)
@app.get('/vendor/orders/<int:oid>')
@require('vendor')
def vendor_order_details(oid):return render_template('vendor-order-details.html',title='Order details',order=vendor_order(oid),items=items_for(oid))
@app.post('/vendor/orders/<int:oid>/status')
@require('vendor')
def vendor_set_status(oid):
    order=vendor_order(oid)
    nxt={'Placed':'Accepted','Accepted':'Preparing','Preparing':'Ready','Ready':'Collected'}
    wanted=request.form.get('status','')
    if nxt.get(order['status'])!=wanted:abort(400,description='Invalid order status transition')
    if wanted=='Collected':
        # Collection requires presenting the token to a dedicated verification page.
        abort(400,description='Verify the pickup token on the collection page')
    with db() as c:
        changed=c.execute('UPDATE orders SET status=? WHERE id=? AND status=?',(wanted,oid,order['status'])).rowcount
        if changed!=1:abort(409)
        c.execute('INSERT INTO status_history(order_id,status,created) VALUES(?,?,?)',(oid,wanted,stamp()))
        title={'Accepted':'Order accepted','Preparing':'Food preparation started','Ready':'Food ready for pickup'}[wanted]
        notify(c,order['user_id'],title,f'Order #{oid}: {wanted}. '+('Show your pickup token at the counter.' if wanted=='Ready' else ''),f'/order/tracking/{oid}')
    flash('Order updated to '+wanted+'.','success');return redirect(url_for('vendor_order_details',oid=oid))
@app.get('/vendor/kitchen')
@require('vendor')
def vendor_kitchen():
    with db() as c:orders=c.execute('SELECT o.*,u.name consumer_name FROM orders o JOIN users u ON u.id=o.user_id WHERE o.vendor_id=? AND o.payment_state="Paid" AND o.status IN ("Accepted","Preparing","Ready") ORDER BY o.id',(g.user['vendor_id'],)).fetchall()
    return render_template('vendor-kitchen.html',title='Kitchen board',orders=orders)
@app.get('/vendor/menu')
@require('vendor')
def vendor_menu():
    with db() as c:foods=c.execute('SELECT * FROM foods WHERE vendor_id=? ORDER BY id',(g.user['vendor_id'],)).fetchall()
    return render_template('vendor-menu.html',title='Manage your menu',foods=foods)

def process_upload(upload,food_id):
    if not upload or not upload.filename:return None
    ext=Path(secure_filename(upload.filename)).suffix.lower()
    if ext not in {'.jpeg','.jpg','.png','.webp'}:abort(400,description='Only JPG, PNG and WebP images are allowed')
    try:
        from PIL import Image,ImageOps
        image=Image.open(upload.stream);image.verify();upload.stream.seek(0)
        image=Image.open(upload.stream).convert('RGB')
        if image.width<320 or image.height<220:abort(400,description='Image needs to be at least 320 × 220 pixels')
        image=ImageOps.fit(image,(960,660),method=Image.Resampling.LANCZOS)
        rel=f'food/vendor-{food_id}-{secrets.token_hex(8)}.webp'
        image.save(BASE/'static/images'/rel,'WEBP',quality=86)
        return rel
    except (OSError,ValueError):abort(400,description='The image file is not valid')
@app.get('/vendor/food/new')
@require('vendor')
def vendor_add_food():return render_template('vendor-add-food.html',title='Add a dish')
@app.post('/vendor/food/new')
@require('vendor')
def vendor_add_food_post():
    name=request.form.get('name','').strip()[:90]
    desc=request.form.get('description','').strip()[:250]
    try:price=int(request.form.get('price','0'))
    except ValueError:price=0
    if len(name)<3 or not desc or not 1<=price<=10000:abort(400)
    with db() as c:
        cat=posted_category(c)
        cur=c.execute('INSERT INTO foods(vendor_id,name,category,price,description,image) VALUES(?,?,?,?,?,?)',(g.user['vendor_id'],name,cat,price,desc,'food/placeholder.svg'))
        rel=process_upload(request.files.get('image'),cur.lastrowid)
        if not rel:abort(400,description='A distinct dish photo is required for a new item')
        c.execute('UPDATE foods SET image=? WHERE id=?',(rel,cur.lastrowid))
        c.execute('INSERT INTO photo_credits(food_id,source,license,photographer) VALUES(?,?,?,?)',(cur.lastrowid,'Vendor upload','Vendor confirms upload rights',g.user['name']))
    flash('Dish added.','success');return redirect(url_for('vendor_menu'))
@app.get('/vendor/food/<int:food_id>/edit')
@require('vendor')
def vendor_edit_food(food_id):
    with db() as c:food=c.execute('SELECT * FROM foods WHERE id=? AND vendor_id=?',(food_id,g.user['vendor_id'])).fetchone()
    if not food:abort(404)
    return render_template('vendor-edit-food.html',title='Edit dish',food=food)
@app.post('/vendor/food/<int:food_id>/edit')
@require('vendor')
def vendor_edit_food_post(food_id):
    with db() as c:
        food=c.execute('SELECT * FROM foods WHERE id=? AND vendor_id=?',(food_id,g.user['vendor_id'])).fetchone()
        if not food:abort(404)
        name=request.form.get('name','').strip()[:90];desc=request.form.get('description','').strip()[:250]
        try:active=int(request.form.get('active','0'))
        except ValueError:active=-1
        try:price=int(request.form.get('price','0'))
        except ValueError:price=0
        if len(name)<3 or not desc or not 1<=price<=10000 or active not in (0,1):abort(400)
        cat=posted_category(c)
        image=process_upload(request.files.get('image'),food_id) or food['image']
        c.execute('UPDATE foods SET name=?,price=?,description=?,category=?,active=?,image=? WHERE id=?',(name,price,desc,cat,active,image,food_id))
        if image!=food['image']:
            c.execute('INSERT OR REPLACE INTO photo_credits(food_id,source,license,photographer) VALUES(?,?,?,?)',(food_id,'Vendor upload','Vendor confirms upload rights',g.user['name']))
    flash('Menu item updated.','success');return redirect(url_for('vendor_menu'))
@app.post('/vendor/food/<int:food_id>/visibility')
@require('vendor')
def vendor_food_visibility(food_id):
    active=request.form.get('active')
    if active not in ('0','1'):abort(400)
    with db() as c:
        changed=c.execute('UPDATE foods SET active=? WHERE id=? AND vendor_id=?',
                          (int(active),food_id,g.user['vendor_id'])).rowcount
        if not changed:abort(404)
        if active=='0':c.execute('DELETE FROM carts WHERE food_id=?',(food_id,))
    flash('Dish now '+('visible' if active=='1' else 'removed from student menu')+'.','success')
    return redirect(url_for('vendor_menu'))

@app.get('/vendor/notifications')
@require('vendor')
def vendor_notifications():return render_template('vendor-notifications.html',title='Vendor notifications',messages=read_notifications(g.user['id']))
@app.get('/vendor/history')
@require('vendor')
def vendor_history():
    with db() as c:orders=c.execute('SELECT o.*,u.name consumer_name FROM orders o JOIN users u ON u.id=o.user_id WHERE o.vendor_id=? AND o.payment_state="Paid" AND status="Collected" ORDER BY o.id DESC',(g.user['vendor_id'],)).fetchall()
    return render_template('vendor-history.html',title='Completed orders',orders=orders)
@app.get('/vendor/profile')
@require('vendor')
def vendor_profile():
    with db() as c:vendor=c.execute('SELECT * FROM vendors WHERE id=?',(g.user['vendor_id'],)).fetchone()
    return render_template('vendor-profile.html',title='Vendor profile',vendor=vendor)
@app.get('/vendor/verify-token')
@require('vendor')
def vendor_verify_token():return render_template('vendor-verify-token.html',title='Verify pickup token')
@app.post('/vendor/verify-token')
@require('vendor')
def vendor_verify_token_post():
    token=request.form.get('token','').strip().upper()
    with db() as c:
        order=c.execute('SELECT * FROM orders WHERE token=? AND vendor_id=? AND payment_state="Paid"',(token,g.user['vendor_id'])).fetchone()
        if not order:
            flash('Token not found for this vendor.','error');return redirect(url_for('vendor_verify_token'))
        if order['status']!='Ready':
            flash('Order is '+order['status']+'. Only Ready orders can be collected.','warning');return redirect(url_for('vendor_verify_token'))
        changed=c.execute('UPDATE orders SET status="Collected" WHERE id=? AND status="Ready"',(order['id'],)).rowcount
        if changed!=1:abort(409)
        c.execute('INSERT INTO status_history(order_id,status,created) VALUES(?,?,?)',(order['id'],'Collected',stamp()))
        notify(c,order['user_id'],'Order collected',f'Order #{order["id"]} was collected successfully.',f'/order/tracking/{order["id"]}')
    flash('Token verified. Food marked Collected.','success');return redirect(url_for('vendor_order_details',oid=order['id']))

# ---- Admin & support ----
@app.get('/admin/dashboard')
@require('admin')
def admin_dashboard():
    with db() as c:
        stats={'students':c.execute('SELECT COUNT(*) FROM users WHERE role="student"').fetchone()[0],
               'vendors':c.execute('SELECT COUNT(*) FROM vendors WHERE approved=1').fetchone()[0],
               'pending':c.execute('SELECT COUNT(*) FROM vendors WHERE approved=0').fetchone()[0],
               'orders':c.execute('SELECT COUNT(*) FROM orders WHERE payment_state="Paid"').fetchone()[0]}
    return render_template('admin-dashboard.html',title='Admin overview',stats=stats)
@app.get('/admin/vendors')
@require('admin')
def admin_vendors():
    with db() as c:vendors=c.execute('SELECT v.*,u.email contact_email FROM vendors v LEFT JOIN users u ON u.vendor_id=v.id AND u.role="vendor" ORDER BY v.approved, v.id DESC').fetchall()
    return render_template('admin-vendors.html',title='Vendor approvals',vendors=vendors)
@app.post('/admin/vendors/<int:vid>/approve')
@require('admin')
def admin_approve_vendor(vid):
    with db() as c:
        v=c.execute('SELECT id FROM vendors WHERE id=?',(vid,)).fetchone()
        if not v:abort(404)
        c.execute('UPDATE vendors SET approved=1 WHERE id=?',(vid,))
        c.execute('UPDATE users SET approved=1 WHERE vendor_id=?',(vid,))
        for u in c.execute('SELECT id FROM users WHERE vendor_id=?',(vid,)):
            notify(c,u['id'],'Vendor approved','Your vendor account is now active.','/vendor/dashboard')
    flash('Vendor approved.','success');return redirect(url_for('admin_vendors'))
@app.get('/admin/orders')
@require('admin')
def admin_orders():
    with db() as c:orders=c.execute('SELECT o.*,v.name vendor_name,u.name consumer_name FROM orders o JOIN vendors v ON v.id=o.vendor_id JOIN users u ON u.id=o.user_id ORDER BY o.id DESC LIMIT 200').fetchall()
    return render_template('admin-orders.html',title='Platform orders',orders=orders)
@app.get('/help')
def help_support():return render_template('help-support.html',title='Help and support')
@app.get('/photo-credits')
def photo_credits():
    with db() as c:credits=c.execute('SELECT f.name,f.image,pc.source,pc.license,pc.photographer FROM photo_credits pc JOIN foods f ON f.id=pc.food_id ORDER BY f.id').fetchall()
    return render_template('photo-credits.html',title='Food image credits',credits=credits)
@app.errorhandler(403)
def forbidden(e):return render_template('error.html',title='Access restricted',message=str(e)),403
@app.errorhandler(404)
def missing(e):return render_template('error.html',title='Page not found',message='This page or order does not exist.'),404
@app.errorhandler(400)
def invalid(e):return render_template('error.html',title='Invalid request',message=str(e)),400

init_db()
if __name__=='__main__':
    app.run(host='127.0.0.1',port=int(os.environ.get('PORT','5000')),debug=os.environ.get('FLASK_DEBUG','0')=='1')
