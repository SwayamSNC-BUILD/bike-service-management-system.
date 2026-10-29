
from flask import Flask, request, redirect, url_for, render_template_string, flash
import sqlite3
import os
from datetime import date, datetime

app = Flask(__name__)
app.secret_key = "bikecare-college-project"

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bike_service.db")


# ================= DATABASE =================

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def setup_db():
    con = db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer TEXT NOT NULL,
            phone TEXT NOT NULL,
            bike TEXT NOT NULL,
            reg_no TEXT NOT NULL,
            service TEXT NOT NULL,
            booking_date TEXT NOT NULL,
            cost REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Booked'
        )
    """)

    # Add new columns without destroying existing records.
    columns = {r["name"] for r in con.execute(
        "PRAGMA table_info(bookings)"
    ).fetchall()}

    additions = {
        "coupon_code": "TEXT DEFAULT ''",
        "discount": "REAL NOT NULL DEFAULT 0",
        "amount_paid": "REAL NOT NULL DEFAULT 0",
        "payment_status": "TEXT NOT NULL DEFAULT 'Unpaid'",
        "notes": "TEXT DEFAULT ''",
        "updated_at": "TEXT DEFAULT ''"
    }

    for name, definition in additions.items():
        if name not in columns:
            con.execute(f"ALTER TABLE bookings ADD COLUMN {name} {definition}")

    con.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT '',
            price REAL NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            discount_type TEXT NOT NULL DEFAULT 'percent',
            discount_value REAL NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            valid_until TEXT DEFAULT '2027-12-31'
        )
    """)

    services = [
        ("General Service", "Basic inspection and servicing", 600),
        ("Engine Oil Change", "Engine oil replacement", 450),
        ("Full Service", "Complete bike service", 1500),
        ("Brake Service", "Brake inspection and adjustment", 350),
        ("Chain Cleaning", "Chain cleaning and lubrication", 200),
        ("Water Wash", "Bike wash and cleaning", 150),
    ]
    for name, description, price in services:
        con.execute(
            "INSERT OR IGNORE INTO services(name,description,price) VALUES(?,?,?)",
            (name, description, price)
        )

    offers = [
        ("WELCOME10", "Welcome Offer", "10% off your service", "percent", 10),
        ("FESTIVE15", "Festive Offer", "15% off your service", "percent", 15),
        ("SAVE200", "Flat Discount", "Save ₹200 on your service", "flat", 200),
    ]
    for code, title, description, kind, value in offers:
        con.execute("""
            INSERT OR IGNORE INTO offers
            (code,title,description,discount_type,discount_value,valid_until)
            VALUES(?,?,?,?,?,'2027-12-31')
        """, (code, title, description, kind, value))

    con.commit()
    con.close()


def money(value):
    return f"₹{float(value or 0):,.2f}"


app.jinja_env.filters["money"] = money


def services_list(active_only=True):
    con = db()
    sql = "SELECT * FROM services"
    if active_only:
        sql += " WHERE active=1"
    result = con.execute(sql + " ORDER BY name").fetchall()
    con.close()
    return result


def booking_record(bid):
    con = db()
    result = con.execute(
        "SELECT * FROM bookings WHERE id=?", (bid,)
    ).fetchone()
    con.close()
    return result


def active_offer(code):
    if not code:
        return None
    con = db()
    result = con.execute("""
        SELECT * FROM offers
        WHERE UPPER(code)=UPPER(?) AND active=1
        AND (valid_until='' OR valid_until>=?)
    """, (code.strip(), date.today().isoformat())).fetchone()
    con.close()
    return result


def calc_discount(cost, code):
    offer = active_offer(code)
    if not offer:
        return 0.0
    if offer["discount_type"] == "percent":
        amount = cost * offer["discount_value"] / 100
    else:
        amount = offer["discount_value"]
    return round(min(max(amount, 0), max(cost, 0)), 2)


def pay_status(total, paid):
    if paid <= 0:
        return "Unpaid"
    if paid >= total:
        return "Paid"
    return "Partial"


