# -*- coding: utf-8 -*-
"""دفتر المدرس - موقع لإدارة الطلاب والجداول والدرجات والحضور
التشغيل:  pip install flask  ثم  python app.py  وافتح http://127.0.0.1:5000
للإنتاج: PRODUCTION=1 (يتطلب HTTPS) و REG_CODE=رمز_سري (لمنع تسجيل الغرباء)
"""
import os, re, io, csv, time, sqlite3, secrets
from datetime import date
from functools import wraps
from flask import (Flask, g, request, session, redirect, render_template,
                   abort, flash, Response)
from jinja2 import DictLoader
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "school.db")
DAYS = ["الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"]
USER_RE = re.compile(r"^[A-Za-z0-9_\u0600-\u06FF]{3,30}$")
FAILS = {}  # محاولات الدخول الفاشلة

CSS = """:root{--n:#14213d;--n2:#1f3a6e;--o:#ff8c1a;--bg:#f4f6fb}*{box-sizing:border-box}
body{margin:0;font-family:Tahoma,'Segoe UI',sans-serif;background:var(--bg);color:#1b2438}
header{background:var(--n);color:#fff;display:flex;justify-content:space-between;align-items:center;padding:12px 20px;flex-wrap:wrap;gap:8px;border-bottom:4px solid var(--o)}
header a{color:#fff;text-decoration:none;margin-inline-end:14px}.logo{font-weight:700;font-size:20px}
nav{display:flex;align-items:center;gap:6px;flex-wrap:wrap}nav form{display:inline}
main{max-width:1000px;margin:20px auto;padding:0 14px}
.card{background:#fff;border-radius:12px;padding:18px;margin-bottom:16px;box-shadow:0 2px 8px #14213d18}
h1,h2{color:var(--n)}h2{border-inline-start:5px solid var(--o);padding-inline-start:10px}
input,select,textarea{padding:9px;border:1px solid #b9c2d6;border-radius:8px;font:inherit;max-width:100%}
button,.btn{background:var(--o);color:#fff;border:0;padding:9px 16px;border-radius:8px;font:inherit;cursor:pointer;text-decoration:none;display:inline-block}
button.o{background:transparent;border:1px solid var(--o)}button.d{background:#c0392b}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;background:#fff;margin-top:10px}th{background:var(--n);color:#fff}
th,td{padding:8px;border:1px solid #dde3ef;text-align:center}
.msg{background:#fff3e0;border:1px solid var(--o);padding:10px;border-radius:8px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
details summary{cursor:pointer;color:#c0392b;font-size:13px}"""

