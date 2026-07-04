#!/usr/bin/env python3
# 手帐 · 自宅版 v1
import os, sqlite3, secrets, hashlib, time, datetime, functools
from flask import (Flask, request, session, redirect, render_template,
                   jsonify, send_file, abort)

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, 'shouzhang.db')
PHOTOS = os.path.join(BASE, 'photos')
os.makedirs(PHOTOS, exist_ok=True)

WEEKDAY = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute('pragma journal_mode=wal')
    return c

def init_db():
    with db() as c:
        c.executescript('''
        create table if not exists settings(key text primary key, value text);
        create table if not exists habits(
          id integer primary key, name text, glyph text, type text default 'check',
          sort integer default 0, active integer default 1);
        create table if not exists days(
          date text primary key, battery integer, period integer default 0);
        create table if not exists checks(
          date text, habit_id integer, value integer default 0,
          primary key(date, habit_id));
        create table if not exists notes(
          id integer primary key, date text, author text, kind text default 'note',
          content text, done integer default 0, created_at text);
        create table if not exists photos(
          id integer primary key, date text, slot text, filename text,
          caption text, created_at text);
        create table if not exists events(
          id integer primary key, ts text, author text, kind text,
          date text, detail text);
        ''')
        try:
            c.execute('alter table days add column symptoms text')
        except sqlite3.OperationalError:
            pass
        if not c.execute("select 1 from settings where key='secret'").fetchone():
            c.execute("insert into settings values('secret',?)", (secrets.token_hex(32),))
        if not c.execute("select 1 from settings where key='api_token'").fetchone():
            c.execute("insert into settings values('api_token',?)", (secrets.token_hex(24),))
        if not c.execute("select 1 from settings where key='cup_ml'").fetchone():
            c.execute("insert into settings values('cup_ml','200')")
        if not c.execute("select 1 from settings where key='start_date'").fetchone():
            c.execute("insert into settings values('start_date',?)",
                      (datetime.date.today().isoformat(),))

def setting(k, d=None):
    with db() as c:
        r = c.execute('select value from settings where key=?', (k,)).fetchone()
        return r['value'] if r else d

def set_setting(k, v):
    with db() as c:
        c.execute('insert into settings values(?,?) on conflict(key) do update set value=?', (k, v, v))

def log_event(author, kind, date='', detail=''):
    with db() as c:
        c.execute('insert into events(ts,author,kind,date,detail) values(?,?,?,?,?)',
                  (datetime.datetime.now().isoformat(timespec='seconds'), author, kind, date, detail))

init_db()
app = Flask(__name__)
app.secret_key = setting('secret')
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024
app.permanent_session_lifetime = datetime.timedelta(days=180)

def today():
    return datetime.date.today().isoformat()

def weekday_of(datestr):
    return WEEKDAY[datetime.date.fromisoformat(datestr).weekday()]

# ── 门 ──
def web_auth(f):
    @functools.wraps(f)
    def w(*a, **kw):
        if not session.get('in'):
            return redirect('/login')
        return f(*a, **kw)
    return w

def api_auth(f):
    @functools.wraps(f)
    def w(*a, **kw):
        tok = request.headers.get('Authorization', '').removeprefix('Bearer ').strip() \
              or request.args.get('token', '')
        if tok != setting('api_token'):
            abort(401)
        return f(*a, **kw)
    return w

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        code = request.form.get('code', '')
        h = hashlib.sha256(code.encode()).hexdigest()
        if h == setting('passcode_hash'):
            session.permanent = True
            session['in'] = True
            return redirect('/')
        time.sleep(1.2)
        return render_template('login.html', err='口令不对')
    return render_template('login.html', err='')

# ── 数据组装 ──
def day_payload(c, date):
    day = c.execute('select * from days where date=?', (date,)).fetchone()
    checks = {r['habit_id']: r['value'] for r in
              c.execute('select * from checks where date=?', (date,))}
    notes = [dict(r) for r in c.execute(
        "select * from notes where date=? order by created_at", (date,))]
    photos = [dict(r) for r in c.execute(
        'select id,slot,caption,created_at from photos where date=? order by id', (date,))]
    return {'date': date, 'weekday': weekday_of(date),
            'battery': day['battery'] if day else None,
            'period': bool(day['period']) if day else False,
            'symptoms': (day['symptoms'] if day else '') or '',
            'checks': checks, 'notes': notes, 'photos': photos}