def save_booking(form, bid=None):
    customer = form.get("customer", "").strip()
    phone = form.get("phone", "").strip()
    bike = form.get("bike", "").strip()
    reg_no = form.get("reg_no", "").strip().upper()
    service = form.get("service", "").strip()
    booking_date = form.get("booking_date", "").strip()
    coupon = form.get("coupon_code", "").strip().upper()
    notes = form.get("notes", "").strip()

    if not all([customer, phone, bike, reg_no, service, booking_date]):
        raise ValueError("Please fill in all required fields.")

    con = db()
    selected = con.execute(
        "SELECT * FROM services WHERE name=? AND active=1", (service,)
    ).fetchone()

    # When editing an old booking, allow its historical service name.
    if selected:
        cost = float(selected["price"])
    elif bid:
        old = con.execute(
            "SELECT service,cost FROM bookings WHERE id=?", (bid,)
        ).fetchone()
        if not old or old["service"] != service:
            con.close()
            raise ValueError("Please select an active service.")
        cost = float(old["cost"])
    else:
        con.close()
        raise ValueError("Please select an active service.")

    con.close()

    if coupon and not active_offer(coupon):
        raise ValueError("Coupon is invalid, inactive, or expired.")

    discount = calc_discount(cost, coupon)
    total = max(0, round(cost - discount, 2))

    try:
        paid = float(form.get("amount_paid", 0) or 0)
        if paid < 0 or paid > total:
            raise ValueError
    except ValueError:
        raise ValueError("Amount paid must be between ₹0 and the final total.")

    pstatus = pay_status(total, paid)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    con = db()
    if bid:
        con.execute("""
            UPDATE bookings SET customer=?,phone=?,bike=?,reg_no=?,service=?,
            booking_date=?,cost=?,coupon_code=?,discount=?,amount_paid=?,
            payment_status=?,notes=?,updated_at=? WHERE id=?
        """, (customer, phone, bike, reg_no, service, booking_date, cost,
              coupon, discount, paid, pstatus, notes, now, bid))
    else:
        con.execute("""
            INSERT INTO bookings
            (customer,phone,bike,reg_no,service,booking_date,cost,coupon_code,
             discount,amount_paid,payment_status,notes,updated_at,status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'Booked')
        """, (customer, phone, bike, reg_no, service, booking_date, cost,
              coupon, discount, paid, pstatus, notes, now))
    con.commit()
    con.close()


# ================= DESIGN =================

