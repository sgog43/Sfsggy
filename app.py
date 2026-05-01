from flask import Flask, request, jsonify, session as flask_session, redirect
from flask_cors import CORS
from bs4 import BeautifulSoup
import requests
import random
import time
import hashlib
import os
import psycopg2
import psycopg2.extras
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)
app.secret_key = "amazon_finder_secret_2024_xK9mP"
CORS(app)

# ══════════════════════════════════════════
#  CONFIG — Render এ Environment Variable এ দাও
# ══════════════════════════════════════════
AMAZON_TAG  = os.environ.get("AMAZON_TAG", "your-tag-20")
ADMIN_USER  = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS  = os.environ.get("ADMIN_PASS", "Sadik@Admin2024")
DATABASE_URL= os.environ.get("DATABASE_URL",
    "postgresql://postgres:Sadik@123478@db.wauipcegsqpsteouqwqz.supabase.co:5432/postgres")
# ══════════════════════════════════════════

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

scrape_session = requests.Session()

# ══════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id       SERIAL PRIMARY KEY,
            email    TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name     TEXT,
            created  TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS tracked (
            id      SERIAL PRIMARY KEY,
            user_id INTEGER,
            asin    TEXT NOT NULL,
            name    TEXT,
            image   TEXT,
            url     TEXT,
            added   TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, asin)
        );
        CREATE TABLE IF NOT EXISTS price_history (
            id      SERIAL PRIMARY KEY,
            asin    TEXT NOT NULL,
            price   FLOAT,
            checked TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS search_logs (
            id      SERIAL PRIMARY KEY,
            user_id INTEGER,
            keyword TEXT,
            results INTEGER,
            ts      TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS click_logs (
            id      SERIAL PRIMARY KEY,
            user_id INTEGER,
            asin    TEXT,
            name    TEXT,
            price   FLOAT,
            ts      TIMESTAMP DEFAULT NOW()
        );
    """)
    conn.commit()
    conn.close()

try:
    init_db()
    print("Database connected and tables ready.")
except Exception as e:
    print(f"Database error: {e}")

# ══════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════
def hash_pass(p):
    return hashlib.sha256(p.encode()).hexdigest()

def logged_in():
    return flask_session.get('user_id')

def admin_in():
    return flask_session.get('is_admin')

def req_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1", "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }

def parse_price(item):
    whole = item.select_one(".a-price .a-price-whole")
    frac  = item.select_one(".a-price .a-price-fraction")
    if whole:
        try:
            w = whole.get_text(strip=True).replace(",","").replace(".","")
            f = frac.get_text(strip=True) if frac else "00"
            return float(f"{w}.{f}")
        except: pass
    el = item.select_one(".a-offscreen")
    if el:
        try: return float(el.get_text(strip=True).replace("$","").replace(",",""))
        except: pass
    for cls in [".a-color-price",".a-price-range",".a-size-base.a-color-price"]:
        el = item.select_one(cls)
        if el:
            txt = el.get_text(strip=True).replace("$","").replace(",","").split("-")[0].strip()
            try: return float(txt)
            except: pass
    return 0.0

def parse_rating(item):
    for el in item.select("[aria-label]"):
        lbl = el.get("aria-label","")
        if "out of 5 stars" in lbl:
            try: return float(lbl.split(" out of")[0].strip())
            except: pass
    return 0.0

def parse_reviews(item):
    for el in item.select("a[aria-label]"):
        lbl = el.get("aria-label","")
        if "rating" in lbl.lower() or "review" in lbl.lower():
            try: return int(lbl.replace(",","").split(" ")[0])
            except: pass
    for el in item.select(".a-size-base.s-underline-text"):
        txt = el.get_text(strip=True).replace(",","")
        if txt.isdigit(): return int(txt)
    return 0

def parse_name(item):
    for sel in ["h2 span.a-text-normal","h2 a span","[data-cy='title-recipe'] span","h2"]:
        el = item.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t and len(t)>3: return t
    return "N/A"

def parse_image(item):
    for sel in ["img.s-image","img[data-image-index]",".s-product-image-container img"]:
        img = item.select_one(sel)
        if img:
            src = img.get("src","") or img.get("data-src","")
            if src: return src
    return ""

def parse_badge(item):
    b = item.select_one(".a-badge-text")
    return b.get_text(strip=True) if b else ""

def product_url(asin):
    return f"https://www.amazon.com/dp/{asin}?tag={AMAZON_TAG}"

def calc_deal_score(price, rating, reviews, prime):
    import math
    score = 0
    if rating  > 0: score += (rating/5.0)*40
    if reviews > 0: score += min(math.log10(reviews+1)/math.log10(100001)*30, 30)
    if prime:       score += 15
    if price   > 0: score += max(0,(1-min(price,500)/500)*15)
    return round(score)

def fetch_page(args):
    keyword, page, filters = args
    time.sleep(random.uniform(1.5, 3.0))
    kw  = keyword.replace(" ","+")
    url = f"https://www.amazon.com/s?k={kw}&page={page}&ref=sr_pg_{page}"
    try:
        resp = scrape_session.get(url, headers=req_headers(), timeout=20)
        if resp.status_code == 503:
            time.sleep(8)
            resp = scrape_session.get(url, headers=req_headers(), timeout=20)
        if resp.status_code != 200: return [], 0
        soup  = BeautifulSoup(resp.text, "html.parser")
        items = soup.select('[data-component-type="s-search-result"]')
        total = len(items)
        out   = []
        for item in items:
            try:
                asin = item.get("data-asin","")
                if not asin or len(asin)<4: continue
                price   = parse_price(item)
                rating  = parse_rating(item)
                reviews = parse_reviews(item)
                name    = parse_name(item)
                if filters["min_price"]>0 and price>0 and price<filters["min_price"]: continue
                if filters["max_price"]>0 and price>0 and price>filters["max_price"]: continue
                if filters["min_rating"]>0 and rating>0 and rating<filters["min_rating"]: continue
                if filters["min_reviews"]>0 and reviews<filters["min_reviews"]: continue
                if filters["prime_only"] and not item.select_one(".s-prime,.a-icon-prime,[aria-label*='Prime']"): continue
                is_prime = bool(item.select_one(".s-prime,.a-icon-prime,[aria-label*='Prime']"))
                out.append({
                    "asin": asin, "name": name, "price": price,
                    "price_str": f"${price:.2f}" if price>0 else "N/A",
                    "rating": rating, "reviews": reviews, "prime": is_prime,
                    "badge": parse_badge(item), "image": parse_image(item),
                    "url": product_url(asin),
                    "deal_score": calc_deal_score(price, rating, reviews, is_prime),
                })
            except: continue
        return out, total
    except Exception as e:
        print(f"Page {page} error: {e}")
        return [], 0

def dedup(products):
    seen, out = set(), []
    for p in products:
        if p["asin"] not in seen:
            seen.add(p["asin"]); out.append(p)
    return out

# ══════════════════════════════════════════
#  COMMON STYLES
# ══════════════════════════════════════════
BASE_CSS = """
<style>
:root{
  --bg:#060608;--bg2:#0E0E12;--bg3:#15151C;--bg4:#1C1C26;
  --border:#22222E;--border2:#2A2A38;
  --amber:#F59E0B;--amber2:#FCD34D;
  --blue:#3B82F6;--green:#10B981;--red:#EF4444;--purple:#8B5CF6;
  --text:#F1F1F8;--muted:#6B6B8A;--muted2:#9999B8;
}
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box;}
html{scroll-behavior:smooth;}
body{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;min-height:100vh;}
a{color:inherit;text-decoration:none;}
input,select,button,textarea{font-family:inherit;}
.wrap{max-width:1300px;margin:0 auto;padding:0 20px;}
nav{padding:14px 20px;border-bottom:1px solid var(--border);background:rgba(6,6,8,.92);backdrop-filter:blur(12px);position:sticky;top:0;z-index:100;}
.nav-inner{max-width:1300px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;}
.brand{font-family:'Bebas Neue',sans-serif;font-size:1.5rem;letter-spacing:1px;}
.brand span{color:var(--amber);}
.nav-links{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
.nav-link{padding:7px 14px;border-radius:8px;font-size:.82rem;font-weight:600;background:var(--bg3);border:1px solid var(--border2);color:var(--muted2);cursor:pointer;transition:all .18s;display:inline-block;}
.nav-link:hover,.nav-link.active{border-color:var(--amber);color:var(--amber);}
.nav-link.danger:hover{border-color:var(--red);color:var(--red);}
.btn{display:inline-block;padding:11px 22px;border-radius:10px;font-size:.88rem;font-weight:700;cursor:pointer;border:none;transition:all .2s;}
.btn-amber{background:linear-gradient(135deg,var(--amber),var(--amber2));color:#000;box-shadow:0 4px 16px rgba(245,158,11,.3);}
.btn-amber:hover{opacity:.9;}
.btn-outline{background:transparent;border:1.5px solid var(--border2);color:var(--muted2);}
.btn-outline:hover{border-color:var(--amber);color:var(--amber);}
.btn-red{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);color:#F87171;}
.btn-red:hover{background:rgba(239,68,68,.2);}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:14px;padding:20px;}
.badge{display:inline-block;padding:3px 10px;border-radius:50px;font-size:.68rem;font-weight:700;letter-spacing:.05em;}
.badge-green{background:rgba(16,185,129,.12);color:#34D399;border:1px solid rgba(16,185,129,.2);}
.badge-amber{background:rgba(245,158,11,.12);color:var(--amber2);border:1px solid rgba(245,158,11,.2);}
.badge-red{background:rgba(239,68,68,.12);color:#F87171;border:1px solid rgba(239,68,68,.2);}
.badge-blue{background:rgba(59,130,246,.12);color:#60A5FA;border:1px solid rgba(59,130,246,.2);}
.form-group{display:flex;flex-direction:column;gap:6px;margin-bottom:14px;}
.form-group label{font-size:.68rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;}
.form-group input{background:var(--bg3);border:1.5px solid var(--border2);border-radius:9px;padding:12px 14px;color:var(--text);font-size:.92rem;outline:none;transition:border-color .2s;}
.form-group input:focus{border-color:var(--amber);}
.alert{padding:12px 16px;border-radius:10px;font-size:.85rem;margin-bottom:14px;}
.alert-red{background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);color:#FCA5A5;}
.alert-green{background:rgba(16,185,129,.08);border:1px solid rgba(16,185,129,.2);color:#6EE7B7;}
table{width:100%;border-collapse:collapse;}
th{text-align:left;font-size:.68rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;padding:10px 12px;border-bottom:1px solid var(--border);}
td{padding:10px 12px;font-size:.83rem;border-bottom:1px solid var(--border);vertical-align:middle;}
tr:last-child td{border-bottom:none;}
tr:hover td{background:var(--bg3);}
.stat-card{background:var(--bg2);border:1px solid var(--border);border-radius:14px;padding:20px;}
.stat-num{font-size:2rem;font-weight:700;margin-bottom:4px;}
.stat-lbl{font-size:.75rem;color:var(--muted2);}
</style>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
"""

def nav_html(page=""):
    user  = flask_session.get('user_name','')
    uid   = flask_session.get('user_id')
    admin = flask_session.get('is_admin')
    links = []
    if admin:
        links += [
            f'<a class="nav-link {"active" if page=="admin" else ""}" href="/admin">Admin Panel</a>',
            f'<a class="nav-link" href="/">Search</a>',
            f'<a class="nav-link danger" href="/logout">Logout</a>',
        ]
    elif uid:
        links += [
            f'<a class="nav-link" href="/">Search</a>',
            f'<a class="nav-link {"active" if page=="tracked" else ""}" href="/my-products">My Products</a>',
            f'<span style="font-size:.8rem;color:var(--muted2);padding:0 6px;">Hi, {user}</span>',
            f'<a class="nav-link danger" href="/logout">Logout</a>',
        ]
    else:
        links += [
            f'<a class="nav-link" href="/">Search</a>',
            f'<a class="nav-link {"active" if page=="login" else ""}" href="/login">Login</a>',
            f'<a class="nav-link {"active" if page=="register" else ""}" href="/register">Register</a>',
        ]
    return f"""<nav><div class="nav-inner">
      <a class="brand" href="/">Amazon <span>Finder</span></a>
      <div class="nav-links">{''.join(links)}</div>
    </div></nav>"""

# ══════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════
@app.route('/login', methods=['GET','POST'])
def login():
    error = ""
    if request.method == 'POST':
        email = request.form.get('email','').strip()
        pwd   = request.form.get('password','')
        if email == ADMIN_USER and pwd == ADMIN_PASS:
            flask_session['is_admin']  = True
            flask_session['user_name'] = 'Admin'
            return redirect('/admin')
        try:
            conn = get_db(); c = conn.cursor()
            c.execute("SELECT * FROM users WHERE email=%s AND password=%s", (email, hash_pass(pwd)))
            user = c.fetchone(); conn.close()
            if user:
                flask_session['user_id']   = user['id']
                flask_session['user_name'] = user['name'] or user['email'].split('@')[0]
                return redirect('/')
            else:
                error = "Wrong email or password."
        except Exception as e:
            error = f"Database error: {e}"

    return f"""<!DOCTYPE html><html><head><title>Login</title>{BASE_CSS}</head><body>
    {nav_html('login')}
    <div class="wrap" style="max-width:420px;margin:60px auto;padding:0 20px;">
      <div class="card">
        <h2 style="font-family:'Bebas Neue',sans-serif;font-size:1.8rem;letter-spacing:1px;margin-bottom:6px;">Welcome Back</h2>
        <p style="color:var(--muted2);font-size:.85rem;margin-bottom:24px;">Login to track product prices</p>
        {f'<div class="alert alert-red">{error}</div>' if error else ''}
        <form method="POST">
          <div class="form-group"><label>Email or Username</label><input type="text" name="email" placeholder="you@email.com" required></div>
          <div class="form-group"><label>Password</label><input type="password" name="password" placeholder="••••••••" required></div>
          <button class="btn btn-amber" style="width:100%;margin-top:8px;">Login</button>
        </form>
        <p style="text-align:center;margin-top:16px;font-size:.82rem;color:var(--muted2);">
          No account? <a href="/register" style="color:var(--amber);font-weight:600;">Register →</a>
        </p>
      </div>
    </div></body></html>"""

@app.route('/register', methods=['GET','POST'])
def register():
    error = msg = ""
    if request.method == 'POST':
        name  = request.form.get('name','').strip()
        email = request.form.get('email','').strip()
        pwd   = request.form.get('password','')
        if len(pwd) < 6:
            error = "Password must be at least 6 characters."
        else:
            try:
                conn = get_db(); c = conn.cursor()
                c.execute("INSERT INTO users (name,email,password) VALUES (%s,%s,%s)",
                          (name, email, hash_pass(pwd)))
                conn.commit(); conn.close()
                msg = "Account created! You can now login."
            except psycopg2.errors.UniqueViolation:
                error = "Email already registered."
            except Exception as e:
                error = f"Error: {e}"

    return f"""<!DOCTYPE html><html><head><title>Register</title>{BASE_CSS}</head><body>
    {nav_html('register')}
    <div class="wrap" style="max-width:420px;margin:60px auto;padding:0 20px;">
      <div class="card">
        <h2 style="font-family:'Bebas Neue',sans-serif;font-size:1.8rem;letter-spacing:1px;margin-bottom:6px;">Create Account</h2>
        <p style="color:var(--muted2);font-size:.85rem;margin-bottom:24px;">Track Amazon prices for free</p>
        {f'<div class="alert alert-red">{error}</div>' if error else ''}
        {f'<div class="alert alert-green">{msg}</div>' if msg else ''}
        <form method="POST">
          <div class="form-group"><label>Name</label><input type="text" name="name" placeholder="Your name" required></div>
          <div class="form-group"><label>Email</label><input type="email" name="email" placeholder="you@email.com" required></div>
          <div class="form-group"><label>Password</label><input type="password" name="password" placeholder="Min 6 characters" required></div>
          <button class="btn btn-amber" style="width:100%;margin-top:8px;">Create Account</button>
        </form>
        <p style="text-align:center;margin-top:16px;font-size:.82rem;color:var(--muted2);">
          Have an account? <a href="/login" style="color:var(--amber);font-weight:600;">Login →</a>
        </p>
      </div>
    </div></body></html>"""

@app.route('/logout')
def logout():
    flask_session.clear()
    return redirect('/')

# ══════════════════════════════════════════
#  TRACKING API
# ══════════════════════════════════════════
@app.route('/api/track', methods=['POST'])
def track():
    if not logged_in(): return jsonify({'error':'login required'}), 401
    d = request.get_json()
    asin  = d.get('asin','')
    name  = d.get('name','')
    img   = d.get('image','')
    url   = d.get('url','')
    price = d.get('price', 0)
    if not asin: return jsonify({'error':'asin required'}), 400
    try:
        conn = get_db(); c = conn.cursor()
        c.execute("INSERT INTO tracked (user_id,asin,name,image,url) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                  (flask_session['user_id'], asin, name, img, url))
        if price > 0:
            c.execute("INSERT INTO price_history (asin,price) VALUES (%s,%s)", (asin, price))
        conn.commit(); conn.close()
        return jsonify({'tracked': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/untrack', methods=['POST'])
def untrack():
    if not logged_in(): return jsonify({'error':'login required'}), 401
    asin = request.get_json().get('asin','')
    conn = get_db(); c = conn.cursor()
    c.execute("DELETE FROM tracked WHERE user_id=%s AND asin=%s", (flask_session['user_id'], asin))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/log-click', methods=['POST'])
def log_click():
    d   = request.get_json()
    uid = flask_session.get('user_id')
    try:
        conn = get_db(); c = conn.cursor()
        c.execute("INSERT INTO click_logs (user_id,asin,name,price) VALUES (%s,%s,%s,%s)",
                  (uid, d.get('asin'), d.get('name'), d.get('price',0)))
        conn.commit(); conn.close()
    except: pass
    return jsonify({'ok': True})

@app.route('/api/tracked-list')
def tracked_list():
    if not logged_in(): return jsonify({'items':[]})
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT asin FROM tracked WHERE user_id=%s", (flask_session['user_id'],))
    rows = c.fetchall(); conn.close()
    return jsonify({'items': [r['asin'] for r in rows]})

@app.route('/api/price-history/<asin>')
def price_history(asin):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT price, checked FROM price_history WHERE asin=%s ORDER BY checked ASC LIMIT 90", (asin,))
    rows = c.fetchall(); conn.close()
    return jsonify({'history': [{'price': r['price'], 'date': str(r['checked'])[:10]} for r in rows]})

# ══════════════════════════════════════════
#  MY PRODUCTS PAGE
# ══════════════════════════════════════════
@app.route('/my-products')
def my_products():
    if not logged_in(): return redirect('/login')
    conn = get_db(); c = conn.cursor()
    c.execute("""
        SELECT t.*,
          (SELECT price FROM price_history WHERE asin=t.asin ORDER BY checked DESC LIMIT 1) as last_price,
          (SELECT price FROM price_history WHERE asin=t.asin ORDER BY checked ASC  LIMIT 1) as first_price,
          (SELECT COUNT(*) FROM price_history WHERE asin=t.asin) as checks
        FROM tracked t WHERE t.user_id=%s ORDER BY t.added DESC
    """, (flask_session['user_id'],))
    items = c.fetchall(); conn.close()

    cards = ""
    for it in items:
        lp = it['last_price']  or 0
        fp = it['first_price'] or 0
        diff = lp - fp
        if diff < -0.5:  trend = f'<span class="badge badge-green">↓ ${abs(diff):.2f} cheaper</span>'
        elif diff > 0.5: trend = f'<span class="badge badge-red">↑ ${diff:.2f} pricier</span>'
        else:            trend = f'<span class="badge badge-amber">→ Stable</span>'
        cards += f"""
        <div class="card" style="display:flex;gap:16px;margin-bottom:14px;align-items:flex-start;">
          <img src="{it['image']}" style="width:80px;height:80px;object-fit:contain;border-radius:10px;background:var(--bg3);flex-shrink:0;" onerror="this.src='https://placehold.co/80x80'">
          <div style="flex:1;min-width:0;">
            <div style="font-size:.85rem;font-weight:600;margin-bottom:6px;line-height:1.4;">{(it['name'] or '')[:80]}</div>
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:8px;">
              <span style="font-size:1.1rem;font-weight:700;color:var(--amber);">{"${:.2f}".format(lp) if lp else "N/A"}</span>
              {trend}
              <span style="font-size:.72rem;color:var(--muted2);">Checked {it['checks']} times</span>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
              <a href="/price-graph/{it['asin']}" class="btn btn-outline" style="padding:6px 14px;font-size:.75rem;">📈 Price Graph</a>
              <a href="{it['url']}" target="_blank" class="btn btn-amber" style="padding:6px 14px;font-size:.75rem;">View →</a>
              <button class="btn btn-red" style="padding:6px 14px;font-size:.75rem;" onclick="untrack('{it['asin']}',this)">Remove</button>
            </div>
          </div>
        </div>"""

    if not items:
        cards = """<div style="text-align:center;padding:4rem 1rem;">
          <div style="font-size:3rem;margin-bottom:1rem;">📦</div>
          <h3 style="font-family:'Bebas Neue',sans-serif;font-size:1.6rem;letter-spacing:1px;margin-bottom:.5rem;">No Tracked Products</h3>
          <p style="color:var(--muted2);margin-bottom:20px;">Search and click 📌 to track prices.</p>
          <a href="/" class="btn btn-amber">Search Now →</a>
        </div>"""

    return f"""<!DOCTYPE html><html><head><title>My Products</title>{BASE_CSS}</head><body>
    {nav_html('tracked')}
    <div class="wrap" style="padding-top:30px;padding-bottom:60px;">
      <h2 style="font-family:'Bebas Neue',sans-serif;font-size:2rem;letter-spacing:1px;margin-bottom:6px;">My Tracked Products</h2>
      <p style="color:var(--muted2);font-size:.85rem;margin-bottom:24px;">Price checked automatically every night.</p>
      {cards}
    </div>
    <script>
    async function untrack(asin,btn){{
      if(!confirm('Remove this product?')) return;
      await fetch('/api/untrack',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{asin}})}});
      btn.closest('.card').remove();
    }}
    </script></body></html>"""

# ══════════════════════════════════════════
#  PRICE GRAPH
# ══════════════════════════════════════════
@app.route('/price-graph/<asin>')
def price_graph(asin):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT name,image,url FROM tracked WHERE asin=%s LIMIT 1", (asin,))
    prod = c.fetchone(); conn.close()
    name  = prod['name']  if prod else asin
    image = prod['image'] if prod else ''
    url   = prod['url']   if prod else product_url(asin)

    return f"""<!DOCTYPE html><html><head>
    <title>Price Graph</title>{BASE_CSS}
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head><body>
    {nav_html()}
    <div class="wrap" style="padding-top:30px;padding-bottom:60px;max-width:900px;">
      <div style="display:flex;gap:16px;align-items:center;margin-bottom:24px;flex-wrap:wrap;">
        <img src="{image}" style="width:70px;height:70px;object-fit:contain;border-radius:10px;background:var(--bg3);" onerror="this.src='https://placehold.co/70x70'">
        <div>
          <h2 style="font-size:1rem;font-weight:600;line-height:1.4;margin-bottom:6px;">{name}</h2>
          <a href="{url}" target="_blank" class="btn btn-amber" style="padding:7px 16px;font-size:.78rem;">View on Amazon →</a>
        </div>
      </div>
      <div class="card" style="margin-bottom:20px;">
        <canvas id="priceChart" height="100"></canvas>
        <p id="noData" style="display:none;text-align:center;padding:2rem;color:var(--muted2);">
          No price data yet. Come back after the nightly check!
        </p>
      </div>
      <div id="statsRow" style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;"></div>
    </div>
    <script>
    async function loadChart(){{
      const res  = await fetch('/api/price-history/{asin}');
      const data = await res.json();
      const hist = data.history;
      if(!hist||hist.length===0){{
        document.getElementById('noData').style.display='block';
        document.getElementById('priceChart').style.display='none';
        return;
      }}
      const labels=hist.map(h=>h.date);
      const prices=hist.map(h=>h.price);
      const minP=Math.min(...prices),maxP=Math.max(...prices),lastP=prices[prices.length-1];
      document.getElementById('statsRow').innerHTML=`
        <div class="stat-card"><div class="stat-num" style="color:var(--green);">$${{minP.toFixed(2)}}</div><div class="stat-lbl">Lowest Price</div></div>
        <div class="stat-card"><div class="stat-num" style="color:var(--red);">$${{maxP.toFixed(2)}}</div><div class="stat-lbl">Highest Price</div></div>
        <div class="stat-card"><div class="stat-num" style="color:var(--amber);">$${{lastP.toFixed(2)}}</div><div class="stat-lbl">Current Price</div></div>`;
      new Chart(document.getElementById('priceChart'),{{
        type:'line',
        data:{{labels,datasets:[{{
          label:'Price ($)',data:prices,
          borderColor:'#F59E0B',backgroundColor:'rgba(245,158,11,.08)',
          borderWidth:2.5,pointBackgroundColor:'#F59E0B',pointRadius:4,tension:0.3,fill:true
        }}]}},
        options:{{
          responsive:true,
          plugins:{{legend:{{labels:{{color:'#9999B8'}}}},tooltip:{{callbacks:{{label:ctx=>' $'+ctx.parsed.y.toFixed(2)}}}}}},
          scales:{{
            x:{{ticks:{{color:'#6B6B8A'}},grid:{{color:'#22222E'}}}},
            y:{{ticks:{{color:'#6B6B8A',callback:v=>'$'+v}},grid:{{color:'#22222E'}}}}
          }}
        }}
      }});
    }}
    loadChart();
    </script></body></html>"""

# ══════════════════════════════════════════
#  ADMIN PANEL
# ══════════════════════════════════════════
@app.route('/admin')
def admin():
    if not admin_in(): return redirect('/login')
    conn = get_db(); c = conn.cursor()

    c.execute("SELECT COUNT(*) as n FROM users");             total_users    = c.fetchone()['n']
    c.execute("SELECT COUNT(*) as n FROM tracked");           total_tracked  = c.fetchone()['n']
    c.execute("SELECT COUNT(*) as n FROM search_logs");       total_searches = c.fetchone()['n']
    c.execute("SELECT COUNT(*) as n FROM click_logs");        total_clicks   = c.fetchone()['n']
    c.execute("SELECT COUNT(*) as n FROM price_history");     total_prices   = c.fetchone()['n']

    c.execute("SELECT keyword, COUNT(*) as cnt FROM search_logs GROUP BY keyword ORDER BY cnt DESC LIMIT 10")
    top_kw = c.fetchall()

    c.execute("SELECT asin, name, COUNT(*) as cnt FROM tracked GROUP BY asin, name ORDER BY cnt DESC LIMIT 10")
    top_tracked = c.fetchall()

    c.execute("""SELECT s.keyword, s.results, s.ts, u.email
                 FROM search_logs s LEFT JOIN users u ON s.user_id=u.id
                 ORDER BY s.ts DESC LIMIT 20""")
    recent_searches = c.fetchall()

    c.execute("""SELECT c.name, c.price, c.ts, u.email
                 FROM click_logs c LEFT JOIN users u ON c.user_id=u.id
                 ORDER BY c.ts DESC LIMIT 20""")
    recent_clicks = c.fetchall()

    c.execute("SELECT id,name,email,created FROM users ORDER BY created DESC")
    users = c.fetchall()
    conn.close()

    kw_rows  = "".join(f"<tr><td><strong>{r['keyword']}</strong></td><td><span class='badge badge-amber'>{r['cnt']}</span></td></tr>" for r in top_kw)
    tr_rows  = "".join(f"<tr><td style='max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>{(r['name'] or '')[:50]}</td><td style='color:var(--muted2);font-size:.75rem;'>{r['asin']}</td><td><span class='badge badge-blue'>{r['cnt']} users</span></td></tr>" for r in top_tracked)
    sr_rows  = "".join(f"<tr><td><strong>{r['keyword']}</strong></td><td>{r['results'] or '-'}</td><td>{r['email'] or 'Guest'}</td><td style='color:var(--muted2);font-size:.75rem;'>{str(r['ts'])[:16]}</td></tr>" for r in recent_searches)
    cl_rows  = "".join(f"<tr><td style='max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>{r['name'] or '-'}</td><td style='color:var(--amber);'>${'{:.2f}'.format(r['price']) if r['price'] else 'N/A'}</td><td>{r['email'] or 'Guest'}</td><td style='color:var(--muted2);font-size:.75rem;'>{str(r['ts'])[:16]}</td></tr>" for r in recent_clicks)
    user_rows= "".join(f"<tr><td>{r['id']}</td><td>{r['name'] or '-'}</td><td>{r['email']}</td><td style='color:var(--muted2);font-size:.75rem;'>{str(r['created'])[:16]}</td></tr>" for r in users)

    return f"""<!DOCTYPE html><html><head><title>Admin Panel</title>{BASE_CSS}
    <style>
    .admin-grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:28px;}}
    .section{{margin-bottom:28px;}}
    .section-title{{font-family:'Bebas Neue',sans-serif;font-size:1.3rem;letter-spacing:1px;margin-bottom:14px;color:var(--amber2);}}
    @media(max-width:800px){{.admin-grid{{grid-template-columns:1fr 1fr;}}}}
    </style>
    </head><body>
    {nav_html('admin')}
    <div class="wrap" style="padding-top:28px;padding-bottom:60px;">
      <h1 style="font-family:'Bebas Neue',sans-serif;font-size:2.2rem;letter-spacing:1px;margin-bottom:6px;">Admin Panel</h1>
      <p style="color:var(--muted2);font-size:.82rem;margin-bottom:24px;">Full overview — Supabase PostgreSQL</p>

      <div class="admin-grid">
        <div class="stat-card"><div class="stat-num" style="color:var(--blue);">{total_users}</div><div class="stat-lbl">Total Users</div></div>
        <div class="stat-card"><div class="stat-num" style="color:var(--amber);">{total_tracked}</div><div class="stat-lbl">Tracked Products</div></div>
        <div class="stat-card"><div class="stat-num" style="color:var(--green);">{total_searches}</div><div class="stat-lbl">Total Searches</div></div>
        <div class="stat-card"><div class="stat-num" style="color:var(--purple);">{total_clicks}</div><div class="stat-lbl">Product Clicks</div></div>
        <div class="stat-card"><div class="stat-num" style="color:var(--red);">{total_prices}</div><div class="stat-lbl">Price Records</div></div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:28px;">
        <div class="section">
          <div class="section-title">🔍 Top Searched Keywords</div>
          <div class="card" style="padding:0;overflow:hidden;">
            <table><thead><tr><th>Keyword</th><th>Times</th></tr></thead>
            <tbody>{kw_rows or '<tr><td colspan=2 style="color:var(--muted2);text-align:center;padding:20px;">No data yet</td></tr>'}</tbody></table>
          </div>
        </div>
        <div class="section">
          <div class="section-title">📦 Most Tracked Products</div>
          <div class="card" style="padding:0;overflow:hidden;">
            <table><thead><tr><th>Product</th><th>ASIN</th><th>Trackers</th></tr></thead>
            <tbody>{tr_rows or '<tr><td colspan=3 style="color:var(--muted2);text-align:center;padding:20px;">No data yet</td></tr>'}</tbody></table>
          </div>
        </div>
      </div>

      <div class="section">
        <div class="section-title">🕐 Recent Searches</div>
        <div class="card" style="padding:0;overflow:hidden;overflow-x:auto;">
          <table><thead><tr><th>Keyword</th><th>Results</th><th>User</th><th>Time</th></tr></thead>
          <tbody>{sr_rows or '<tr><td colspan=4 style="color:var(--muted2);text-align:center;padding:20px;">No searches yet</td></tr>'}</tbody></table>
        </div>
      </div>

      <div class="section">
        <div class="section-title">👆 Recent Product Clicks</div>
        <div class="card" style="padding:0;overflow:hidden;overflow-x:auto;">
          <table><thead><tr><th>Product</th><th>Price</th><th>User</th><th>Time</th></tr></thead>
          <tbody>{cl_rows or '<tr><td colspan=4 style="color:var(--muted2);text-align:center;padding:20px;">No clicks yet</td></tr>'}</tbody></table>
        </div>
      </div>

      <div class="section">
        <div class="section-title">👤 All Users</div>
        <div class="card" style="padding:0;overflow:hidden;overflow-x:auto;">
          <table><thead><tr><th>#</th><th>Name</th><th>Email</th><th>Joined</th></tr></thead>
          <tbody>{user_rows or '<tr><td colspan=4 style="color:var(--muted2);text-align:center;padding:20px;">No users yet</td></tr>'}</tbody></table>
        </div>
      </div>

      <a href="/admin/run-check" class="btn btn-amber" onclick="return confirm('Run price check now? This may take several minutes.')">⚡ Run Price Check Now</a>
    </div></body></html>"""

@app.route('/admin/run-check')
def admin_run_check():
    if not admin_in(): return redirect('/login')
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT DISTINCT asin FROM tracked")
    asins = c.fetchall(); conn.close()
    checked = 0
    for row in asins:
        try:
            url  = f"https://www.amazon.com/dp/{row['asin']}"
            resp = scrape_session.get(url, headers=req_headers(), timeout=20)
            soup = BeautifulSoup(resp.text, "html.parser")
            price = 0.0
            for sel in ["#priceblock_ourprice","#priceblock_dealprice",".a-price .a-offscreen","#price_inside_buybox"]:
                el = soup.select_one(sel)
                if el:
                    try:
                        price = float(el.get_text(strip=True).replace("$","").replace(",",""))
                        if price > 0: break
                    except: pass
            if price > 0:
                conn = get_db(); c2 = conn.cursor()
                c2.execute("INSERT INTO price_history (asin,price) VALUES (%s,%s)", (row['asin'], price))
                conn.commit(); conn.close()
                checked += 1
            time.sleep(random.uniform(3,6))
        except: pass
    return f"""<!DOCTYPE html><html><head><title>Done</title>{BASE_CSS}</head><body>
    {nav_html('admin')}
    <div class="wrap" style="padding-top:40px;">
      <div class="alert alert-green" style="font-size:1rem;">
        Price check complete! {checked}/{len(asins)} products updated.
      </div>
      <a href="/admin" class="btn btn-amber">← Back to Admin</a>
    </div></body></html>"""

# ══════════════════════════════════════════
#  SEARCH
# ══════════════════════════════════════════
@app.route('/search')
def search():
    keyword = request.args.get('q','').strip()
    if not keyword: return jsonify({'error':'keyword required'}), 400

    pages = int(request.args.get('pages',3) or 3)
    filters = {
        'min_price':   float(request.args.get('min_price',  0) or 0),
        'max_price':   float(request.args.get('max_price',  0) or 0),
        'min_rating':  float(request.args.get('min_rating', 0) or 0),
        'min_reviews': int(  request.args.get('min_reviews',0) or 0),
        'prime_only':  request.args.get('prime_only','0') == '1',
    }

    args_list = [(keyword, p, filters) for p in range(1, pages+1)]
    raw, total_scanned = [], 0

    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(fetch_page, a): a for a in args_list}
        for f in as_completed(futures):
            res, scanned = f.result()
            raw.extend(res); total_scanned += scanned

    total_filtered = len(raw)
    products       = dedup(raw)
    dupes          = total_filtered - len(products)

    try:
        conn = get_db(); c = conn.cursor()
        c.execute("INSERT INTO search_logs (user_id,keyword,results) VALUES (%s,%s,%s)",
                  (flask_session.get('user_id'), keyword, len(products)))
        conn.commit(); conn.close()
    except: pass

    return jsonify({
        'keyword': keyword,
        'total_scanned': total_scanned,
        'total_filtered': total_filtered,
        'duplicates_removed': dupes,
        'total': len(products),
        'products': products,
    })

# ══════════════════════════════════════════
#  MAIN PAGE
# ══════════════════════════════════════════
@app.route('/')
def index():
    is_login = 'true' if logged_in() else 'false'
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Amazon Finder</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
{BASE_CSS}
<style>
body::before{{content:'';position:fixed;inset:0;z-index:0;pointer-events:none;
  background:radial-gradient(ellipse 70% 40% at 5% 0%,rgba(245,158,11,.06) 0%,transparent 55%),
             radial-gradient(ellipse 50% 35% at 95% 100%,rgba(59,130,246,.05) 0%,transparent 50%);}}
.wrap{{max-width:1400px;margin:0 auto;padding:0 18px;position:relative;z-index:1;}}
.live-badge{{display:flex;align-items:center;gap:6px;background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.2);color:var(--green);font-size:.7rem;font-weight:600;padding:5px 12px;border-radius:50px;letter-spacing:.08em;text-transform:uppercase;}}
.live-dot{{width:6px;height:6px;border-radius:50%;background:var(--green);animation:blink 1.4s ease infinite;}}
@keyframes blink{{0%,100%{{opacity:1;}}50%{{opacity:.3;}}}}
.hero{{padding:36px 18px 24px;}}
.hero-tag{{display:inline-flex;align-items:center;gap:7px;background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);color:var(--amber);font-size:.72rem;font-weight:700;letter-spacing:.1em;padding:5px 13px;border-radius:50px;text-transform:uppercase;margin-bottom:16px;}}
.hero h1{{font-family:'Bebas Neue',sans-serif;font-size:clamp(2.4rem,6vw,4.5rem);line-height:1;letter-spacing:1px;margin-bottom:10px;}}
.hero h1 .hl{{background:linear-gradient(90deg,var(--amber),var(--amber2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}}
.hero p{{color:var(--muted2);font-size:.95rem;max-width:500px;}}
.panel{{background:var(--bg2);border:1px solid var(--border);border-radius:16px;padding:22px;margin-bottom:18px;}}
.search-row{{display:flex;gap:10px;margin-bottom:14px;}}
.search-input{{flex:1;background:var(--bg3);border:1.5px solid var(--border2);border-radius:10px;padding:14px 18px;color:var(--text);font-size:1rem;font-family:'DM Sans',sans-serif;outline:none;transition:border-color .2s,box-shadow .2s;}}
.search-input:focus{{border-color:var(--amber);box-shadow:0 0 0 3px rgba(245,158,11,.1);}}
.search-input::placeholder{{color:var(--muted);}}
.btn-search{{background:linear-gradient(135deg,var(--amber),var(--amber2));color:#000;border:none;border-radius:10px;padding:14px 26px;font-size:.95rem;font-weight:700;font-family:'DM Sans',sans-serif;cursor:pointer;white-space:nowrap;transition:opacity .2s;box-shadow:0 4px 18px rgba(245,158,11,.3);}}
.btn-search:hover{{opacity:.9;}}
.btn-search:disabled{{opacity:.35;cursor:not-allowed;}}
.quick-filters{{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:14px;}}
.qf{{background:var(--bg3);border:1.5px solid var(--border2);border-radius:50px;padding:5px 13px;font-size:.73rem;font-weight:600;cursor:pointer;color:var(--muted2);transition:all .18s;}}
.qf:hover,.qf.active{{border-color:var(--amber);color:var(--amber);background:rgba(245,158,11,.08);}}
.filters-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;}}
.fg{{display:flex;flex-direction:column;gap:6px;}}
.fg label{{font-size:.62rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;}}
.fg input,.fg select{{background:var(--bg3);border:1.5px solid var(--border2);border-radius:9px;padding:10px 12px;color:var(--text);font-size:.88rem;font-family:'DM Sans',sans-serif;outline:none;transition:border-color .2s;}}
.fg input:focus,.fg select:focus{{border-color:var(--amber);}}
.toggle-wrap{{display:flex;align-items:center;gap:10px;padding-top:6px;}}
.toggle{{position:relative;width:42px;height:24px;flex-shrink:0;}}
.toggle input{{opacity:0;width:0;height:0;}}
.toggle-slider{{position:absolute;inset:0;background:var(--border2);border-radius:50px;cursor:pointer;transition:.25s;}}
.toggle-slider::before{{content:'';position:absolute;width:18px;height:18px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:.25s;}}
.toggle input:checked+.toggle-slider{{background:var(--amber);}}
.toggle input:checked+.toggle-slider::before{{transform:translateX(18px);}}
.stats-bar{{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:14px 18px;margin-bottom:14px;display:none;}}
.stats-bar.on{{display:block;}}
.stats-inner{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;}}
.stat-chip{{display:flex;align-items:center;gap:8px;background:var(--bg3);border-radius:8px;padding:8px 14px;font-size:.82rem;}}
.stat-chip .n{{font-weight:700;font-size:.95rem;}}
.n-blue{{color:#60A5FA;}}.n-amber{{color:var(--amber2);}}.n-red{{color:#F87171;}}.n-green{{color:#34D399;}}
.arr{{color:var(--muted);}}
.results-hdr{{display:none;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:10px;}}
.results-hdr.on{{display:flex;}}
.res-count{{font-size:.85rem;color:var(--muted2);}}
.res-count strong{{color:var(--amber);font-size:1rem;font-weight:700;}}
.sort-sel{{background:var(--bg2);border:1px solid var(--border2);border-radius:9px;padding:9px 13px;color:var(--text);font-family:'DM Sans',sans-serif;font-size:.83rem;outline:none;cursor:pointer;}}
.status{{margin-bottom:14px;display:none;}}
.status.on{{display:block;}}
.status-pill{{display:inline-flex;align-items:center;gap:9px;background:var(--bg2);border:1px solid var(--border);border-radius:50px;padding:9px 18px;font-size:.82rem;color:var(--muted2);}}
.spin{{width:14px;height:14px;border:2px solid var(--border2);border-top-color:var(--amber);border-radius:50%;animation:spin .6s linear infinite;}}
@keyframes spin{{to{{transform:rotate(360deg);}}}}
.err-box{{background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:10px;padding:13px 16px;color:#FCA5A5;font-size:.88rem;margin-bottom:14px;display:none;}}
.err-box.on{{display:block;}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(235px,1fr));gap:16px;margin-bottom:60px;}}
.pcard{{background:var(--bg2);border:1px solid var(--border);border-radius:16px;overflow:hidden;display:flex;flex-direction:column;transition:transform .22s cubic-bezier(.34,1.56,.64,1),border-color .2s,box-shadow .2s;animation:fadeUp .38s ease both;}}
.pcard:hover{{transform:translateY(-5px);border-color:var(--amber);box-shadow:0 12px 36px rgba(245,158,11,.12);}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(18px);}}to{{opacity:1;transform:translateY(0);}}}}
.pcard-img{{position:relative;background:var(--bg3);height:190px;overflow:hidden;flex-shrink:0;}}
.pcard-img img{{width:100%;height:100%;object-fit:contain;transition:transform .3s;padding:10px;}}
.pcard:hover .pcard-img img{{transform:scale(1.06);}}
.badge-disc{{position:absolute;top:10px;left:10px;background:linear-gradient(135deg,var(--amber),var(--amber2));color:#000;font-size:.62rem;font-weight:800;padding:3px 8px;border-radius:6px;}}
.badge-prime{{position:absolute;top:10px;right:10px;background:rgba(59,130,246,.85);color:#fff;font-size:.62rem;font-weight:700;padding:3px 8px;border-radius:6px;}}
.rating-pill{{position:absolute;bottom:10px;right:10px;background:rgba(0,0,0,.75);color:var(--amber2);font-size:.72rem;font-weight:700;padding:4px 9px;border-radius:6px;}}
.deal-badge-c{{position:absolute;bottom:10px;left:10px;font-size:.62rem;font-weight:800;padding:3px 8px;border-radius:6px;}}
.deal-hot{{background:rgba(239,68,68,.85);color:#fff;}}
.deal-good{{background:rgba(16,185,129,.85);color:#fff;}}
.deal-ok{{background:rgba(107,114,128,.75);color:#fff;}}
.pcard-body{{padding:13px;flex:1;display:flex;flex-direction:column;gap:8px;}}
.pcard-name{{font-size:.82rem;line-height:1.5;color:var(--text);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:2.5em;}}
.price-now{{font-size:1.2rem;font-weight:700;color:var(--amber);}}
.price-na{{font-size:.85rem;color:var(--muted);font-style:italic;}}
.deal-row{{display:flex;align-items:center;gap:8px;}}
.deal-bar-bg{{flex:1;height:5px;background:var(--border2);border-radius:3px;overflow:hidden;}}
.deal-bar-fill{{height:100%;border-radius:3px;}}
.ss-grid{{display:grid;grid-template-columns:1fr 1fr;gap:5px;}}
.ss{{background:var(--bg3);border-radius:7px;padding:6px 9px;}}
.ss-label{{font-size:.57rem;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;font-weight:700;}}
.ss-val{{font-size:.8rem;font-weight:600;}}
.sv-green{{color:#34D399;}}.sv-blue{{color:#60A5FA;}}.sv-red{{color:#F87171;}}
.pcard-foot{{display:flex;gap:5px;margin-top:auto;}}
.btn-view{{flex:1;background:transparent;border:1.5px solid var(--amber);color:var(--amber);border-radius:8px;padding:9px;font-size:.75rem;font-weight:700;text-align:center;transition:background .18s,color .18s;cursor:pointer;display:block;text-decoration:none;}}
.btn-view:hover{{background:var(--amber);color:#000;}}
.icon-btn{{width:36px;height:36px;border:1.5px solid var(--border2);border-radius:8px;background:var(--bg3);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:.85rem;transition:all .18s;flex-shrink:0;}}
.icon-btn:hover{{border-color:var(--amber);}}
.icon-btn.tracked{{border-color:var(--green);background:rgba(16,185,129,.1);}}
.empty{{text-align:center;padding:5rem 1rem;display:none;}}
.empty.on{{display:block;}}
.hist-bar{{display:flex;flex-wrap:wrap;gap:7px;margin-top:10px;}}
.hist-chip{{display:flex;align-items:center;gap:6px;background:var(--bg3);border:1px solid var(--border2);border-radius:50px;padding:4px 12px;font-size:.72rem;color:var(--muted2);cursor:pointer;transition:all .18s;}}
.hist-chip:hover{{border-color:var(--amber);color:var(--amber);}}
.load-more{{text-align:center;margin:10px 0 40px;}}
.btn-load{{background:var(--bg2);border:1.5px solid var(--border2);border-radius:12px;padding:13px 36px;font-size:.9rem;font-weight:600;color:var(--muted2);cursor:pointer;transition:all .2s;}}
.btn-load:hover{{border-color:var(--amber);color:var(--amber);}}
@media(max-width:560px){{
  .search-row{{flex-direction:column;}}
  .grid{{grid-template-columns:repeat(2,1fr);}}
  .pcard-img{{height:145px;}}
  .hero h1{{font-size:2.4rem;}}
}}
</style>
</head><body>
{nav_html()}
<div class="wrap">
<div class="hero">
  <div class="hero-tag">⚡ Smart Search + Price Tracker</div>
  <h1>Find The <span class="hl">Best Deals</span><br>On Amazon</h1>
  <p>Search products, track prices daily, see price history graphs.</p>
</div>
<div class="panel">
  <div class="search-row">
    <input type="text" id="q" class="search-input" placeholder="e.g. iPhone 15, wireless earbuds..." autocomplete="off">
    <button class="btn-search" id="sbtn" onclick="doSearch()">🔍 Search</button>
  </div>
  <div class="quick-filters">
    <span class="qf" onclick="setQ('under25')">Under $25</span>
    <span class="qf" onclick="setQ('under50')">Under $50</span>
    <span class="qf" onclick="setQ('under100')">Under $100</span>
    <span class="qf" onclick="setQ('rating45')">4.5+ Rating</span>
    <span class="qf" onclick="setQ('prime')">Prime Only</span>
    <span class="qf" onclick="setQ('clear')">Clear</span>
  </div>
  <div class="filters-grid">
    <div class="fg"><label>Min Price ($)</label><input type="number" id="minP" placeholder="0" min="0"></div>
    <div class="fg"><label>Max Price ($)</label><input type="number" id="maxP" placeholder="Any" min="0"></div>
    <div class="fg"><label>Min Rating ⭐</label><input type="number" id="minR" placeholder="4.0" step="0.1" min="0" max="5"></div>
    <div class="fg"><label>Min Reviews</label><input type="number" id="minRev" placeholder="50" min="0"></div>
    <div class="fg"><label>Pages to Scan</label><input type="number" id="pages" value="3" min="1"></div>
    <div class="fg">
      <label>Sort By</label>
      <select id="sortSel" onchange="render()">
        <option value="deal">Best Deal Score</option>
        <option value="score">Best Match</option>
        <option value="price_asc">Price: Low → High</option>
        <option value="price_desc">Price: High → Low</option>
        <option value="rating">Highest Rated</option>
        <option value="reviews">Most Reviews</option>
      </select>
    </div>
    <div class="fg">
      <label>Prime Only</label>
      <div class="toggle-wrap">
        <label class="toggle"><input type="checkbox" id="primeOnly"><span class="toggle-slider"></span></label>
        <span style="font-size:.82rem;color:var(--muted2);">Amazon Prime</span>
      </div>
    </div>
  </div>
  <div class="hist-bar" id="histBar"></div>
</div>
<div class="status" id="stat">
  <div class="status-pill"><div class="spin"></div><span>Scanning Amazon...</span></div>
</div>
<div class="stats-bar" id="statsBar">
  <div class="stats-inner">
    <div class="stat-chip">🔍 Scanned: <span class="n n-blue" id="s-scan">0</span></div>
    <span class="arr">→</span>
    <div class="stat-chip">🎯 Passed: <span class="n n-amber" id="s-pass">0</span></div>
    <span class="arr">→</span>
    <div class="stat-chip">🚫 Dupes: <span class="n n-red" id="s-dupe">0</span></div>
    <span class="arr">→</span>
    <div class="stat-chip">🏆 Final: <span class="n n-green" id="s-final">0</span></div>
  </div>
</div>
<div class="results-hdr" id="resHdr">
  <div class="res-count">Showing <strong id="resCount">0</strong> products</div>
  <select class="sort-sel" onchange="document.getElementById('sortSel').value=this.value;render()">
    <option value="deal">Best Deal Score</option>
    <option value="score">Best Match</option>
    <option value="price_asc">Price: Low → High</option>
    <option value="price_desc">Price: High → Low</option>
    <option value="rating">Highest Rated</option>
    <option value="reviews">Most Reviews</option>
  </select>
</div>
<div class="err-box" id="err"></div>
<div class="grid" id="grid"></div>
<div class="load-more" id="loadMore" style="display:none;">
  <button class="btn-load" onclick="loadMore()">Load More ↓</button>
</div>
<div class="empty" id="empty">
  <div style="font-size:3.5rem;margin-bottom:1rem;opacity:.5;">🔍</div>
  <h3 style="font-family:'Bebas Neue',sans-serif;font-size:1.8rem;letter-spacing:1px;margin-bottom:.5rem;">No Products Found</h3>
  <p style="color:var(--muted2);">Try different filters or keywords.</p>
</div>
</div>
<script>
const IS_LOGGED={is_login};
let all=[],displayed=0,PAGE=20,trackedSet=new Set();
const $=id=>document.getElementById(id);

async function loadTracked(){{
  if(!IS_LOGGED) return;
  const r=await fetch('/api/tracked-list');
  const d=await r.json();
  trackedSet=new Set(d.items);
}}
loadTracked();

let hist=JSON.parse(localStorage.getItem('sh')||'[]');
function saveHist(q){{hist=[q,...hist.filter(x=>x!==q)].slice(0,7);localStorage.setItem('sh',JSON.stringify(hist));renderHist();}}
function renderHist(){{
  const b=$('histBar');
  if(!hist.length){{b.innerHTML='';return;}}
  b.innerHTML='<span style="font-size:.65rem;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-right:4px;">Recent:</span>'
    +hist.map((h,i)=>`<span class="hist-chip" onclick="useHist('${{h.replace(/'/g,"\\'")}}')">🕐 ${{h}} <span onclick="event.stopPropagation();delHist(${{i}})" style="margin-left:4px;color:var(--muted);">✕</span></span>`).join('');
}}
function useHist(q){{$('q').value=q;doSearch();}}
function delHist(i){{hist.splice(i,1);localStorage.setItem('sh',JSON.stringify(hist));renderHist();}}
renderHist();

function setQ(t){{
  document.querySelectorAll('.qf').forEach(e=>e.classList.remove('active'));
  if(t==='clear'){{$('minP').value='';$('maxP').value='';$('minR').value='';$('minRev').value='';$('primeOnly').checked=false;return;}}
  event.target.classList.add('active');
  if(t==='under25')$('maxP').value=25;
  else if(t==='under50')$('maxP').value=50;
  else if(t==='under100')$('maxP').value=100;
  else if(t==='rating45')$('minR').value=4.5;
  else if(t==='prime')$('primeOnly').checked=true;
}}

async function trackProduct(asin,btn){{
  if(!IS_LOGGED){{
    if(confirm('Login required to track prices. Go to login?')) window.location.href='/login';
    return;
  }}
  const p=all.find(x=>x.asin===asin);
  if(!p) return;
  if(trackedSet.has(asin)){{window.location.href='/price-graph/'+asin;return;}}
  fetch('/api/log-click',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{asin:p.asin,name:p.name,price:p.price}})}});
  const r=await fetch('/api/track',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{asin:p.asin,name:p.name,image:p.image,url:p.url,price:p.price}})}});
  const d=await r.json();
  if(d.tracked){{trackedSet.add(asin);btn.textContent='📈';btn.classList.add('tracked');btn.title='View Price Graph';}}
}}

function scoreColor(s){{return s>=75?'#10B981':s>=50?'#F59E0B':'#6B7280';}}
function dealClass(s){{return s>=75?'deal-hot':s>=50?'deal-good':'deal-ok';}}
function dealLabel(s){{return s>=75?'🔥 Hot Deal':s>=50?'👍 Good Deal':'• Fair';}}

function cardHTML(p,i){{
  const isT=trackedSet.has(p.asin);
  return `
  <div class="pcard" style="animation-delay:${{Math.min(i*.03,.6)}}s">
    <div class="pcard-img">
      <img src="${{p.image}}" onerror="this.src='https://placehold.co/235x190/15151C/6B6B8A?text=No+Image'" loading="lazy" alt="${{p.name}}">
      ${{p.badge?`<div class="badge-disc">${{p.badge}}</div>`:''}}
      ${{p.prime?'<div class="badge-prime">PRIME</div>':''}}
      <div class="rating-pill">⭐ ${{p.rating.toFixed(1)}}</div>
      <div class="deal-badge-c ${{dealClass(p.deal_score)}}">${{dealLabel(p.deal_score)}}</div>
    </div>
    <div class="pcard-body">
      <div class="pcard-name" title="${{p.name}}">${{p.name}}</div>
      <div>${{p.price>0?`<span class="price-now">$${{p.price.toFixed(2)}}</span>`:'<span class="price-na">Price N/A</span>'}}</div>
      <div class="deal-row">
        <span style="font-size:.68rem;color:var(--muted2);white-space:nowrap;">Deal Score</span>
        <div class="deal-bar-bg"><div class="deal-bar-fill" style="width:${{p.deal_score}}%;background:${{scoreColor(p.deal_score)}}"></div></div>
        <span style="font-size:.7rem;font-weight:700;color:var(--amber2);min-width:26px;text-align:right;">${{p.deal_score}}</span>
      </div>
      <div class="ss-grid">
        <div class="ss"><div class="ss-label">Reviews</div><div class="ss-val sv-green">📝 ${{p.reviews.toLocaleString()}}</div></div>
        <div class="ss"><div class="ss-label">Prime</div><div class="ss-val ${{p.prime?'sv-blue':'sv-red'}}">${{p.prime?'Yes':'No'}}</div></div>
      </div>
      <div class="pcard-foot">
        <a href="${{p.url}}" target="_blank" class="btn-view" onclick="logClick('${{p.asin}}','${{p.name.replace(/'/g,"\\'")}}')">View on Amazon →</a>
        <button class="icon-btn ${{isT?'tracked':''}}" title="${{isT?'View Price Graph':'Track Price'}}"
          onclick="trackProduct('${{p.asin}}',this)">${{isT?'📈':'📌'}}</button>
      </div>
    </div>
  </div>`;
}}

function logClick(asin,name){{
  const p=all.find(x=>x.asin===asin);
  if(p) fetch('/api/log-click',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{asin,name,price:p.price}})}});
}}

function getSorted(){{
  const s=$('sortSel').value;let list=[...all];
  if(s==='deal')list.sort((a,b)=>b.deal_score-a.deal_score);
  else if(s==='price_asc')list.sort((a,b)=>(a.price||9999)-(b.price||9999));
  else if(s==='price_desc')list.sort((a,b)=>(b.price||0)-(a.price||0));
  else if(s==='rating')list.sort((a,b)=>b.rating-a.rating);
  else if(s==='reviews')list.sort((a,b)=>b.reviews-a.reviews);
  else list.sort((a,b)=>(b.rating*Math.min(b.reviews,10000))-(a.rating*Math.min(a.reviews,10000)));
  return list;
}}

function render(){{
  const list=getSorted();
  $('resCount').textContent=list.length.toLocaleString();
  const g=$('grid'),em=$('empty');
  if(!list.length){{g.innerHTML='';em.classList.add('on');$('loadMore').style.display='none';return;}}
  em.classList.remove('on');
  const chunk=list.slice(0,PAGE);displayed=chunk.length;
  g.innerHTML=chunk.map((p,i)=>cardHTML(p,i)).join('');
  $('loadMore').style.display=list.length>PAGE?'block':'none';
}}

function loadMore(){{
  const list=getSorted();
  const chunk=list.slice(displayed,displayed+PAGE);
  chunk.forEach((p,i)=>$('grid').insertAdjacentHTML('beforeend',cardHTML(p,displayed+i)));
  displayed+=chunk.length;
  if(displayed>=list.length)$('loadMore').style.display='none';
}}

async function doSearch(){{
  const q=$('q').value.trim();
  if(!q){{showErr('Please enter a keyword!');return;}}
  const params=new URLSearchParams({{q,pages:$('pages').value||3,
    min_price:$('minP').value||0,max_price:$('maxP').value||0,
    min_rating:$('minR').value||0,min_reviews:$('minRev').value||0,
    prime_only:$('primeOnly').checked?1:0}});
  setLoad(true);hideErr();
  try{{
    const res=await fetch('/search?'+params);
    if(!res.ok) throw new Error();
    const d=await res.json();
    all=d.products||[];displayed=0;
    saveHist(q);
    await loadTracked();
    $('s-scan').textContent=d.total_scanned.toLocaleString();
    $('s-pass').textContent=d.total_filtered.toLocaleString();
    $('s-dupe').textContent=d.duplicates_removed.toLocaleString();
    $('s-final').textContent=d.total.toLocaleString();
    $('statsBar').classList.add('on');
    $('resHdr').classList.add('on');
    render();
  }}catch(e){{showErr('Connection failed.');}}
  finally{{setLoad(false);}}
}}

function setLoad(on){{$('sbtn').disabled=on;$('sbtn').textContent=on?'⏳ Loading...':'🔍 Search';$('stat').className='status'+(on?' on':'');}}
function showErr(m){{const e=$('err');e.textContent=m;e.className='err-box on';}}
function hideErr(){{$('err').className='err-box';}}
$('q').addEventListener('keydown',e=>{{if(e.key==='Enter')doSearch();}});
</script>
</body></html>"""

