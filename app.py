# -*- coding: utf-8 -*-
"""دفتر المدرس - موقع لإدارة الطلاب والجداول والدرجات والحضور
التشغيل:  pip install flask  ثم  python app.py  وافتح http://127.0.0.1:5000
للإنتاج: PRODUCTION=1 (يتطلب HTTPS) و REG_CODE=رمز_سري (لمنع تسجيل الغرباء)
"""
import os, re, io, csv, sys, time, sqlite3, secrets, zipfile, tempfile
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from flask import (Flask, g, request, session, redirect, render_template,
                   abort, flash, Response, send_from_directory)
from jinja2 import DictLoader
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DATA_DIR", BASE)  # على Render: مسار القرص الدائم
os.makedirs(DATA, exist_ok=True)
DB = os.path.join(DATA, "school.db")
DAYS = ["الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"]
USER_RE = re.compile(r"^[A-Za-z0-9_\u0600-\u06FF]{3,30}$")
FAILS = {}  # محاولات الدخول الفاشلة
TZ_HOURS = 3  # توقيت العراق (UTC+3) لعرض الأوقات في سجل النشاط
PER_PAGE = 50
UPLOADS = os.path.join(DATA, "uploads")  # صور الطلاب (خارج المجلد العام)
MAX_IMG = 2 * 1024 * 1024  # أقصى حجم للصورة 2MB

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
<header><a class="logo" href="/">📘 دفتر المدرس</a>{% if me %}<nav><a href="/">الصفوف</a><a href="/schedule">الجدول</a>{% if is_admin %}<a href="/admin">⚙ لوحة المدير</a>{% endif %}
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
<div class="wrap"><table><tr><th>#</th><th>الاسم</th><th></th></tr>{% for s in students %}<tr><td>{{loop.index}}</td><td><a href="/student/{{s.id}}">{{s.name}}</a></td><td>{{dl("/student/%d/delete"|format(s.id))}}</td></tr>{% endfor %}</table></div></div>
<div class="card"><h2>الاختبارات والواجبات</h2><form method="post" action="/class/{{c.id}}/assess" class="row">""" + H + """
<input name="title" placeholder="مثال: امتحان الشهر الأول" required maxlength="60"><input name="max" type="number" step="0.5" min="1" max="1000" value="100" required><button>إضافة</button></form>
<div class="wrap"><table><tr><th>العنوان</th><th>الدرجة العظمى</th><th></th></tr>{% for a in asm %}<tr><td>{{a.title}}</td><td>{{'%g'|format(a.max)}}</td><td>{{dl("/assess/%d/delete"|format(a.id))}}</td></tr>{% endfor %}</table></div></div>{% endblock %}""","grades.html": """{% extends "base.html" %}{% block c %}<h1>درجات {{c.name}}</h1><a href="/class/{{c.id}}">← رجوع</a>
<form method="post">""" + H + """<div class="card wrap"><table><tr><th>الطالب</th>{% for a in asm %}<th>{{a.title}}<br><small>من {{'%g'|format(a.max)}}</small></th>{% endfor %}<th>المجموع</th><th>النسبة</th></tr>
{% for r in rows %}<tr><td>{{r.name}}</td>{% for a in asm %}<td><input type="number" name="s_{{a.id}}_{{r.id}}" value="{{r.sc[a.id]}}" min="0" max="{{'%g'|format(a.max)}}" step="0.25" style="width:80px"></td>{% endfor %}<td>{{r.tot}}</td><td>{{r.pct}}</td></tr>{% endfor %}
<tr><th>المعدل</th>{% for a in asm %}<th>{{avg[a.id]}}</th>{% endfor %}<th></th><th></th></tr></table></div><button>حفظ الدرجات</button></form>{% endblock %}""",

"attendance.html": """{% extends "base.html" %}{% block c %}<h1>حضور {{c.name}}</h1><a href="/class/{{c.id}}">← رجوع</a>
<form method="get" class="row" style="margin:12px 0"><input type="date" name="d" value="{{d}}"><button class="o">عرض</button></form>
<form method="post">""" + H + """<input type="hidden" name="d" value="{{d}}"><div class="card wrap"><table><tr><th>الطالب</th><th>حاضر</th><th>غائب</th><th>متأخر</th></tr>
{% for s in students %}<tr><td>{{s.name}}</td>{% for k in ['p','a','l'] %}<td><input type="radio" name="st_{{s.id}}" value="{{k}}" {{'checked' if st.get(s.id,'p')==k}}></td>{% endfor %}</tr>{% endfor %}</table></div><button>حفظ الحضور</button></form>{% endblock %}""",

"admin.html": """{% extends "base.html" %}{% block c %}<h1>لوحة المدير</h1>
<div class="row" style="margin-bottom:14px"><a class="btn" href="/admin/log">سجل النشاط الكامل</a>
<form method="post" action="/admin/backup">""" + H + """<button class="o">⬇ تنزيل نسخة احتياطية</button></form></div>
<div class="card wrap"><h2>الأساتذة ({{ts|length}})</h2><table><tr><th>الاسم</th><th>المستخدم</th><th>الدور</th><th>الحالة</th><th>آخر دخول</th><th>الصفوف</th><th>الطلاب</th><th>إجراءات</th></tr>
{% for t in ts %}<tr><td>{{t.name}}</td><td>{{t.username}}</td><td>{{'مدير' if t.role=='admin' else 'مدرس'}}</td>
<td>{{'فعّال' if t.active else '⛔ موقوف'}}</td><td>{{t.last}}</td><td>{{t.nc}}</td><td>{{t.ns}}</td>
<td><a class="btn" href="/admin/teacher/{{t.id}}">عرض</a> <a class="btn" href="/admin/log?t={{t.id}}">نشاطه</a></td></tr>{% endfor %}</table></div>
<div class="card"><h2>آخر الأنشطة</h2>{% include "logtable.html" %}</div>{% endblock %}""",

"logtable.html": """<div class="wrap"><table><tr><th>الوقت</th><th>المستخدم</th><th>العملية</th><th>التفاصيل</th><th>IP</th></tr>
{% for r in logs %}<tr><td dir="ltr">{{r.t}}</td><td>{{r.username}}</td><td>{{r.action}}</td><td>{{r.detail}}</td><td dir="ltr">{{r.ip}}</td></tr>
{% else %}<tr><td colspan="5">لا توجد سجلات</td></tr>{% endfor %}</table></div>""",

"admin_log.html": """{% extends "base.html" %}{% block c %}<h1>سجل النشاط</h1><a href="/admin">← رجوع</a>
<form method="get" class="row" style="margin:12px 0"><select name="t"><option value="">كل المستخدمين</option>
{% for t in ts %}<option value="{{t.id}}" {{'selected' if sel==t.id}}>{{t.name}} ({{t.username}})</option>{% endfor %}</select>
<select name="a"><option value="">كل العمليات</option>{% for a in acts %}<option {{'selected' if a==act}}>{{a}}</option>{% endfor %}</select>
<button class="o">تصفية</button></form>
<div class="card">{% include "logtable.html" %}
<div class="row" style="margin-top:10px">{% if page>1 %}<a class="btn" href="?t={{sel or ''}}&a={{act}}&p={{page-1}}">السابق</a>{% endif %}
<span>صفحة {{page}}</span>{% if more %}<a class="btn" href="?t={{sel or ''}}&a={{act}}&p={{page+1}}">التالي</a>{% endif %}</div></div>{% endblock %}""",

"admin_teacher.html": """{% extends "base.html" %}{% from "m.html" import dl with context %}{% block c %}<h1>{{t.name}} <small>({{t.username}})</small></h1><a href="/admin">← رجوع</a>
<div class="card"><h2>التحكم بالحساب</h2><div class="row">
{% if t.id != session.tid %}
<form method="post" action="/admin/teacher/{{t.id}}/toggle">""" + H + """<button class="{{'o' if not t.active else 'd'}}">{{'تفعيل الحساب' if not t.active else 'إيقاف الحساب'}}</button></form>
<form method="post" action="/admin/teacher/{{t.id}}/role">""" + H + """<button class="o">{{'إلغاء صلاحية المدير' if t.role=='admin' else 'ترقية إلى مدير'}}</button></form>
{% endif %}
<form method="post" action="/admin/teacher/{{t.id}}/password" class="row">""" + H + """<input name="p" type="password" placeholder="كلمة مرور جديدة (8+)" required minlength="8" maxlength="128"><button>تغيير كلمة المرور</button></form>
</div>{% if t.id != session.tid %}<p>{{dl("/admin/teacher/%d/delete"|format(t.id))}}</p>{% endif %}</div>
<div class="card wrap"><h2>صفوفه</h2><table><tr><th>الصف</th><th>المادة</th><th>الطلاب</th><th>الاختبارات</th></tr>
{% for c in cl %}<tr><td>{{c.name}}</td><td>{{c.subject}}</td><td>{{c.n}}</td><td>{{c.a}}</td></tr>{% else %}<tr><td colspan="4">لا توجد صفوف</td></tr>{% endfor %}</table></div>
<div class="card"><h2>آخر نشاطه</h2>{% include "logtable.html" %}</div>{% endblock %}""",

"student.html": """{% extends "base.html" %}{% from "m.html" import dl with context %}{% block c %}
<h1>{{s.name}} <small>{{s.cname}}</small></h1><a href="/class/{{s.class_id}}">← رجوع للصف</a>
<div class="card"><h2>الصورة والبيانات</h2><div class="row" style="align-items:flex-start;gap:24px">
<div>{% if s.photo %}<img src="/student/{{s.id}}/photo" alt="" style="width:150px;height:150px;object-fit:cover;border-radius:50%;border:4px solid #ff8c1a">{% else %}<div style="width:150px;height:150px;border-radius:50%;background:#dde3ef;display:flex;align-items:center;justify-content:center">لا توجد صورة</div>{% endif %}
{% if can_edit %}<form method="post" action="/student/{{s.id}}/photo" enctype="multipart/form-data" style="margin-top:10px">""" + H + """<input type="file" name="photo" accept="image/png,image/jpeg,image/webp" required style="width:190px"><br><button style="margin-top:6px">رفع الصورة</button></form>{% endif %}</div>
<div style="flex:1;min-width:240px">
{% if can_edit %}<form method="post" action="/student/{{s.id}}/info">""" + H + """
<p>تاريخ التسجيل<br><input type="date" name="reg_date" value="{{s.reg_date or ''}}"></p>
<p>مستوى الطالب قبل (0-100)<br><input type="number" name="level_before" min="0" max="100" value="{{'' if s.level_before is none else s.level_before}}"></p>
<p>مستوى الطالب بعد (0-100)<br><input type="number" name="level_after" min="0" max="100" value="{{'' if s.level_after is none else s.level_after}}"></p>
<button>حفظ</button></form>
{% else %}<p>تاريخ التسجيل: {{s.reg_date or 'غير محدد'}}</p><p>المستوى قبل: {{'-' if s.level_before is none else s.level_before}} · بعد: {{'-' if s.level_after is none else s.level_after}}</p>{% endif %}
{% if s.level_before is not none and s.level_after is not none %}<p><b>التطور: {{s.level_after - s.level_before}} نقطة</b></p>{% endif %}
</div></div></div>
<div class="card"><h2>الدرجات والامتحانات</h2><div class="wrap"><table><tr><th>الامتحان</th><th>الدرجة</th><th>من</th><th>النسبة</th></tr>
{% for r in rows %}<tr><td>{{r.title}}</td><td>{{r.score}}</td><td>{{r.mx}}</td><td>{{r.pct}}</td></tr>{% else %}<tr><td colspan="4">لا توجد امتحانات في هذا الصف</td></tr>{% endfor %}</table></div>
{% if total_pct %}<p><b>النسبة الإجمالية: {{total_pct}}</b></p>{% endif %}
{% if can_edit %}<a class="btn" href="/class/{{s.class_id}}/grades">تعديل الدرجات</a>{% endif %}</div>
<div class="card"><h2>الحضور</h2><p>حاضر: {{att.p}} · غائب: {{att.a}} · متأخر: {{att.l}}</p></div>
<div class="card"><h2>المدفوعات</h2><div class="wrap"><table><tr><th>المبلغ</th><th>التاريخ</th><th>ملاحظة</th><th></th></tr>
{% for p in pays %}<tr><td>{{'%g'|format(p.amount)}}</td><td>{{p.paid_at}}</td><td>{{p.note}}</td><td>{% if can_edit %}{{dl("/payment/%d/delete"|format(p.id))}}{% endif %}</td></tr>{% else %}<tr><td colspan="4">لا توجد دفعات</td></tr>{% endfor %}</table></div>
<p><b>مجموع ما دفعه: {{paid}}</b></p>
{% if can_edit %}<form method="post" action="/student/{{s.id}}/payment" class="row">""" + H + """
<input name="amount" type="number" step="0.01" min="0.01" placeholder="المبلغ" required><input name="paid_at" type="date" value="{{today}}"><input name="note" placeholder="ملاحظة (اختياري)" maxlength="100"><button>تسجيل دفعة</button></form>{% endif %}</div>{% endblock %}""",

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
kf = os.path.join(DATA, ".secret_key")
if not os.path.exists(kf):
    with open(kf, "w") as f:
        f.write(secrets.token_hex(32))
    try: os.chmod(kf, 0o600)
    except OSError: pass
app.secret_key = open(kf).read().strip()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("PRODUCTION") == "1",
                  MAX_CONTENT_LENGTH=3 << 20, PERMANENT_SESSION_LIFETIME=7200)
app.jinja_loader = DictLoader(T)
SCHEMA = """
CREATE TABLE IF NOT EXISTS teachers(id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE, name TEXT, pw TEXT);
CREATE TABLE IF NOT EXISTS classes(id INTEGER PRIMARY KEY, teacher_id INTEGER REFERENCES teachers(id) ON DELETE CASCADE, name TEXT, subject TEXT);
CREATE TABLE IF NOT EXISTS students(id INTEGER PRIMARY KEY, class_id INTEGER REFERENCES classes(id) ON DELETE CASCADE, name TEXT);
CREATE TABLE IF NOT EXISTS assessments(id INTEGER PRIMARY KEY, class_id INTEGER REFERENCES classes(id) ON DELETE CASCADE, title TEXT, max REAL);
CREATE TABLE IF NOT EXISTS grades(assessment_id INTEGER REFERENCES assessments(id) ON DELETE CASCADE, student_id INTEGER REFERENCES students(id) ON DELETE CASCADE, score REAL, PRIMARY KEY(assessment_id, student_id));
CREATE TABLE IF NOT EXISTS attendance(student_id INTEGER REFERENCES students(id) ON DELETE CASCADE, date TEXT, status TEXT, PRIMARY KEY(student_id, date));
CREATE TABLE IF NOT EXISTS schedule(id INTEGER PRIMARY KEY, teacher_id INTEGER REFERENCES teachers(id) ON DELETE CASCADE, class_id INTEGER REFERENCES classes(id) ON DELETE CASCADE, day INTEGER, period INTEGER, note TEXT, UNIQUE(teacher_id, day, period));
CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY, ts TEXT, teacher_id INTEGER, username TEXT, action TEXT, detail TEXT, ip TEXT);
CREATE INDEX IF NOT EXISTS act_ts ON activity(id DESC);
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY, student_id INTEGER REFERENCES students(id) ON DELETE CASCADE, amount REAL, paid_at TEXT, note TEXT);
"""

def init():
    c = sqlite3.connect(DB); c.executescript(SCHEMA)
    cols = [r[1] for r in c.execute("PRAGMA table_info(teachers)")]  # ترقية قاعدة البيانات القديمة دون فقدان بيانات
    if "role" not in cols: c.execute("ALTER TABLE teachers ADD COLUMN role TEXT DEFAULT 'teacher'")
    if "active" not in cols: c.execute("ALTER TABLE teachers ADD COLUMN active INTEGER DEFAULT 1")
    if "last_login" not in cols: c.execute("ALTER TABLE teachers ADD COLUMN last_login TEXT")
    scols = [r[1] for r in c.execute("PRAGMA table_info(students)")]
    for col, typ in (("reg_date", "TEXT"), ("photo", "TEXT"), ("level_before", "INTEGER"), ("level_after", "INTEGER")):
        if col not in scols: c.execute(f"ALTER TABLE students ADD COLUMN {col} {typ}")
    au = os.environ.get("ADMIN_USER")  # اسم حسابك: يصبح مديراً عند تشغيل الموقع
    if au: c.execute("UPDATE teachers SET role='admin' WHERE username=?", (au,))
    c.commit(); c.close()
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

def now(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def local(ts):  # من UTC إلى توقيت العراق للعرض
    try: return (datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") + timedelta(hours=TZ_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError): return ""

def log(action, detail="", tid=None, username=None):
    """تسجيل نشاط في السجل (لا تضع كلمات المرور هنا أبداً)"""
    tid = tid if tid is not None else session.get("tid")
    if username is None: username = session.get("user", "")
    try:
        x("INSERT INTO activity(ts,teacher_id,username,action,detail,ip) VALUES(?,?,?,?,?,?)",
          (now(), tid, username, action, detail[:200], request.remote_addr))
    except sqlite3.Error: pass

def clean(s, n=100): return (s or "").strip()[:n]
def fmt(v): return "" if v is None else f"{round(v, 2):g}"
def safe(s): return "'" + s if s.startswith(("=", "+", "-", "@")) else s
def num(v, lo, hi):
    try: f = float(v)
    except (TypeError, ValueError): return None
    return f if lo <= f <= hi else None
BACKUPS = os.path.join(DATA, "backups")
_last_bk = [0.0]

def auto_backup():
    """نسخة احتياطية تلقائية مرة كل 24 ساعة، ونحتفظ بآخر 14 نسخة"""
    if time.time() - _last_bk[0] < 3600: return
    _last_bk[0] = time.time()
    try:
        os.makedirs(BACKUPS, exist_ok=True)
        files = sorted(f for f in os.listdir(BACKUPS) if f.endswith(".zip"))
        if files and time.time() - os.path.getmtime(os.path.join(BACKUPS, files[-1])) < 86400: return
        tmp = os.path.join(BACKUPS, "tmp.db")
        src = sqlite3.connect(DB); dst = sqlite3.connect(tmp); src.backup(dst); dst.close(); src.close()
        name = os.path.join(BACKUPS, "backup-" + datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M") + ".zip")
        with zipfile.ZipFile(name, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp, "school.db")
            if os.path.isdir(UPLOADS):
                for n in os.listdir(UPLOADS): z.write(os.path.join(UPLOADS, n), "uploads/" + n)
        os.remove(tmp)
        for old in files[:-13]: os.remove(os.path.join(BACKUPS, old))
    except Exception: pass

@app.before_request
def guard():
    auto_backup()
    if "csrf" not in session: session["csrf"] = secrets.token_hex(16)
    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("csrf", "").encode(), session["csrf"].encode()):
            abort(400)

@app.context_processor
def inj(): return dict(csrf=session.get("csrf", ""), me=session.get("name"), days=DAYS,
                       is_admin=session.get("role") == "admin")

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
        # نتحقق في كل طلب من قاعدة البيانات: الإيقاف وسحب الصلاحية يسريان فوراً
        t = q("SELECT active, role, name, username FROM teachers WHERE id=?", (session["tid"],), True)
        if not t or not t["active"]:
            session.clear(); flash("الحساب موقوف أو غير موجود"); return redirect("/login")
        session["role"] = t["role"]; session["user"] = t["username"]
        return f(*a, **k)
    return w

def admin_only(f):
    @wraps(f)
    @auth
    def w(*a, **k):
        if session.get("role") != "admin":
            log("محاولة دخول للوحة المدير", request.path); abort(404)
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
            log("إنشاء حساب", n, username=u)
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
                if not t["active"]:
                    log("دخول مرفوض (حساب موقوف)", tid=t["id"], username=t["username"])
                    flash("هذا الحساب موقوف، تواصل مع المدير"); return render_template("login.html", title="تسجيل الدخول", reg=False)
                FAILS.pop(k, None); session.clear()
                session.update(tid=t["id"], name=t["name"], user=t["username"], role=t["role"], csrf=secrets.token_hex(16))
                session.permanent = True
                x("UPDATE teachers SET last_login=? WHERE id=?", (now(), t["id"]))
                log("تسجيل دخول")
                return redirect("/")
            log("فشل تسجيل الدخول", "المستخدم: " + u, tid=t["id"] if t else None, username=u)
            FAILS[k] = (FAILS.get(k, (0, 0))[0] + 1, time.time()); flash("بيانات الدخول غير صحيحة")
    return render_template("login.html", title="تسجيل الدخول", reg=False)

@app.post("/logout")
def logout():
    if "tid" in session: log("تسجيل خروج")
    session.clear(); return redirect("/login")# ---------------- الصفوف والطلاب ----------------
@app.route("/")
@auth
def index():
    cl = q("SELECT c.*,(SELECT COUNT(*) FROM students WHERE class_id=c.id) n FROM classes c WHERE teacher_id=? ORDER BY id", (session["tid"],))
    return render_template("index.html", classes=cl)

@app.post("/class/add")
@auth
def class_add():
    n = clean(request.form.get("name"), 60)
    if n:
        x("INSERT INTO classes(teacher_id,name,subject) VALUES(?,?,?)", (session["tid"], n, clean(request.form.get("subject"), 60)))
        log("إضافة صف", n)
    return redirect("/")

@app.post("/class/<int:cid>/delete")
@auth
def class_del(cid):
    c = q("SELECT name FROM classes WHERE id=? AND teacher_id=?", (cid, session["tid"]), True)
    x("DELETE FROM classes WHERE id=? AND teacher_id=?", (cid, session["tid"]))
    if c: log("حذف صف", c["name"])
    return redirect("/")

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
    c = mycls(cid)
    names = [clean(l, 80) for l in (request.form.get("names") or "").splitlines()[:200]]
    names = [n for n in names if n]
    today = date.today().isoformat()
    db().executemany("INSERT INTO students(class_id,name,reg_date) VALUES(?,?,?)", [(cid, n, today) for n in names]); db().commit()
    if names: log("إضافة طلاب", f"{len(names)} طالب في {c['name']}")
    return redirect(f"/class/{cid}")

@app.post("/student/<int:sid>/delete")
@auth
def stu_del(sid):
    cid = owned("students", sid)
    s = q("SELECT name, photo FROM students WHERE id=?", (sid,), True)
    x("DELETE FROM students WHERE id=?", (sid,)); log("حذف طالب", s["name"] if s else "")
    if s and s["photo"]:
        try: os.remove(os.path.join(UPLOADS, os.path.basename(s["photo"])))
        except OSError: pass
    return redirect(f"/class/{cid}")

@app.post("/class/<int:cid>/assess")
@auth
def asm_add(cid):
    mycls(cid); t = clean(request.form.get("title"), 60); m = num(request.form.get("max"), 1, 1000)
    if t and m:
        x("INSERT INTO assessments(class_id,title,max) VALUES(?,?,?)", (cid, t, m)); log("إضافة اختبار", t)
    return redirect(f"/class/{cid}")

@app.post("/assess/<int:aid>/delete")
@auth
def asm_del(aid):
    cid = owned("assessments", aid)
    a = q("SELECT title FROM assessments WHERE id=?", (aid,), True)
    x("DELETE FROM assessments WHERE id=?", (aid,)); log("حذف اختبار", a["title"] if a else "")
    return redirect(f"/class/{cid}")

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
        db().commit(); log("حفظ درجات", c["name"]); flash("تم حفظ الدرجات"); return redirect(f"/class/{cid}/grades")
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
    c = mycls(cid); st, asm, sc = gradedata(cid); log("تصدير الدرجات", c["name"])
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
        db().commit(); log("حفظ حضور", f"{c['name']} - {d}"); flash("تم حفظ الحضور"); return redirect(f"/class/{cid}/attendance?d={d}")
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
        log("إضافة حصة", f"{DAYS[d]} - الحصة {p}")
    return redirect("/schedule")

@app.post("/schedule/<int:sid>/delete")
@auth
def sch_del(sid):
    x("DELETE FROM schedule WHERE id=? AND teacher_id=?", (sid, session["tid"])); log("حذف حصة")
    return redirect("/schedule")# ---------------- ملف الطالب ----------------
def stu_get(sid, write=False):
    """المدرس يرى ويعدّل طلابه فقط، والمدير يرى كل الطلاب دون تعديل"""
    s = q("SELECT s.*, c.name AS cname, c.teacher_id AS owner FROM students s "
          "JOIN classes c ON c.id=s.class_id WHERE s.id=?", (sid,), True)
    if not s: abort(404)
    if s["owner"] != session["tid"] and (write or session.get("role") != "admin"): abort(404)
    return s

def img_ext(head):  # نتحقق من محتوى الملف الحقيقي لا من اسمه
    if head.startswith(b"\x89PNG\r\n\x1a\n"): return "png"
    if head.startswith(b"\xff\xd8\xff"): return "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP": return "webp"
    return None

@app.get("/student/<int:sid>")
@auth
def stu_page(sid):
    s = stu_get(sid)
    gr = q("SELECT a.title, a.max, g.score FROM assessments a LEFT JOIN grades g "
           "ON g.assessment_id=a.id AND g.student_id=? WHERE a.class_id=? ORDER BY a.id", (sid, s["class_id"]))
    rows = [dict(title=r["title"], mx=fmt(r["max"]), score=fmt(r["score"]),
                 pct=f"{r['score'] / r['max'] * 100:.1f}%" if r["score"] is not None else "") for r in gr]
    got = [r for r in gr if r["score"] is not None]
    tot, mx = sum(r["score"] for r in got), sum(r["max"] for r in got)
    pays = q("SELECT * FROM payments WHERE student_id=? ORDER BY paid_at DESC, id DESC", (sid,))
    att = {"p": 0, "a": 0, "l": 0}
    for r in q("SELECT status, COUNT(*) n FROM attendance WHERE student_id=? GROUP BY status", (sid,)):
        att[r["status"]] = r["n"]
    log("عرض ملف طالب", s["name"])
    return render_template("student.html", s=s, rows=rows, total_pct=f"{tot / mx * 100:.1f}%" if mx else "",
                           pays=pays, paid=fmt(sum(p["amount"] for p in pays)) or "0", att=att,
                           can_edit=s["owner"] == session["tid"], today=date.today().isoformat())

@app.post("/student/<int:sid>/info")
@auth
def stu_info(sid):
    s = stu_get(sid, True)
    try: rd = date.fromisoformat(request.form.get("reg_date") or "").isoformat()
    except ValueError: rd = None
    lv = []
    for k in ("level_before", "level_after"):
        v = num(request.form.get(k), 0, 100) if (request.form.get(k) or "").strip() else None
        lv.append(None if v is None else int(v))
    x("UPDATE students SET reg_date=?, level_before=?, level_after=? WHERE id=?", (rd, lv[0], lv[1], sid))
    log("تعديل بيانات طالب", s["name"]); flash("تم الحفظ")
    return redirect(f"/student/{sid}")

@app.get("/student/<int:sid>/photo")
@auth
def stu_photo(sid):
    s = stu_get(sid)
    if not s["photo"]: abort(404)
    return send_from_directory(UPLOADS, os.path.basename(s["photo"]))

@app.post("/student/<int:sid>/photo")
@auth
def stu_photo_set(sid):
    s = stu_get(sid, True)
    f = request.files.get("photo")
    data = f.read(MAX_IMG + 1) if f else b""
    ext = img_ext(data[:12])
    if not data or len(data) > MAX_IMG or not ext:
        flash("الصورة غير صالحة (png أو jpg أو webp وبحجم أقل من 2 ميغابايت)")
        return redirect(f"/student/{sid}")
    os.makedirs(UPLOADS, exist_ok=True)
    name = secrets.token_hex(16) + "." + ext
    with open(os.path.join(UPLOADS, name), "wb") as fh: fh.write(data)
    x("UPDATE students SET photo=? WHERE id=?", (name, sid))
    if s["photo"]:
        try: os.remove(os.path.join(UPLOADS, os.path.basename(s["photo"])))
        except OSError: pass
    log("رفع صورة طالب", s["name"]); flash("تم رفع الصورة")
    return redirect(f"/student/{sid}")

@app.post("/student/<int:sid>/payment")
@auth
def pay_add(sid):
    s = stu_get(sid, True)
    amt = num(request.form.get("amount"), 0.01, 1e9)
    try: d = date.fromisoformat(request.form.get("paid_at") or "").isoformat()
    except ValueError: d = date.today().isoformat()
    if amt is None: flash("المبلغ غير صحيح")
    else:
        x("INSERT INTO payments(student_id,amount,paid_at,note) VALUES(?,?,?,?)",
          (sid, amt, d, clean(request.form.get("note"), 100)))
        log("تسجيل دفعة", f"{s['name']} - {fmt(amt)}"); flash("تم تسجيل الدفعة")
    return redirect(f"/student/{sid}")

@app.post("/payment/<int:pid>/delete")
@auth
def pay_del(pid):
    p = q("SELECT p.student_id, p.amount FROM payments p JOIN students s ON s.id=p.student_id "
          "JOIN classes c ON c.id=s.class_id WHERE p.id=? AND c.teacher_id=?", (pid, session["tid"]), True)
    if not p: abort(404)
    x("DELETE FROM payments WHERE id=?", (pid,)); log("حذف دفعة", fmt(p["amount"]))
    return redirect(f"/student/{p['student_id']}")

# ---------------- لوحة المدير ----------------
def logrows(where="", args=(), limit=PER_PAGE, offset=0):
    rs = q(f"SELECT * FROM activity {where} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset))
    return [dict(t=local(r["ts"]), username=r["username"], action=r["action"], detail=r["detail"], ip=r["ip"]) for r in rs]

def teacher_or_404(tid):
    t = q("SELECT * FROM teachers WHERE id=?", (tid,), True)
    if not t: abort(404)
    return t

@app.route("/admin")
@admin_only
def admin():
    ts = [dict(r, last=local(r["last_login"]) or "—") for r in q(
        "SELECT t.*,(SELECT COUNT(*) FROM classes WHERE teacher_id=t.id) nc,"
        "(SELECT COUNT(*) FROM students s JOIN classes c ON c.id=s.class_id WHERE c.teacher_id=t.id) ns "
        "FROM teachers t ORDER BY t.id")]
    return render_template("admin.html", ts=ts, logs=logrows(limit=20))

@app.route("/admin/log")
@admin_only
def admin_log():
    sel = request.args.get("t", type=int); act = clean(request.args.get("a"), 60)
    page = max(request.args.get("p", 1, type=int), 1)
    w, a = [], []
    if sel: w.append("teacher_id=?"); a.append(sel)
    if act: w.append("action=?"); a.append(act)
    rows = logrows("WHERE " + " AND ".join(w) if w else "", tuple(a), PER_PAGE + 1, (page - 1) * PER_PAGE)
    return render_template("admin_log.html", logs=rows[:PER_PAGE], more=len(rows) > PER_PAGE, page=page, sel=sel, act=act,
        ts=q("SELECT id,name,username FROM teachers ORDER BY name"),
        acts=[r[0] for r in q("SELECT DISTINCT action FROM activity ORDER BY action")])

@app.route("/admin/teacher/<int:tid>")
@admin_only
def admin_teacher(tid):
    t = teacher_or_404(tid); log("عرض حساب مدرس", t["username"])
    cl = q("SELECT c.*,(SELECT COUNT(*) FROM students WHERE class_id=c.id) n,"
           "(SELECT COUNT(*) FROM assessments WHERE class_id=c.id) a FROM classes c WHERE teacher_id=? ORDER BY id", (tid,))
    return render_template("admin_teacher.html", t=t, cl=cl, logs=logrows("WHERE teacher_id=?", (tid,), 30))

@app.post("/admin/teacher/<int:tid>/toggle")
@admin_only
def admin_toggle(tid):
    t = teacher_or_404(tid)
    if tid == session["tid"]: abort(400)
    new = 0 if t["active"] else 1
    x("UPDATE teachers SET active=? WHERE id=?", (new, tid))
    log("تفعيل حساب" if new else "إيقاف حساب", t["username"]); return redirect(f"/admin/teacher/{tid}")

@app.post("/admin/teacher/<int:tid>/role")
@admin_only
def admin_role(tid):
    t = teacher_or_404(tid)
    if tid == session["tid"]: abort(400)
    new = "teacher" if t["role"] == "admin" else "admin"
    x("UPDATE teachers SET role=? WHERE id=?", (new, tid))
    log("تغيير الصلاحية", f"{t['username']} ← {new}"); return redirect(f"/admin/teacher/{tid}")

@app.post("/admin/teacher/<int:tid>/password")
@admin_only
def admin_pw(tid):
    t = teacher_or_404(tid); p = request.form.get("p", "")
    if 8 <= len(p) <= 128:
        x("UPDATE teachers SET pw=? WHERE id=?", (generate_password_hash(p), tid))
        log("تغيير كلمة مرور مدرس", t["username"]); flash("تم تغيير كلمة المرور")
    else: flash("كلمة المرور يجب أن تكون 8 أحرف على الأقل")
    return redirect(f"/admin/teacher/{tid}")

@app.post("/admin/teacher/<int:tid>/delete")
@admin_only
def admin_del(tid):
    t = teacher_or_404(tid)
    if tid == session["tid"]: abort(400)
    x("DELETE FROM teachers WHERE id=?", (tid,))
    log("حذف حساب مدرس", t["username"]); flash("تم حذف الحساب وكل بياناته"); return redirect("/admin")

init()
@app.post("/admin/backup")
@admin_only
def admin_backup():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    src = sqlite3.connect(DB); dst = sqlite3.connect(tmp.name)
    src.backup(dst); dst.close(); src.close()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmp.name, "school.db")
        if os.path.isdir(UPLOADS):
            for n in os.listdir(UPLOADS): z.write(os.path.join(UPLOADS, n), "uploads/" + n)
    os.remove(tmp.name)
    log("تنزيل نسخة احتياطية")
    return Response(buf.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition": f"attachment; filename=backup-{date.today().isoformat()}.zip"})

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "make-admin":  # python app.py make-admin اسم_المستخدم
        c = sqlite3.connect(DB)
        n = c.execute("UPDATE teachers SET role='admin' WHERE username=?", (sys.argv[2],)).rowcount
        c.commit(); c.close()
        print("تم" if n else "المستخدم غير موجود"); sys.exit(0)
    app.run(host="0.0.0.0", port=5000, debug=False)