BASE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} | BikeCare</title>
<style>
:root{--purple:#6759e8;--pink:#ed6ba5;--green:#20b98b;--orange:#f5a447;
--ink:#242541;--muted:#7b809a;--bg:#f5f6fc}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);font:15px Arial,sans-serif;color:var(--ink)}
a{text-decoration:none;color:inherit}
.layout{display:flex;min-height:100vh}
.sidebar{width:225px;flex-shrink:0;background:#fff;padding:22px 14px;
box-shadow:3px 0 20px #28265a0a}
.logo{font-size:25px;font-weight:900;color:var(--purple);padding:10px 12px 25px}
.logo span{color:var(--pink)}
.nav{display:block;padding:13px 12px;border-radius:11px;margin:5px 0;color:#777b96}
.nav:hover{background:#eeeaff;color:var(--purple)}
.nav small{display:block;font-size:10px;margin-top:4px}
.main{padding:27px;width:100%;min-width:0;max-width:1600px;margin:auto}
.top{display:flex;justify-content:space-between;align-items:center;gap:12px;
flex-wrap:wrap;margin-bottom:24px}
h1{font-size:27px;margin:0 0 7px}h2{font-size:19px;margin:0 0 16px}
.muted{color:var(--muted);font-size:13px}
.btn{display:inline-block;border:0;border-radius:9px;padding:10px 13px;
background:var(--purple);color:white;font-weight:bold;cursor:pointer;font-size:12px}
.btn.green{background:var(--green)}.btn.pink{background:var(--pink)}
.btn.orange{background:var(--orange)}.btn.gray{background:#7c8198}
.btn.red{background:#d94f68}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:15px;margin-bottom:18px}
.stat,.panel,.card{border-radius:16px;background:white;padding:20px;
box-shadow:0 5px 24px #25234b08;margin-bottom:17px}
.stat{color:white;min-height:130px}
.stat:nth-child(4n+1){background:linear-gradient(135deg,#7565ee,#a18bff)}
.stat:nth-child(4n+2){background:linear-gradient(135deg,#ff8eaa,#f5b3cc)}
.stat:nth-child(4n+3){background:linear-gradient(135deg,#27b99a,#79d9c0)}
.stat:nth-child(4n+4){background:linear-gradient(135deg,#f4a44b,#ffd18a)}
.stat strong{display:block;font-size:25px;margin:15px 0 7px}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;white-space:nowrap}
th,td{text-align:left;padding:12px 10px;border-bottom:1px solid #eff0f7;font-size:13px}
th{color:var(--muted);font-size:10px;text-transform:uppercase}
.badge{display:inline-block;padding:6px 9px;border-radius:20px;background:#eeeaff;
color:#6253d9;font-size:11px;font-weight:bold}
form .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.field label{display:block;font-size:12px;font-weight:bold;margin-bottom:7px;color:#666b86}
input,select,textarea{width:100%;padding:12px;border:1px solid #e1e3ef;
border-radius:9px;font:14px Arial;background:white;color:var(--ink)}
textarea{min-height:75px;resize:vertical}
.full{grid-column:1/-1}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:17px}
.flash{background:#e0f8ed;color:#187c60;padding:13px;border-radius:10px;margin-bottom:14px}
.flash.error{background:#ffe5e9;color:#b92d49}
.menu{position:relative;display:inline-block}
.dots{border:0;background:#f0efff;border-radius:8px;padding:5px 11px;font-size:20px;cursor:pointer}
.menu-list{display:none;position:absolute;right:0;top:34px;background:#fff;min-width:165px;
z-index:20;box-shadow:0 6px 25px #24204420;border-radius:11px;padding:7px}
.menu-list.show{display:block}
.menu-list a,.menu-list button{display:block;padding:10px;width:100%;text-align:left;
border:0;background:white;border-radius:7px;font-size:12px;cursor:pointer;color:var(--ink)}
.menu-list a:hover,.menu-list button:hover{background:#f0efff}
.search{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:15px}
.search input,.search select{max-width:250px}
.big-number{font-size:28px;font-weight:900;color:var(--purple)}
@media(max-width:900px){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.sidebar{width:185px}}
@media(max-width:620px){.layout{display:block}.sidebar{width:100%;padding:10px}
.logo{padding:6px 8px}.nav{display:inline-block;padding:9px;font-size:12px}
.nav small{display:none}.main{padding:14px}.cards{grid-template-columns:1fr 1fr}
form .grid{grid-template-columns:1fr}.full{grid-column:auto}h1{font-size:22px}}
@media print{.sidebar,.top,.no-print,.flash{display:none!important}
body{background:white}.main{padding:0}.panel,.card{box-shadow:none;border:1px solid #ddd}}
</style>
</head>
<body><div class="layout">
<aside class="sidebar">
<div class="logo">Bike<span>Care</span> 🏍️</div>
<a class="nav" href="{{ url_for('dashboard') }}">🏠 Dashboard<small>Overview and analytics</small></a>
<a class="nav" href="{{ url_for('bookings') }}">📋 Bookings<small>Manage service jobs</small></a>
<a class="nav" href="{{ url_for('new_booking') }}">➕ New Booking<small>Register a service</small></a>
<a class="nav" href="{{ url_for('customers') }}">👥 Customers<small>Customer history</small></a>
<a class="nav" href="{{ url_for('services') }}">🔧 Services & Prices<small>Manage service menu</small></a>
<a class="nav" href="{{ url_for('offers') }}">🎟️ Offers & Coupons<small>Manage discounts</small></a>
<a class="nav" href="{{ url_for('reports') }}">📈 Reports<small>Revenue and payments</small></a>
</aside>
<main class="main">
<div class="top"><div><h1>{{ title }}</h1><div class="muted">BikeCare Service Management System</div></div>
<a class="btn" href="{{ url_for('new_booking') }}">＋ New Booking</a></div>
{% with messages=get_flashed_messages(with_categories=true) %}
{% for category,message in messages %}
<div class="flash {{ 'error' if category=='error' else '' }}">{{ message }}</div>
{% endfor %}{% endwith %}
{{ content|safe }}
</main></div>
<script>
document.addEventListener('click',function(e){
 document.querySelectorAll('.menu-list').forEach(function(m){
  if(e.target.closest('.dots') && e.target.closest('.menu')===m.parentElement){
   m.classList.toggle('show');
  }else if(!e.target.closest('.menu-list')){m.classList.remove('show');}
 });
});
</script></body></html>
"""


def page(title, template, **context):
    rendered = render_template_string(template, **context)
    return render_template_string(BASE, title=title, content=rendered)


# ================= DASHBOARD =================

@app.route("/")
def dashboard():
    con = db()
    total = con.execute("SELECT COUNT(*) n FROM bookings").fetchone()["n"]
    today_count = con.execute(
        "SELECT COUNT(*) n FROM bookings WHERE booking_date=?",
        (date.today().isoformat(),)
    ).fetchone()["n"]
    completed = con.execute(
        "SELECT COUNT(*) n FROM bookings WHERE status='Completed'"
    ).fetchone()["n"]
    revenue = con.execute(
        "SELECT COALESCE(SUM(cost-discount),0) n FROM bookings"
    ).fetchone()["n"]
    paid = con.execute(
        "SELECT COALESCE(SUM(amount_paid),0) n FROM bookings"
    ).fetchone()["n"]
    recent = con.execute(
        "SELECT * FROM bookings ORDER BY id DESC LIMIT 7"
    ).fetchall()
    con.close()

    content = """
    <div class="cards">
    <div class="stat">Total Bookings<strong>{{ total }}</strong>All records</div>
    <div class="stat">Today's Bookings<strong>{{ today_count }}</strong>Scheduled today</div>
    <div class="stat">Completed<strong>{{ completed }}</strong>Jobs finished</div>
    <div class="stat">Service Value<strong>{{ revenue|money }}</strong>After discounts</div>
    </div>
    <div class="cards">
    <div class="card"><div class="muted">Collected payments</div><div class="big-number">{{ paid|money }}</div></div>
    <div class="card"><div class="muted">Outstanding balance</div><div class="big-number">{{ [revenue-paid,0]|max|money }}</div></div>
    <div class="card"><div class="muted">Quick action</div><p>Register a service booking.</p><a class="btn" href="{{ url_for('new_booking') }}">New Booking</a></div>
    <div class="card"><div class="muted">Workshop menu</div><p>Manage prices and discounts.</p><a class="btn pink" href="{{ url_for('services') }}">Manage Services</a></div>
    </div>
    <div class="panel"><h2>Recent Bookings</h2><div class="table-wrap"><table>
    <tr><th>Booking ID</th><th>Customer</th><th>Bike</th><th>Service</th><th>Date</th><th>Status</th><th>Details</th></tr>
    {% for b in recent %}
    <tr><td>#{{ '%05d'|format(b.id) }}</td><td>{{ b.customer }}<br><span class="muted">{{ b.phone }}</span></td>
    <td>{{ b.bike }}<br><span class="muted">{{ b.reg_no }}</span></td><td>{{ b.service }}</td><td>{{ b.booking_date }}</td>
    <td><span class="badge">{{ b.status }}</span></td>
    <td><a class="btn" href="{{ url_for('booking_detail',bid=b.id) }}">View Details</a></td></tr>
    {% else %}<tr><td colspan="7">No bookings yet. Click New Booking to begin.</td></tr>{% endfor %}
    </table></div></div>
    """
    return page("Dashboard", content, total=total, today_count=today_count,
                completed=completed, revenue=revenue, paid=paid, recent=recent)


# ================= BOOKINGS =================

@app.route("/bookings")
def bookings():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    con = db()
    sql = "SELECT * FROM bookings WHERE 1=1"
    args = []
    if q:
        sql += """ AND (customer LIKE ? OR phone LIKE ? OR bike LIKE ?
                   OR reg_no LIKE ? OR service LIKE ?)"""
        args.extend([f"%{q}%"] * 5)
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY id DESC"
    rows = con.execute(sql, args).fetchall()
    con.close()

    content = """
    <div class="panel">
    <form class="search" method="get">
    <input name="q" value="{{ q }}" placeholder="Search customer, phone, bike...">
    <select name="status"><option value="">All statuses</option>
    {% for s in ['Booked','In Progress','Completed','Cancelled'] %}
    <option value="{{ s }}" {{ 'selected' if status==s else '' }}>{{ s }}</option>
    {% endfor %}</select>
    <button class="btn">Search</button><a class="btn gray" href="{{ url_for('bookings') }}">Reset Filters</a>
    </form>
    <div class="table-wrap"><table><tr><th>ID</th><th>Customer</th><th>Bike / Reg No.</th><th>Service</th><th>Date</th><th>Total</th><th>Status</th><th>Payment</th><th>Actions</th></tr>
    {% for b in rows %}<tr>
    <td>#{{ '%05d'|format(b.id) }}</td><td>{{ b.customer }}<br><span class="muted">{{ b.phone }}</span></td>
    <td>{{ b.bike }}<br><span class="muted">{{ b.reg_no }}</span></td><td>{{ b.service }}</td><td>{{ b.booking_date }}</td>
    <td>{{ (b.cost-b.discount)|money }}</td><td><span class="badge">{{ b.status }}</span></td>
    <td><span class="badge">{{ b.payment_status }}</span></td><td>
    <div class="menu"><button type="button" class="dots">⋮</button><div class="menu-list">
    <a href="{{ url_for('booking_detail',bid=b.id) }}">View Details</a>
    <a href="{{ url_for('edit_booking',bid=b.id) }}">Edit Booking</a>
    <a href="{{ url_for('payment',bid=b.id) }}">Update Payment</a>
    <a href="{{ url_for('bill',bid=b.id) }}">Print Invoice</a>
    <a href="{{ url_for('change_status',bid=b.id,status='Completed') }}">Mark Completed</a>
    <form method="post" action="{{ url_for('delete_booking',bid=b.id) }}" onsubmit="return confirm('Delete this booking?')"><button style="color:#d94f68">Delete Booking</button></form>
    </div></div></td></tr>
    {% else %}<tr><td colspan="9">No matching bookings.</td></tr>{% endfor %}
    </table></div></div>

    <div class="panel no-print"><h2>Start Fresh</h2>
    <p class="muted">This permanently deletes all booking records, but keeps services and coupons. Make a backup first.</p>
    <form method="post" action="{{ url_for('reset_bookings') }}"
    onsubmit="return confirm('Delete ALL bookings and payment records? This cannot be undone.')">
    <button class="btn red">Reset All Bookings</button></form></div>
    """
    return page("Bookings", content, rows=rows, q=q, status=status)


@app.route("/bookings/reset", methods=["POST"])
def reset_bookings():
    if request.form.get("confirm_reset") != "YES":
        # Require an explicit confirmation field as an additional safeguard.
        flash("Reset not completed. Use the confirmation form.", "error")
        return redirect(url_for("bookings"))
    con = db()
    con.execute("DELETE FROM bookings")
    con.commit()
    con.close()
    flash("All booking records have been cleared.")
    return redirect(url_for("bookings"))


BOOKING_FORM = """
<div class="panel"><form method="post"><div class="grid">
<div class="field"><label>Customer name *</label><input name="customer" required value="{{ b.customer if b else '' }}"></div>
<div class="field"><label>Phone number *</label><input name="phone" required value="{{ b.phone if b else '' }}"></div>
<div class="field"><label>Bike model *</label><input name="bike" required placeholder="e.g. Honda SP 125" value="{{ b.bike if b else '' }}"></div>
<div class="field"><label>Registration number *</label><input name="reg_no" required placeholder="MH 43 AB 1234" value="{{ b.reg_no if b else '' }}"></div>
<div class="field"><label>Service *</label><select name="service" id="service" required>
{% for s in services %}<option value="{{ s.name }}" data-price="{{ s.price }}" {{ 'selected' if b and b.service==s.name else '' }}>{{ s.name }} — {{ s.price|money }}</option>{% endfor %}
{% if b and b.service not in services|map(attribute='name')|list %}<option value="{{ b.service }}" selected>{{ b.service }} (old booking)</option>{% endif %}
</select></div>
<div class="field"><label>Booking date *</label><input type="date" name="booking_date" required value="{{ b.booking_date if b else today }}"></div>
<div class="field"><label>Coupon code</label><input name="coupon_code" value="{{ b.coupon_code if b else '' }}" placeholder="WELCOME10"></div>
<div class="field"><label>Total amount paid (₹)</label><input type="number" min="0" step="0.01" name="amount_paid" value="{{ b.amount_paid if b else 0 }}"></div>
<div class="field full"><label>Notes</label><textarea name="notes">{{ b.notes if b else '' }}</textarea></div>
</div><div class="card" style="background:#f2f0ff;margin-top:15px">
<div class="muted">Selected service price before discount</div><div class="big-number" id="price">₹0.00</div>
<div class="muted">Coupon discount is applied when saved. Use an active coupon code.</div></div>
<div class="actions"><button class="btn green">Save Booking</button><a class="btn gray" href="{{ url_for('bookings') }}">Cancel</a></div>
</form></div>
<script>
function showPrice(){
 const s=document.getElementById('service');
 const o=s.options[s.selectedIndex];
 document.getElementById('price').textContent='₹'+Number(o.dataset.price||0).toFixed(2);
}
document.getElementById('service').addEventListener('change',showPrice);showPrice();
</script>
"""


@app.route("/book/new", methods=["GET", "POST"])
def new_booking():
    if request.method == "POST":
        try:
            save_booking(request.form)
            flash("Booking saved successfully.")
            return redirect(url_for("bookings"))
        except ValueError as e:
            flash(str(e), "error")
    return page("New Booking", BOOKING_FORM, b=None,
                services=services_list(), today=date.today().isoformat())


@app.route("/book", methods=["GET", "POST"])
def old_book():
    if request.method == "POST":
        try:
            save_booking(request.form)
            flash("Booking saved successfully.")
            return redirect(url_for("bookings"))
        except ValueError as e:
            flash(str(e), "error")
    return redirect(url_for("new_booking"))


@app.route("/book/<int:bid>/edit", methods=["GET", "POST"])
def edit_booking(bid):
    b = booking_record(bid)
    if not b:
        flash("Booking not found.", "error")
        return redirect(url_for("bookings"))
    if request.method == "POST":
        try:
            save_booking(request.form, bid)
            flash("Booking updated.")
            return redirect(url_for("booking_detail", bid=bid))
        except ValueError as e:
            flash(str(e), "error")
    return page("Edit Booking", BOOKING_FORM, b=b,
                services=services_list(), today=date.today().isoformat())


@app.route("/book/<int:bid>")
def booking_detail(bid):
    b = booking_record(bid)
    if not b:
        flash("Booking not found.", "error")
        return redirect(url_for("bookings"))
    content = """
    <div class="panel"><div style="display:flex;justify-content:space-between;gap:15px;flex-wrap:wrap">
    <div><h2>{{ b.customer }}</h2><div class="muted">{{ b.phone }}</div><p>{{ b.bike }} · {{ b.reg_no }}</p></div>
    <div><div class="muted">Booking ID</div><div class="big-number">#{{ '%05d'|format(b.id) }}</div></div></div>
    <div class="cards">
    <div class="card"><div class="muted">Service</div><b>{{ b.service }}</b></div>
    <div class="card"><div class="muted">Date</div><b>{{ b.booking_date }}</b></div>
    <div class="card"><div class="muted">Status</div><span class="badge">{{ b.status }}</span></div>
    <div class="card"><div class="muted">Payment</div><span class="badge">{{ b.payment_status }}</span></div>
    </div>
    <table><tr><td>Service price</td><td>{{ b.cost|money }}</td></tr>
    <tr><td>Coupon {{ b.coupon_code or '' }}</td><td>- {{ b.discount|money }}</td></tr>
    <tr><th>Total</th><th>{{ (b.cost-b.discount)|money }}</th></tr>
    <tr><td>Paid</td><td>{{ b.amount_paid|money }}</td></tr>
    <tr><td>Balance due</td><td>{{ [b.cost-b.discount-b.amount_paid,0]|max|money }}</td></tr></table>
    <p><b>Notes:</b> {{ b.notes or 'No notes' }}</p>
    <div class="actions no-print"><a class="btn" href="{{ url_for('edit_booking',bid=b.id) }}">Edit</a>
    <a class="btn green" href="{{ url_for('payment',bid=b.id) }}">Update Payment</a>
    <a class="btn pink" href="{{ url_for('bill',bid=b.id) }}">Print Invoice</a>
    <a class="btn gray" href="{{ url_for('bookings') }}">Back to Bookings</a></div></div>
    """
    return page("Booking Details", content, b=b)


@app.route("/book/<int:bid>/status/<status>")
def change_status(bid, status):
    if status not in ["Booked", "In Progress", "Completed", "Cancelled"]:
        flash("Invalid status.", "error")
    else:
        con = db()
        con.execute("UPDATE bookings SET status=?,updated_at=? WHERE id=?",
                    (status, datetime.now().strftime("%Y-%m-%d %H:%M"), bid))
        con.commit()
        con.close()
        flash("Booking status updated.")
    return redirect(request.referrer or url_for("bookings"))


@app.route("/book/<int:bid>/delete", methods=["POST"])
def delete_booking(bid):
    con = db()
    con.execute("DELETE FROM bookings WHERE id=?", (bid,))
    con.commit()
    con.close()
    flash("Booking deleted.")
    return redirect(url_for("bookings"))


# ================= PAYMENTS & INVOICES =================

@app.route("/book/<int:bid>/payment", methods=["GET", "POST"])
def payment(bid):
    b = booking_record(bid)
    if not b:
        flash("Booking not found.", "error")
        return redirect(url_for("bookings"))
    total = max(0, b["cost"] - b["discount"])
    if request.method == "POST":
        try:
            paid = float(request.form.get("amount_paid", 0))
            if paid < 0 or paid > total:
                raise ValueError
        except ValueError:
            flash("Enter a valid payment up to the total amount.", "error")
            return redirect(url_for("payment", bid=bid))
        con = db()
        con.execute("""
            UPDATE bookings SET amount_paid=?,payment_status=?,updated_at=?
            WHERE id=?
        """, (paid, pay_status(total, paid),
              datetime.now().strftime("%Y-%m-%d %H:%M"), bid))
        con.commit()
        con.close()
        flash("Payment updated.")
        return redirect(url_for("booking_detail", bid=bid))
    content = """
    <div class="panel"><h2>Payment · Invoice #{{ '%05d'|format(b.id) }}</h2>
    <p>Customer: <b>{{ b.customer }}</b></p><p>Total: <b>{{ total|money }}</b></p>
    <p>Outstanding: <b>{{ [total-b.amount_paid,0]|max|money }}</b></p>
    <form method="post"><div class="field"><label>Total amount paid so far (₹)</label>
    <input type="number" name="amount_paid" min="0" max="{{ total }}" step="0.01" required value="{{ b.amount_paid }}"></div>
    <div class="actions"><button class="btn green">Save Payment</button><a class="btn gray" href="{{ url_for('booking_detail',bid=b.id) }}">Cancel</a></div></form></div>
    """
    return page("Update Payment", content, b=b, total=total)


@app.route("/bill/<int:bid>")
def bill(bid):
    b = booking_record(bid)
    if not b:
        flash("Invoice not found.", "error")
        return redirect(url_for("bookings"))
    content = """
    <div class="panel" style="max-width:800px;margin:auto">
    <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:15px">
    <div><div class="logo">Bike<span>Care</span> 🏍️</div><p class="muted">Bike Service & Maintenance</p></div>
    <div><h2>INVOICE</h2><b>#{{ '%05d'|format(b.id) }}</b><p>Date: {{ b.booking_date }}</p></div></div>
    <hr><h2>Bill To</h2><p><b>{{ b.customer }}</b><br>{{ b.phone }}<br>{{ b.bike }} · {{ b.reg_no }}</p>
    <table><tr><th>Description</th><th>Amount</th></tr>
    <tr><td>{{ b.service }}</td><td>{{ b.cost|money }}</td></tr>
    <tr><td>Discount {{ b.coupon_code or '' }}</td><td>- {{ b.discount|money }}</td></tr>
    <tr><th>Total</th><th>{{ (b.cost-b.discount)|money }}</th></tr>
    <tr><td>Amount paid</td><td>{{ b.amount_paid|money }}</td></tr>
    <tr><th>Balance due</th><th>{{ [b.cost-b.discount-b.amount_paid,0]|max|money }}</th></tr>
    </table><p>Payment status: <b>{{ b.payment_status }}</b></p>
    <p>Notes: {{ b.notes or '—' }}</p><p class="muted">Thank you for choosing BikeCare!</p>
    <div class="actions no-print"><button class="btn" onclick="window.print()">Print / Save as PDF</button>
    <a class="btn gray" href="{{ url_for('booking_detail',bid=b.id) }}">Back</a></div></div>
    """
    return page("Invoice", content, b=b)


# ================= CUSTOMERS =================

@app.route("/customers")
def customers():
    q = request.args.get("q", "").strip()
    con = db()
    rows = con.execute("""
        SELECT customer,phone,COUNT(*) visits,MAX(booking_date) last_visit,
        SUM(cost-discount) total_value,SUM(amount_paid) total_paid
        FROM bookings WHERE customer LIKE ? OR phone LIKE ?
        GROUP BY customer,phone ORDER BY last_visit DESC
    """, (f"%{q}%", f"%{q}%")).fetchall()
    con.close()
    content = """
    <div class="panel"><form class="search"><input name="q" value="{{ q }}" placeholder="Search customer or phone">
    <button class="btn">Search</button></form><div class="table-wrap"><table>
    <tr><th>Customer</th><th>Phone</th><th>Visits</th><th>Last Visit</th><th>Service Value</th><th>Paid</th><th>History</th></tr>
    {% for r in rows %}<tr><td>{{ r.customer }}</td><td>{{ r.phone }}</td><td>{{ r.visits }}</td>
    <td>{{ r.last_visit }}</td><td>{{ (r.total_value or 0)|money }}</td><td>{{ (r.total_paid or 0)|money }}</td>
    <td><a class="btn" href="{{ url_for('bookings',q=r.phone) }}">View Bookings</a></td></tr>
    {% else %}<tr><td colspan="7">No customer records.</td></tr>{% endfor %}</table></div></div>
    """
    return page("Customers", content, rows=rows, q=q)


# ================= SERVICES & PRICES =================

@app.route("/services", methods=["GET", "POST"])
def services():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        try:
            price = float(request.form.get("price", 0))
            if not name or price < 0:
                raise ValueError
            con = db()
            con.execute("INSERT INTO services(name,description,price) VALUES(?,?,?)",
                        (name, description, price))
            con.commit()
            con.close()
            flash("Service added.")
        except sqlite3.IntegrityError:
            flash("A service with this name already exists.", "error")
        except ValueError:
            flash("Enter a service name and a valid price.", "error")
        return redirect(url_for("services"))

    con = db()
    rows = con.execute("SELECT * FROM services ORDER BY active DESC,name").fetchall()
    con.close()
    content = """
    <div class="panel"><h2>Add a service</h2><form method="post"><div class="grid">
    <div class="field"><label>Service name</label><input name="name" required></div>
    <div class="field"><label>Price (₹)</label><input type="number" name="price" min="0" step="0.01" required></div>
    <div class="field full"><label>Description</label><input name="description"></div></div>
    <div class="actions"><button class="btn green">Add Service</button></div></form></div>
    <div class="panel"><h2>Service menu</h2><div class="table-wrap"><table>
    <tr><th>Service</th><th>Description</th><th>Price</th><th>Status</th><th>Actions</th></tr>
    {% for s in rows %}<tr><td>{{ s.name }}</td><td>{{ s.description }}</td><td>{{ s.price|money }}</td>
    <td>{{ 'Active' if s.active else 'Inactive' }}</td><td>
    <form method="post" action="{{ url_for('toggle_service',sid=s.id) }}" style="display:inline">
    <button class="btn gray">{{ 'Deactivate' if s.active else 'Activate' }}</button></form>
    <form method="post" action="{{ url_for('delete_service',sid=s.id) }}" style="display:inline"
    onsubmit="return confirm('Remove this service?')">
    <button class="btn red">Remove</button></form>
    </td></tr>{% else %}<tr><td colspan="5">No services yet.</td></tr>{% endfor %}
    </table></div><p class="muted">Services used in old bookings are deactivated instead of deleted.</p></div>
    """
    return page("Services & Prices", content, rows=rows)


@app.route("/services/<int:sid>/toggle", methods=["POST"])
def toggle_service(sid):
    con = db()
    con.execute("UPDATE services SET active=CASE WHEN active=1 THEN 0 ELSE 1 END WHERE id=?", (sid,))
    con.commit()
    con.close()
    flash("Service status changed.")
    return redirect(url_for("services"))


@app.route("/services/<int:sid>/delete", methods=["POST"])
def delete_service(sid):
    con = db()
    service = con.execute("SELECT name FROM services WHERE id=?", (sid,)).fetchone()
    if not service:
        con.close()
        flash("Service not found.", "error")
        return redirect(url_for("services"))

    used = con.execute(
        "SELECT COUNT(*) n FROM bookings WHERE service=?", (service["name"],)
    ).fetchone()["n"]

    if used:
        con.execute("UPDATE services SET active=0 WHERE id=?", (sid,))
        flash("This service is used in bookings, so it was deactivated.")
    else:
        con.execute("DELETE FROM services WHERE id=?", (sid,))
        flash("Service removed.")
    con.commit()
    con.close()
    return redirect(url_for("services"))


# ================= OFFERS & COUPONS =================

@app.route("/offers", methods=["GET", "POST"])
def offers():
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        kind = request.form.get("discount_type", "percent")
        until = request.form.get("valid_until", "")
        try:
            value = float(request.form.get("discount_value", 0))
            if not code or not title or value <= 0 or kind not in ("percent", "flat"):
                raise ValueError
            if kind == "percent" and value > 100:
                raise ValueError
            con = db()
            con.execute("""
                INSERT INTO offers(code,title,description,discount_type,discount_value,valid_until)
                VALUES(?,?,?,?,?,?)
            """, (code, title, description, kind, value, until))
            con.commit()
            con.close()
            flash("Coupon created.")
        except sqlite3.IntegrityError:
            flash("That coupon code already exists.", "error")
        except ValueError:
            flash("Check the coupon details and discount value.", "error")
        return redirect(url_for("offers"))

    con = db()
    rows = con.execute("SELECT * FROM offers ORDER BY id DESC").fetchall()
    con.close()
    content = """
    <div class="panel"><h2>Create coupon</h2><form method="post"><div class="grid">
    <div class="field"><label>Coupon code</label><input name="code" required placeholder="NEW10"></div>
    <div class="field"><label>Offer title</label><input name="title" required></div>
    <div class="field"><label>Discount type</label><select name="discount_type">
    <option value="percent">Percentage (%)</option><option value="flat">Flat amount (₹)</option></select></div>
    <div class="field"><label>Discount value</label><input type="number" name="discount_value" min="0.01" step="0.01" required></div>
    <div class="field"><label>Valid until</label><input type="date" name="valid_until" value="2027-12-31"></div>
    <div class="field"><label>Description</label><input name="description"></div></div>
    <div class="actions"><button class="btn green">Create Coupon</button></div></form></div>
    <div class="panel"><h2>Coupons</h2><div class="table-wrap"><table>
    <tr><th>Code</th><th>Offer</th><th>Discount</th><th>Expiry</th><th>Status</th><th>Action</th></tr>
    {% for o in rows %}<tr><td><b>{{ o.code }}</b></td><td>{{ o.title }}<br><span class="muted">{{ o.description }}</span></td>
    <td>{{ o.discount_value }}{{ '%' if o.discount_type=='percent' else ' ₹' }}</td>
    <td>{{ o.valid_until or 'No expiry' }}</td><td>{{ 'Active' if o.active else 'Inactive' }}</td>
    <td><form method="post" action="{{ url_for('toggle_offer',oid=o.id) }}"><button class="btn gray">{{ 'Deactivate' if o.active else 'Activate' }}</button></form></td></tr>
    {% else %}<tr><td colspan="6">No coupons.</td></tr>{% endfor %}
    </table></div><p class="muted">Sample codes: WELCOME10, FESTIVE15, SAVE200.</p></div>
    """
    return page("Offers & Coupons", content, rows=rows)


@app.route("/offers/<int:oid>/toggle", methods=["POST"])
def toggle_offer(oid):
    con = db()
    con.execute("UPDATE offers SET active=CASE WHEN active=1 THEN 0 ELSE 1 END WHERE id=?", (oid,))
    con.commit()
    con.close()
    flash("Coupon status changed.")
    return redirect(url_for("offers"))


# ================= REPORTS =================

@app.route("/reports")
def reports():
    con = db()
    rows = con.execute("""
        SELECT service,COUNT(*) jobs,SUM(cost-discount) value,SUM(amount_paid) paid
        FROM bookings GROUP BY service ORDER BY jobs DESC
    """).fetchall()
    total = con.execute(
        "SELECT COALESCE(SUM(cost-discount),0) n FROM bookings"
    ).fetchone()["n"]
    paid = con.execute(
        "SELECT COALESCE(SUM(amount_paid),0) n FROM bookings"
    ).fetchone()["n"]
    con.close()
    content = """
    <div class="cards">
    <div class="stat">Service Value<strong>{{ total|money }}</strong>After discounts</div>
    <div class="stat">Payments Collected<strong>{{ paid|money }}</strong>Total paid</div>
    <div class="stat">Balance Due<strong>{{ [total-paid,0]|max|money }}</strong>Estimated balance</div>
    <div class="stat">Service Categories<strong>{{ rows|length }}</strong>With bookings</div>
    </div>
    <div class="panel"><h2>Service performance</h2><div class="table-wrap"><table>
    <tr><th>Service</th><th>Bookings</th><th>Service Value</th><th>Paid</th></tr>
    {% for r in rows %}<tr><td>{{ r.service }}</td><td>{{ r.jobs }}</td>
    <td>{{ (r.value or 0)|money }}</td><td>{{ (r.paid or 0)|money }}</td></tr>
    {% else %}<tr><td colspan="4">No data yet.</td></tr>{% endfor %}
    </table></div></div>
    """
    return page("Reports", content, rows=rows, total=total, paid=paid)


# ================= START =================

if __name__ == "__main__":
    setup_db()
    print("BikeCare database:", DB_PATH)
    print("Open http://127.0.0.1:5002")
    app.run(host="127.0.0.1", port=5002, debug=False)