H = '<input type="hidden" name="csrf" value="{{csrf}}">'
T = {
"base.html": """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>دفتر المدرس</title>
<style>""" + CSS + """</style></head><body>
<header><a class="logo" href="/">📘 دفتر المدرس</a>{% if me %}<nav><a href="/">الصفوف</a><a href="/schedule">الجدول</a>
<span>{{me}}</span><form method="post" action="/logout">""" + H + """<button class="o">خروج</button></form></nav>{% endif %}</header>
<main>{% for m in get_flashed_messages() %}<div class="msg">{{m}}</div>{% endfor %}{% block c %}{% endblock %}</main></body></html>""",

"m.html": """{% macro dl(url) %}<details><summary>حذف</summary><form method="post" action="{{url}}">""" + H +
"""<button class="d">تأكيد الحذف</button></form></details>{% endmacro %}""",

"login.html": """{% extends "base.html" %}{% block c %}<div class="card" style="max-width:380px;margin:auto"><h1>{{title}}</h1>
<form method="post">""" + H + """
{% if reg %}<p><input name="name" placeholder="الاسم الكامل" required maxlength="60" style="width:100%"></p>{% endif %}
<p><input name="u" placeholder="اسم المستخدم" required maxlength="30" style="width:100%"></p>
<p><input name="p" type="password" placeholder="كلمة المرور (8 أحرف فأكثر)" required minlength="8" maxlength="128" style="width:100%"></p>
{% if need_code %}<p><input name="code" placeholder="رمز التسجيل" required style="width:100%"></p>{% endif %}
<button style="width:100%">{{title}}</button></form>
<p>{% if reg %}<a href="/login">لدي حساب</a>{% else %}<a href="/register">إنشاء حساب مدرس</a>{% endif %}</p></div>{% endblock %}""",

"index.html": """{% extends "base.html" %}{% from "m.html" import dl with context %}{% block c %}<h1>أهلاً {{me}}</h1>
<div class="card"><h2>إضافة صف / شعبة</h2><form method="post" action="/class/add" class="row">""" + H + """
<input name="name" placeholder="مثال: الخامس أ" required maxlength="60"><input name="subject" placeholder="المادة" maxlength="60"><button>إضافة</button></form></div>
<div class="grid">{% for c in classes %}<div class="card"><h2>{{c.name}}</h2><p>{{c.subject}} · {{c.n}} طالب</p>
<a class="btn" href="/class/{{c.id}}">فتح</a> {{dl("/class/%d/delete"|format(c.id))}}</div>{% else %}<p>لا توجد صفوف بعد، أضف أول صف.</p>{% endfor %}</div>{% endblock %}""",

"class.html": """{% extends "base.html" %}{% from "m.html" import dl with context %}{% block c %}<h1>{{c.name}} <small>{{c.subject}}</small></h1>
<div class="row" style="margin-bottom:14px"><a class="btn" href="/class/{{c.id}}/grades">الدرجات</a><a class="btn" href="/class/{{c.id}}/attendance">الحضور</a><a class="btn" href="/class/{{c.id}}/grades.csv">تصدير Excel</a></div>
<div class="card"><h2>الطلاب ({{students|length}})</h2><form method="post" action="/class/{{c.id}}/students">""" + H + """
<textarea name="names" rows="4" style="width:100%" placeholder="اكتب اسم كل طالب في سطر مستقل"></textarea><br><button>إضافة الطلاب</button></form>
<div class="wrap"><table><tr><th>#</th><th>الاسم</th><th></th></tr>{% for s in students %}<tr><td>{{loop.index}}</td><td>{{s.name}}</td><td>{{dl("/student/%d/delete"|format(s.id))}}</td></tr>{% endfor %}</table></div></div>
<div class="card"><h2>الاختبارات والواجبات</h2><form method="post" action="/class/{{c.id}}/assess" class="row">""" + H + """
<input name="title" placeholder="مثال: امتحان الشهر الأول" required maxlength="60"><input name="max" type="number" step="0.5" min="1" max="1000" value="100" required><button>إضافة</button></form>
<div class="wrap"><table><tr><th>العنوان</th><th>الدرجة العظمى</th><th></th></tr>{% for a in asm %}<tr><td>{{a.title}}</td><td>{{'%g'|format(a.max)}}</td><td>{{dl("/assess/%d/delete"|format(a.id))}}</td></tr>{% endfor %}</table></div></div>{% endblock %}""",

"grades.html": """{% extends "base.html" %}{% block c %}<h1>درجات {{c.name}}</h1><a href="/class/{{c.id}}">← رجوع</a>
<form method="post">""" + H + """<div class="card wrap"><table><tr><th>الطالب</th>{% for a in asm %}<th>{{a.title}}<br><small>من {{'%g'|format(a.max)}}</small></th>{% endfor %}<th>المجموع</th><th>النسبة</th></tr>
{% for r in rows %}<tr><td>{{r.name}}</td>{% for a in asm %}<td><input type="number" name="s_{{a.id}}_{{r.id}}" value="{{r.sc[a.id]}}" min="0" max="{{'%g'|format(a.max)}}" step="0.25" style="width:80px"></td>{% endfor %}<td>{{r.tot}}</td><td>{{r.pct}}</td></tr>{% endfor %}
<tr><th>المعدل</th>{% for a in asm %}<th>{{avg[a.id]}}</th>{% endfor %}<th></th><th></th></tr></table></div><button>حفظ الدرجات</button></form>{% endblock %}""",

"attendance.html": """{% extends "base.html" %}{% block c %}<h1>حضور {{c.name}}</h1><a href="/class/{{c.id}}">← رجوع</a>
<form method="get" class="row" style="margin:12px 0"><input type="date" name="d" value="{{d}}"><button class="o">عرض</button></form>
<form method="post">""" + H + """<input type="hidden" name="d" value="{{d}}"><div class="card wrap"><table><tr><th>الطالب</th><th>حاضر</th><th>غائب</th><th>متأخر</th></tr>
{% for s in students %}<tr><td>{{s.name}}</td>{% for k in ['p','a','l'] %}<td><input type="radio" name="st_{{s.id}}" value="{{k}}" {{'checked' if st.get(s.id,'p')==k}}></td>{% endfor %}</tr>{% endfor %}</table></div><button>حفظ الحضور</button></form>{% endblock %}""",

"schedule.html": """{% extends "base.html" %}{% from "m.html" import dl with context %}{% block c %}<h1>الجدول الأسبوعي</h1>
<div class="card"><form method="post" action="/schedule/add" class="row">""" + H + """
<select name="day">{% for i in range(7) %}<option value="{{i}}">{{days[i]}}</option>{% endfor %}</select>
<select name="period">{% for p in range(1,9) %}<option>{{p}}</option>{% endfor %}</select>
<select name="cid">{% for c in classes %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select>
<input name="note" placeholder="ملاحظة / قاعة" maxlength="40"><button>إضافة حصة</button></form></div>
<div class="card wrap"><table><tr><th>الحصة</th>{% for d in days %}<th>{{d}}</th>{% endfor %}</tr>
{% for p in range(1,9) %}<tr><th>{{p}}</th>{% for i in range(7) %}<td>{% set e = grid.get((i,p)) %}{% if e %}<b>{{e.name}}</b><br><small>{{e.note}}</small>{{dl("/schedule/%d/delete"|format(e.id))}}{% endif %}</td>{% endfor %}</tr>{% endfor %}</table></div>{% endblock %}""",
}