@app.route('/run-price-check')
def run_price_check():
    secret = request.args.get('secret','')
    if secret != os.environ.get('CRON_SECRET','mysecret123'):
        return 'Unauthorized', 401

    import threading
    def do_check():
        try:
            conn = get_db(); c = conn.cursor()
            c.execute("SELECT DISTINCT asin FROM tracked")
            asins = c.fetchall(); conn.close()
            print(f"Cron: checking {len(asins)} products...")
            for row in asins:
                try:
                    url  = f"https://www.amazon.com/dp/{row['asin']}"
                    resp = scrape_session.get(url, headers=req_headers(), timeout=20)
                    soup = BeautifulSoup(resp.text, "html.parser")
                    price = 0.0
                    for sel in [".a-price .a-offscreen","#priceblock_ourprice",
                                "#priceblock_dealprice","#price_inside_buybox",
                                "#newBuyBoxPrice"]:
                        el = soup.select_one(sel)
                        if el:
                            try:
                                price = float(el.get_text(strip=True).replace("$","").replace(",",""))
                                if price > 0: break
                            except: pass
                    if price > 0:
                        conn2 = get_db(); c2 = conn2.cursor()
                        c2.execute("INSERT INTO price_history (asin,price) VALUES (%s,%s)",
                                   (row['asin'], price))
                        conn2.commit(); conn2.close()
                        print(f"  {row['asin']} → ${price:.2f}")
                    time.sleep(random.uniform(3, 7))
                except Exception as e:
                    print(f"  {row['asin']} error: {e}")
        except Exception as e:
            print(f"Cron error: {e}")

    threading.Thread(target=do_check, daemon=True).start()
    return jsonify({'ok': True, 'message': 'Price check started in background'})

@app.route('/ping')
def ping():
    return 'pong', 200

if __name__ == '__main__':
    print("="*50)
    print("  Amazon Finder — Supabase Edition")
    print("="*50)
    print("  http://127.0.0.1:5000")
    print(f"  Admin login: {ADMIN_USER} / {ADMIN_PASS}")
    print("="*50)
    app.run(debug=False, host='0.0.0.0', port=5000)