@app.route('/')
@web_auth
def journal():
    start = setting('start_date', '2026-07-03')
    t = today()
    with db() as c:
        habits = [dict(r) for r in c.execute(
            'select * from habits where active=1 order by sort')]
        d = datetime.date.fromisoformat(t)
        dates = []
        while d.isoformat() >= start and len(dates) < 60:
            dates.append(d.isoformat())
            d -= datetime.timedelta(days=1)
        days = [day_payload(c, x) for x in dates]
        days.reverse()  # 旧→新，翻页从左往右
        todos = [dict(r) for r in c.execute(
            "select * from notes where kind='todo' and (date='' or date is null) "
            "order by done, id desc")]
    return render_template('journal.html', habits=habits, days=days,
                           todos=todos, today=t, cup_ml=int(setting('cup_ml', '200')))

# ── 网页动作（乔） ──
@app.route('/act/toggle', methods=['POST'])
@web_auth
def act_toggle():
    d, h = request.form['date'], int(request.form['habit'])
    with db() as c:
        cur = c.execute('select value from checks where date=? and habit_id=?', (d, h)).fetchone()
        v = 0 if (cur and cur['value']) else 1
        c.execute('insert into checks values(?,?,?) on conflict do update set value=?', (d, h, v, v))
        name = c.execute('select name from habits where id=?', (h,)).fetchone()['name']
    log_event('qiao', 'check', d, f"{name}={'✓' if v else '取消'}")
    return jsonify(ok=True, value=v)

@app.route('/act/water', methods=['POST'])
@web_auth
def act_water():
    d = request.form['date']
    delta = int(request.form.get('delta', 1))
    with db() as c:
        h = c.execute("select id from habits where type='water' and active=1").fetchone()['id']
        cur = c.execute('select value from checks where date=? and habit_id=?', (d, h)).fetchone()
        v = max(0, (cur['value'] if cur else 0) + delta)
        c.execute('insert into checks values(?,?,?) on conflict do update set value=?', (d, h, v, v))
    log_event('qiao', 'water', d, f'{v}杯')
    return jsonify(ok=True, value=v)

@app.route('/act/battery', methods=['POST'])
@web_auth
def act_battery():
    d, v = request.form['date'], int(request.form['value'])
    with db() as c:
        c.execute('insert into days(date,battery) values(?,?) '
                  'on conflict(date) do update set battery=?', (d, v, v))
    log_event('qiao', 'battery', d, str(v))
    return jsonify(ok=True)

@app.route('/act/period', methods=['POST'])
@web_auth
def act_period():
    d = request.form['date']
    with db() as c:
        cur = c.execute('select period from days where date=?', (d,)).fetchone()
        v = 0 if (cur and cur['period']) else 1
        c.execute('insert into days(date,period) values(?,?) '
                  'on conflict(date) do update set period=?', (d, v, v))
    return jsonify(ok=True, value=v)

@app.route('/act/note', methods=['POST'])
@web_auth
def act_note():
    d = request.form.get('date', '')
    kind = request.form.get('kind', 'note')
    if not d and kind != 'todo':
        d = today()
    content = request.form.get('content', '').strip()
    if not content:
        return jsonify(ok=False)
    with db() as c:
        c.execute('insert into notes(date,author,kind,content,created_at) values(?,?,?,?,?)',
                  (d, 'qiao', kind, content,
                   datetime.datetime.now().isoformat(timespec='seconds')))
    log_event('qiao', kind, d, content[:80])
    return jsonify(ok=True)

@app.route('/act/todo_toggle', methods=['POST'])
@web_auth
def act_todo_toggle():
    i = int(request.form['id'])
    with db() as c:
        cur = c.execute('select done from notes where id=?', (i,)).fetchone()
        v = 0 if cur['done'] else 1
        c.execute('update notes set done=? where id=?', (v, i))
        content = c.execute('select content from notes where id=?', (i,)).fetchone()['content']
    if v:
        log_event('qiao', 'todo_done', today(), content[:80])
    return jsonify(ok=True, done=v)