# ---------------- الإعداد والأمان ----------------
app = Flask(__name__)
kf = os.path.join(BASE, ".secret_key")
if not os.path.exists(kf):
    with open(kf, "w") as f:
        f.write(secrets.token_hex(32))
    try: os.chmod(kf, 0o600)
    except OSError: pass
app.secret_key = open(kf).read().strip()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("PRODUCTION") == "1",
                  MAX_CONTENT_LENGTH=1 << 20, PERMANENT_SESSION_LIFETIME=7200)
app.jinja_loader = DictLoader(T)

SCHEMA = """
CREATE TABLE IF NOT EXISTS teachers(id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE, name TEXT, pw TEXT);
CREATE TABLE IF NOT EXISTS classes(id INTEGER PRIMARY KEY, teacher_id INTEGER REFERENCES teachers(id) ON DELETE CASCADE, name TEXT, subject TEXT);
CREATE TABLE IF NOT EXISTS students(id INTEGER PRIMARY KEY, class_id INTEGER REFERENCES classes(id) ON DELETE CASCADE, name TEXT);
CREATE TABLE IF NOT EXISTS assessments(id INTEGER PRIMARY KEY, class_id INTEGER REFERENCES classes(id) ON DELETE CASCADE, title TEXT, max REAL);
CREATE TABLE IF NOT EXISTS grades(assessment_id INTEGER REFERENCES assessments(id) ON DELETE CASCADE, student_id INTEGER REFERENCES students(id) ON DELETE CASCADE, score REAL, PRIMARY KEY(assessment_id, student_id));
CREATE TABLE IF NOT EXISTS attendance(student_id INTEGER REFERENCES students(id) ON DELETE CASCADE, date TEXT, status TEXT, PRIMARY KEY(student_id, date));
CREATE TABLE IF NOT EXISTS schedule(id INTEGER PRIMARY KEY, teacher_id INTEGER REFERENCES teachers(id) ON DELETE CASCADE, class_id INTEGER REFERENCES classes(id) ON DELETE CASCADE, day INTEGER, period INTEGER, note TEXT, UNIQUE(teacher_id, day, period));
"""

def init():
    c = sqlite3.connect(DB); c.executescript(SCHEMA); c.close()
    try: os.chmod(DB, 0o600)
    except OSError: pass

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def _close(e):
    d = g.pop("db", None)
    if d: d.close()

def q(sql, a=(), one=False):  # كل الاستعلامات بمعاملات (منع SQL Injection)
    r = db().execute(sql, a).fetchall()
    return (r[0] if r else None) if one else r

def x(sql, a=()):
    db().execute(sql, a); db().commit()

def clean(s, n=100): return (s or "").strip()[:n]
def fmt(v): return "" if v is None else f"{round(v, 2):g}"
def safe(s): return "'" + s if s.startswith(("=", "+", "-", "@")) else s
def num(v, lo, hi):
    try: f = float(v)
    except (TypeError, ValueError): return None
    return f if lo <= f <= hi else None

@app.before_request
def guard():  # حماية CSRF
    if "csrf" not in session: session["csrf"] = secrets.token_hex(16)
    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("csrf", "").encode(), session["csrf"].encode()):
            abort(400)

@app.context_processor
def inj(): return dict(csrf=session.get("csrf", ""), me=session.get("name"), days=DAYS)

@app.after_request
def hdr(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["Referrer-Policy"] = "same-origin"
    r.headers["Cache-Control"] = "no-store"
    r.headers["Content-Security-Policy"] = ("default-src 'self'; style-src 'unsafe-inline'; "
        "script-src 'none'; frame-ancestors 'none'; form-action 'self'")
    return r

@app.errorhandler(404)
@app.errorhandler(400)
@app.errorhandler(413)
def err(e): return "الصفحة غير موجودة أو الطلب غير صالح", e.code

def auth(f):
    @wraps(f)
    def w(*a, **k):
        if "tid" not in session: return redirect("/login")
        return f(*a, **k)
    return w

def mycls(cid):  # التأكد أن الصف يخص المدرس الحالي فقط
    c = q("SELECT * FROM classes WHERE id=? AND teacher_id=?", (cid, session["tid"]), True)
    if not c: abort(404)
    return c

def owned(tbl, i):  # tbl ثابت داخلي: students أو assessments
    r = q(f"SELECT t.class_id FROM {tbl} t JOIN classes c ON c.id=t.class_id WHERE t.id=? AND c.teacher_id=?",
          (i, session["tid"]), True)
    if not r: abort(404)
    return r[0]

def locked(k):
    n, t = FAILS.get(k, (0, 0))
    if n >= 5 and time.time() - t >= 300: FAILS.pop(k, None); return False
    return n >= 5

# ---------------- الحسابات ----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    code = os.environ.get("REG_CODE")
    if request.method == "POST":
        u, n, p = clean(request.form.get("u"), 30), clean(request.form.get("name"), 60), request.form.get("p", "")
        if code and not secrets.compare_digest(request.form.get("code", "").encode(), code.encode()):
            flash("رمز التسجيل غير صحيح")
        elif not USER_RE.match(u) or len(n) < 2: flash("اسم المستخدم 3-30 (حروف وأرقام و _) والاسم مطلوب")
        elif not 8 <= len(p) <= 128: flash("كلمة المرور يجب أن تكون 8 أحرف على الأقل")
        elif q("SELECT 1 FROM teachers WHERE username=?", (u,), True): flash("اسم المستخدم مستخدم مسبقاً")
        else:
            x("INSERT INTO teachers(username,name,pw) VALUES(?,?,?)", (u, n, generate_password_hash(p)))
            flash("تم إنشاء الحساب، سجّل الدخول"); return redirect("/login")
    return render_template("login.html", title="حساب جديد", reg=True, need_code=bool(code))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = clean(request.form.get("u"), 30); k = (request.remote_addr, u.lower())
        if locked(k): flash("محاولات كثيرة، حاول بعد 5 دقائق")
        else:
            t = q("SELECT * FROM teachers WHERE username=?", (u,), True)
            if t and check_password_hash(t["pw"], request.form.get("p", "")):
                FAILS.pop(k, None); session.clear()
                session.update(tid=t["id"], name=t["name"], csrf=secrets.token_hex(16))
                session.permanent = True
                return redirect("/")
            FAILS[k] = (FAILS.get(k, (0, 0))[0] + 1, time.time()); flash("بيانات الدخول غير صحيحة")
    return render_template("login.html", title="تسجيل الدخول", reg=False)

@app.post("/logout")
def logout():
    session.clear(); return redirect("/login")

# ---------------- الصفوف والطلاب ----------------
@app.route("/")
@auth
def index():
    cl = q("SELECT c.*,(SELECT COUNT(*) FROM students WHERE class_id=c.id) n FROM classes c WHERE teacher_id=? ORDER BY id", (session["tid"],))
    return render_template("index.html", classes=cl)

@app.post("/class/add")
@auth
def class_add():
    n = clean(request.form.get("name"), 60)
    if n: x("INSERT INTO classes(teacher_id,name,subject) VALUES(?,?,?)", (session["tid"], n, clean(request.form.get("subject"), 60)))
    return redirect("/")

@app.post("/class/<int:cid>/delete")
@auth
def class_del(cid):
    x("DELETE FROM classes WHERE id=? AND teacher_id=?", (cid, session["tid"])); return redirect("/")

@app.route("/class/<int:cid>")
@auth
def class_page(cid):
    c = mycls(cid)
    return render_template("class.html", c=c,
        students=q("SELECT * FROM students WHERE class_id=? ORDER BY name", (cid,)),
        asm=q("SELECT * FROM assessments WHERE class_id=? ORDER BY id", (cid,)))

@app.post("/class/<int:cid>/students")
@auth
def stu_add(cid):
    mycls(cid)
    names = [clean(l, 80) for l in (request.form.get("names") or "").splitlines()[:200]]
    db().executemany("INSERT INTO students(class_id,name) VALUES(?,?)", [(cid, n) for n in names if n]); db().commit()
    return redirect(f"/class/{cid}")

@app.post("/student/<int:sid>/delete")
@auth
def stu_del(sid):
    cid = owned("students", sid); x("DELETE FROM students WHERE id=?", (sid,)); return redirect(f"/class/{cid}")

@app.post("/class/<int:cid>/assess")
@auth
def asm_add(cid):
    mycls(cid); t = clean(request.form.get("title"), 60); m = num(request.form.get("max"), 1, 1000)
    if t and m: x("INSERT INTO assessments(class_id,title,max) VALUES(?,?,?)", (cid, t, m))
    return redirect(f"/class/{cid}")

@app.post("/assess/<int:aid>/delete")
@auth
def asm_del(aid):
    cid = owned("assessments", aid); x("DELETE FROM assessments WHERE id=?", (aid,)); return redirect(f"/class/{cid}")

# ---------------- الدرجات ----------------
def gradedata(cid):
    st = q("SELECT * FROM students WHERE class_id=? ORDER BY name", (cid,))
    asm = q("SELECT * FROM assessments WHERE class_id=? ORDER BY id", (cid,))
    sc = {(r["student_id"], r["assessment_id"]): r["score"] for r in q(
        "SELECT g.* FROM grades g JOIN assessments a ON a.id=g.assessment_id WHERE a.class_id=?", (cid,))}
    return st, asm, sc

@app.route("/class/<int:cid>/grades", methods=["GET", "POST"])
@auth
def grades(cid):
    c = mycls(cid); st, asm, sc = gradedata(cid)
    if request.method == "POST":
        for a in asm:
            for s in st:
                v = (request.form.get(f"s_{a['id']}_{s['id']}") or "").strip()
                if v == "": db().execute("DELETE FROM grades WHERE assessment_id=? AND student_id=?", (a["id"], s["id"]))
                else:
                    f = num(v, 0, a["max"])
                    if f is not None: db().execute("INSERT OR REPLACE INTO grades VALUES(?,?,?)", (a["id"], s["id"], f))
        db().commit(); flash("تم حفظ الدرجات"); return redirect(f"/class/{cid}/grades")
    rows = []
    for s in st:
        got = [(sc[(s["id"], a["id"])], a["max"]) for a in asm if (s["id"], a["id"]) in sc]
        t, m = sum(g for g, _ in got), sum(mx for _, mx in got)
        rows.append(dict(id=s["id"], name=s["name"], tot=fmt(t) if got else "", pct=f"{t / m * 100:.1f}%" if m else "",
                         sc={a["id"]: fmt(sc.get((s["id"], a["id"]))) for a in asm}))
    avg = {}
    for a in asm:
        v = [sc[(s["id"], a["id"])] for s in st if (s["id"], a["id"]) in sc]
        avg[a["id"]] = fmt(sum(v) / len(v)) if v else ""
    return render_template("grades.html", c=c, asm=asm, rows=rows, avg=avg)

@app.route("/class/<int:cid>/grades.csv")
@auth
def grades_csv(cid):
    mycls(cid); st, asm, sc = gradedata(cid)
    o = io.StringIO(); w = csv.writer(o)
    w.writerow(["الطالب"] + [f"{safe(a['title'])} ({a['max']:g})" for a in asm])
    for s in st: w.writerow([safe(s["name"])] + [fmt(sc.get((s["id"], a["id"]))) for a in asm])
    return Response("\ufeff" + o.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=grades.csv"})

# ---------------- الحضور ----------------
@app.route("/class/<int:cid>/attendance", methods=["GET", "POST"])
@auth
def attendance(cid):
    c = mycls(cid)
    try: d = date.fromisoformat(request.values.get("d") or "").isoformat()
    except ValueError: d = date.today().isoformat()
    st = q("SELECT * FROM students WHERE class_id=? ORDER BY name", (cid,))
    if request.method == "POST":
        for s in st:
            v = request.form.get(f"st_{s['id']}")
            if v in ("p", "a", "l"): db().execute("INSERT OR REPLACE INTO attendance VALUES(?,?,?)", (s["id"], d, v))
        db().commit(); flash("تم حفظ الحضور"); return redirect(f"/class/{cid}/attendance?d={d}")
    saved = {r["student_id"]: r["status"] for r in q(
        "SELECT * FROM attendance WHERE date=? AND student_id IN (SELECT id FROM students WHERE class_id=?)", (d, cid))}
    return render_template("attendance.html", c=c, d=d, students=st, st=saved)

# ---------------- الجدول ----------------
@app.route("/schedule")
@auth
def schedule():
    rows = q("SELECT s.*,c.name FROM schedule s JOIN classes c ON c.id=s.class_id WHERE s.teacher_id=?", (session["tid"],))
    return render_template("schedule.html", grid={(r["day"], r["period"]): r for r in rows},
                           classes=q("SELECT * FROM classes WHERE teacher_id=?", (session["tid"],)))

@app.post("/schedule/add")
@auth
def sch_add():
    mycls(request.form.get("cid", type=int))
    d, p = request.form.get("day", type=int), request.form.get("period", type=int)
    if d in range(7) and p in range(1, 9):
        x("INSERT OR REPLACE INTO schedule(teacher_id,class_id,day,period,note) VALUES(?,?,?,?,?)",
          (session["tid"], request.form.get("cid", type=int), d, p, clean(request.form.get("note"), 40)))
    return redirect("/schedule")

@app.post("/schedule/<int:sid>/delete")
@auth
def sch_del(sid):
    x("DELETE FROM schedule WHERE id=? AND teacher_id=?", (sid, session["tid"])); return redirect("/schedule")

init()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