@app.route('/act/note_del', methods=['POST'])
@web_auth
def act_note_del():
    with db() as c:
        c.execute("delete from notes where id=? and author='qiao'", (int(request.form['id']),))
    return jsonify(ok=True)

@app.route('/act/upload', methods=['POST'])
@web_auth
def act_upload():
    d = request.form['date']
    slot = request.form.get('slot', 'casual')
    f = request.files['photo']
    ext = os.path.splitext(f.filename or '')[1].lower() or '.jpg'
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.heic', '.gif'):
        ext = '.jpg'
    os.makedirs(os.path.join(PHOTOS, d), exist_ok=True)
    fn = f"{slot}-{int(time.time())}{ext}"
    f.save(os.path.join(PHOTOS, d, fn))
    with db() as c:
        c.execute('insert into photos(date,slot,filename,caption,created_at) values(?,?,?,?,?)',
                  (d, slot, fn, request.form.get('caption', ''),
                   datetime.datetime.now().isoformat(timespec='seconds')))
    log_event('qiao', 'photo', d, slot)
    return redirect('/')

@app.route('/act/photo_del', methods=['POST'])
@web_auth
def act_photo_del():
    pid = int(request.form['id'])
    with db() as c:
        r = c.execute('select date,filename from photos where id=?', (pid,)).fetchone()
        if r:
            c.execute('delete from photos where id=?', (pid,))
    if r:
        try:
            os.remove(os.path.join(PHOTOS, r['date'], r['filename']))
        except OSError:
            pass
    return jsonify(ok=True)

@app.route('/act/photo_cap', methods=['POST'])
@web_auth
def act_photo_cap():
    with db() as c:
        c.execute('update photos set caption=? where id=?',
                  (request.form.get('caption', '').strip(), int(request.form['id'])))
    return jsonify(ok=True)

@app.route('/act/symptoms', methods=['POST'])
@web_auth
def act_symptoms():
    d = request.form['date']
    s = request.form.get('symptoms', '').strip()
    with db() as c:
        c.execute('insert into days(date,symptoms) values(?,?) '
                  'on conflict(date) do update set symptoms=?', (d, s, s))
    log_event('qiao', 'symptoms', d, s[:80])
    return jsonify(ok=True)

@app.route('/photo/<int:pid>')
@web_auth
def photo(pid):
    with db() as c:
        r = c.execute('select date,filename from photos where id=?', (pid,)).fetchone()
    if not r:
        abort(404)
    return send_file(os.path.join(PHOTOS, r['date'], r['filename']))

# ── 桥（克与云克） ──
@app.route('/api/day/<date>')
@api_auth
def api_day(date):
    with db() as c:
        p = day_payload(c, date)
        habits = {r['id']: dict(r) for r in c.execute('select * from habits')}
    p['habit_names'] = {i: h['name'] for i, h in habits.items()}
    return jsonify(p)

@app.route('/api/changes')
@api_auth
def api_changes():
    since = int(request.args.get('since', 0))
    with db() as c:
        rows = [dict(r) for r in c.execute(
            'select * from events where id>? order by id limit 200', (since,))]
    return jsonify(rows)

@app.route('/api/note', methods=['POST'])
@api_auth
def api_note():
    p = request.get_json(force=True)
    author = p.get('author', 'ke')
    if author not in ('ke', 'yunke'):
        author = 'ke'
    with db() as c:
        c.execute('insert into notes(date,author,kind,content,created_at) values(?,?,?,?,?)',
                  (p.get('date') or today(), author, p.get('kind', 'note'),
                   p['content'], datetime.datetime.now().isoformat(timespec='seconds')))
    return jsonify(ok=True)

@app.route('/api/photo/<int:pid>')
@api_auth
def api_photo(pid):
    with db() as c:
        r = c.execute('select date,filename from photos where id=?', (pid,)).fetchone()
    if not r:
        abort(404)
    return send_file(os.path.join(PHOTOS, r['date'], r['filename']))

@app.route('/icon.png')
def icon():
    return send_file(os.path.join(BASE, 'icon.png'))

@app.route('/api/health')
def health():
    return jsonify(ok=True, ts=datetime.datetime.now().isoformat(timespec='seconds'))

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8787)
