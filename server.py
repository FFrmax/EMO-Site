import os
import sqlite3
import random
import string
import time
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, g
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'emo-platform-secret-key-2024'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, 'emo.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads', 'avatars')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
THEMES = ['dark', 'midnight', 'purple', 'amoled', 'ocean', 'sunset']
ADMIN_PASSWORD = '912999313'

online_users = {}
user_rooms = {}


def allowed_file(fn):
    return '.' in fn and fn.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db: db.close()

def query_db(q, args=(), one=False):
    cur = get_db().execute(q, args)
    rv = cur.fetchall()
    return (rv[0] if rv else None) if one else rv

def execute_db(q, args=()):
    db = get_db(); cur = db.execute(q, args); db.commit(); return cur

def generate_emo_id():
    while True:
        eid = 'EMO-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not get_db().execute("SELECT id FROM users WHERE emo_id=?", (eid,)).fetchone():
            return eid

def init_db():
    conn = sqlite3.connect(DATABASE)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            emo_id TEXT UNIQUE NOT NULL,
            avatar_color TEXT DEFAULT '#e94560',
            avatar_image TEXT DEFAULT '',
            status_text TEXT DEFAULT 'EMO kullaniyorum!',
            theme TEXT DEFAULT 'dark',
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            contact_user_id INTEGER NOT NULL,
            nickname TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (contact_user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, contact_user_id)
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_read INTEGER DEFAULT 0,
            FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (receiver_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_msg_s ON messages(sender_id);
        CREATE INDEX IF NOT EXISTS idx_msg_r ON messages(receiver_id);
        CREATE INDEX IF NOT EXISTS idx_msg_t ON messages(timestamp);
        CREATE INDEX IF NOT EXISTS idx_ct_u ON contacts(user_id);
    ''')
    for col, typ in [('avatar_image',"TEXT DEFAULT ''"),('theme',"TEXT DEFAULT 'dark'"),('is_admin','INTEGER DEFAULT 0')]:
        try: conn.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
        except: pass

    # clear non-admin users and create admin
    admin = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()
    if not admin:
        # fresh install: remove all data, create admin
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM contacts")
        conn.execute("DELETE FROM users")
        pw = generate_password_hash(ADMIN_PASSWORD)
        conn.execute(
            "INSERT INTO users (username,display_name,password_hash,emo_id,avatar_color,is_admin,status_text) VALUES (?,?,?,?,?,?,?)",
            ['admin','Admin',pw,'EMO-ADMIN','#e94560',1,'Platform Yoneticisi']
        )
    conn.commit()
    conn.close()


def login_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if 'user_id' not in session:
            if request.is_json or request.path.startswith('/api/'): return jsonify({'error':'Giris gerekli'}),401
            return redirect(url_for('login_page'))
        return f(*a,**kw)
    return dec

def admin_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if not session.get('is_admin'):
            if request.is_json or request.path.startswith('/api/'): return jsonify({'error':'Yetkisiz'}),403
            return redirect(url_for('admin_login_page'))
        return f(*a,**kw)
    return dec

def user_dict(row):
    if not row: return None
    d = dict(row); d.pop('password_hash', None); return d


# =================== PAGES ===================

@app.route('/')
def index():
    return redirect(url_for('chat_page') if 'user_id' in session else url_for('login_page'))

@app.route('/login')
def login_page():
    if 'user_id' in session: return redirect(url_for('chat_page'))
    return render_template('login.html')

@app.route('/register')
def register_page():
    if 'user_id' in session: return redirect(url_for('chat_page'))
    return render_template('register.html')

@app.route('/chat')
@login_required
def chat_page():
    user = query_db("SELECT * FROM users WHERE id=?", [session['user_id']], one=True)
    if not user: session.pop('user_id',None); return redirect(url_for('login_page'))
    return render_template('chat.html', user=user_dict(user))


# =================== AUTH API ===================

@app.route('/api/auth/register', methods=['POST'])
def api_register():
    data = request.get_json()
    username = data.get('username','').strip().lower()
    display_name = data.get('display_name','').strip()
    password = data.get('password','')
    if not username or not password or not display_name: return jsonify({'error':'Tum alanlari doldurun'}),400
    if len(username)<3: return jsonify({'error':'Kullanici adi en az 3 karakter'}),400
    if len(password)<6: return jsonify({'error':'Sifre en az 6 karakter'}),400
    if len(display_name)<2: return jsonify({'error':'Isim en az 2 karakter'}),400
    if query_db("SELECT id FROM users WHERE username=?",[username],one=True):
        return jsonify({'error':'Bu kullanici adi alinmis'}),400

    eid = generate_emo_id()
    colors = ['#e94560','#6C63FF','#00b894','#fdcb6e','#e17055','#0984e3','#00cec9','#ff7675','#a29bfe','#55efc4']
    db = get_db()
    db.execute("INSERT INTO users (username,display_name,password_hash,emo_id,avatar_color) VALUES (?,?,?,?,?)",
               [username, display_name, generate_password_hash(password), eid, random.choice(colors)])
    db.commit()
    user = query_db("SELECT * FROM users WHERE username=?",[username],one=True)
    session['user_id'] = user['id']
    return jsonify({'success':True,'emo_id':eid,'message':f'Hesap olusturuldu! EMO ID: {eid}'})

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.get_json()
    username = data.get('username','').strip().lower()
    password = data.get('password','')
    if not username or not password: return jsonify({'error':'Kullanici adi ve sifre gerekli'}),400
    user = query_db("SELECT * FROM users WHERE username=?",[username],one=True)
    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error':'Kullanici adi veya sifre hatali'}),401
    session['user_id'] = user['id']
    execute_db("UPDATE users SET last_seen=? WHERE id=?",[datetime.now().isoformat(),user['id']])
    return jsonify({'success':True,'emo_id':user['emo_id'],'user_id':user['id'],'theme':user['theme'] or 'dark'})

@app.route('/api/auth/logout', methods=['POST'])
@login_required
def api_logout():
    session.pop('user_id',None); return jsonify({'success':True})


# =================== USER API ===================

@app.route('/api/user/me')
@login_required
def get_me():
    u = query_db("SELECT id,username,display_name,emo_id,avatar_color,avatar_image,status_text,theme,created_at,last_seen FROM users WHERE id=?",[session['user_id']],one=True)
    return jsonify(dict(u))

@app.route('/api/user/me', methods=['PUT'])
@login_required
def update_me():
    data = request.get_json(); uid = session['user_id']
    if 'display_name' in data and data['display_name'].strip():
        execute_db("UPDATE users SET display_name=? WHERE id=?",[data['display_name'].strip(),uid])
    if 'status_text' in data:
        execute_db("UPDATE users SET status_text=? WHERE id=?",[data['status_text'].strip(),uid])
    if 'avatar_color' in data:
        execute_db("UPDATE users SET avatar_color=? WHERE id=?",[data['avatar_color'],uid])
    if 'theme' in data and data['theme'] in THEMES:
        execute_db("UPDATE users SET theme=? WHERE id=?",[data['theme'],uid])
    u = query_db("SELECT id,username,display_name,emo_id,avatar_color,avatar_image,status_text,theme FROM users WHERE id=?",[uid],one=True)
    return jsonify({'success':True,'user':dict(u)})

@app.route('/api/user/avatar', methods=['POST'])
@login_required
def upload_avatar():
    if 'avatar' not in request.files: return jsonify({'error':'Dosya secilmedi'}),400
    f = request.files['avatar']
    if f.filename == '': return jsonify({'error':'Dosya secilmedi'}),400
    if not allowed_file(f.filename): return jsonify({'error':'Desteklenmeyen format'}),400
    uid = session['user_id']
    ext = f.filename.rsplit('.',1)[1].lower()
    fname = f"{uid}_{int(time.time())}.{ext}"
    old = query_db("SELECT avatar_image FROM users WHERE id=?",[uid],one=True)
    if old and old['avatar_image']:
        op = os.path.join(BASE_DIR, old['avatar_image'].lstrip('/'))
        if os.path.exists(op):
            try: os.remove(op)
            except: pass
    f.save(os.path.join(UPLOAD_FOLDER, fname))
    url = f"/static/uploads/avatars/{fname}"
    execute_db("UPDATE users SET avatar_image=? WHERE id=?",[url,uid])
    return jsonify({'success':True,'avatar_url':url})

@app.route('/api/user/avatar', methods=['DELETE'])
@login_required
def delete_avatar():
    uid = session['user_id']
    old = query_db("SELECT avatar_image FROM users WHERE id=?",[uid],one=True)
    if old and old['avatar_image']:
        op = os.path.join(BASE_DIR, old['avatar_image'].lstrip('/'))
        if os.path.exists(op):
            try: os.remove(op)
            except: pass
    execute_db("UPDATE users SET avatar_image='' WHERE id=?",[uid])
    return jsonify({'success':True})

@app.route('/api/user/search')
@login_required
def search_user():
    eid = request.args.get('emo_id','').strip().upper()
    if not eid: return jsonify({'error':'EMO ID gerekli'}),400
    if not eid.startswith('EMO-'): eid = 'EMO-'+eid
    u = query_db("SELECT id,username,display_name,emo_id,avatar_color,avatar_image,status_text FROM users WHERE emo_id=?",[eid],one=True)
    if not u: return jsonify({'error':'Kullanici bulunamadi'}),404
    if u['id']==session['user_id']: return jsonify({'error':'Kendinizi ekleyemezsiniz'}),400
    r = dict(u)
    r['is_contact'] = query_db("SELECT id FROM contacts WHERE user_id=? AND contact_user_id=?",[session['user_id'],u['id']],one=True) is not None
    r['is_online'] = u['id'] in online_users
    return jsonify(r)


# =================== CONTACTS API ===================

@app.route('/api/contacts')
@login_required
def get_contacts():
    uid = session['user_id']
    saved = query_db('''
        SELECT c.id as contact_id,c.nickname,c.added_at,
               u.id as user_id,u.username,u.display_name,u.emo_id,
               u.avatar_color,u.avatar_image,u.status_text,u.last_seen,
               (SELECT content FROM messages WHERE (sender_id=? AND receiver_id=u.id) OR (sender_id=u.id AND receiver_id=?) ORDER BY timestamp DESC LIMIT 1) as last_message,
               (SELECT timestamp FROM messages WHERE (sender_id=? AND receiver_id=u.id) OR (sender_id=u.id AND receiver_id=?) ORDER BY timestamp DESC LIMIT 1) as last_message_time,
               (SELECT COUNT(*) FROM messages WHERE sender_id=u.id AND receiver_id=? AND is_read=0) as unread_count
        FROM contacts c JOIN users u ON c.contact_user_id=u.id
        WHERE c.user_id=?
        ORDER BY last_message_time DESC NULLS LAST, c.added_at DESC
    ''', [uid]*6)

    saved_ids = set()
    saved_list = []
    for c in saved:
        saved_ids.add(c['user_id'])
        saved_list.append({
            'contact_id':c['contact_id'],'user_id':c['user_id'],'username':c['username'],
            'display_name':c['nickname'] or c['display_name'],'real_name':c['display_name'],
            'nickname':c['nickname'],'emo_id':c['emo_id'],'avatar_color':c['avatar_color'],
            'avatar_image':c['avatar_image'] or '','status_text':c['status_text'],
            'last_seen':c['last_seen'],'is_online':c['user_id'] in online_users,'is_saved':True,
            'last_message':c['last_message'],'last_message_time':c['last_message_time'],
            'unread_count':c['unread_count'],'added_at':c['added_at']
        })

    unsaved = query_db('''
        SELECT DISTINCT u.id as user_id,u.username,u.display_name,u.emo_id,
               u.avatar_color,u.avatar_image,u.status_text,u.last_seen,
               (SELECT content FROM messages WHERE (sender_id=? AND receiver_id=u.id) OR (sender_id=u.id AND receiver_id=?) ORDER BY timestamp DESC LIMIT 1) as last_message,
               (SELECT timestamp FROM messages WHERE (sender_id=? AND receiver_id=u.id) OR (sender_id=u.id AND receiver_id=?) ORDER BY timestamp DESC LIMIT 1) as last_message_time,
               (SELECT COUNT(*) FROM messages WHERE sender_id=u.id AND receiver_id=? AND is_read=0) as unread_count
        FROM messages m JOIN users u ON (CASE WHEN m.sender_id=? THEN m.receiver_id ELSE m.sender_id END)=u.id
        WHERE (m.sender_id=? OR m.receiver_id=?) AND u.id NOT IN (SELECT contact_user_id FROM contacts WHERE user_id=?) AND u.id!=?
        ORDER BY last_message_time DESC
    ''', [uid]*10)

    unsaved_list = []
    for u in unsaved:
        if u['user_id'] not in saved_ids:
            unsaved_list.append({
                'contact_id':None,'user_id':u['user_id'],'username':u['username'],
                'display_name':u['display_name'],'real_name':u['display_name'],
                'nickname':None,'emo_id':u['emo_id'],'avatar_color':u['avatar_color'],
                'avatar_image':u['avatar_image'] or '','status_text':u['status_text'],
                'last_seen':u['last_seen'],'is_online':u['user_id'] in online_users,'is_saved':False,
                'last_message':u['last_message'],'last_message_time':u['last_message_time'],
                'unread_count':u['unread_count'],'added_at':None
            })
    return jsonify({'saved':saved_list,'unsaved':unsaved_list})

@app.route('/api/contacts', methods=['POST'])
@login_required
def add_contact():
    data = request.get_json()
    eid = data.get('emo_id','').strip().upper()
    nick = data.get('nickname','').strip() or None
    tuid = data.get('user_id')
    uid = session['user_id']
    if tuid:
        t = query_db("SELECT id,display_name,emo_id FROM users WHERE id=?",[tuid],one=True)
    else:
        if not eid: return jsonify({'error':'EMO ID gerekli'}),400
        if not eid.startswith('EMO-'): eid='EMO-'+eid
        t = query_db("SELECT id,display_name,emo_id FROM users WHERE emo_id=?",[eid],one=True)
    if not t: return jsonify({'error':'Kullanici bulunamadi'}),404
    if t['id']==uid: return jsonify({'error':'Kendinizi ekleyemezsiniz'}),400
    if query_db("SELECT id FROM contacts WHERE user_id=? AND contact_user_id=?",[uid,t['id']],one=True):
        return jsonify({'error':'Zaten kayitli'}),400
    execute_db("INSERT INTO contacts (user_id,contact_user_id,nickname) VALUES (?,?,?)",[uid,t['id'],nick])
    return jsonify({'success':True,'message':f'{t["display_name"]} eklendi'})

@app.route('/api/contacts/<int:cid>', methods=['DELETE'])
@login_required
def delete_contact(cid):
    execute_db("DELETE FROM contacts WHERE id=? AND user_id=?",[cid,session['user_id']])
    return jsonify({'success':True})

@app.route('/api/contacts/<int:cuid>/nickname', methods=['PUT'])
@login_required
def update_nickname(cuid):
    nick = request.get_json().get('nickname','').strip() or None
    execute_db("UPDATE contacts SET nickname=? WHERE user_id=? AND contact_user_id=?",[nick,session['user_id'],cuid])
    return jsonify({'success':True})


# =================== MESSAGES API ===================

@app.route('/api/messages/<int:oid>')
@login_required
def get_messages(oid):
    uid = session['user_id']
    page = request.args.get('page',1,type=int)
    pp = 50; off = (page-1)*pp
    msgs = query_db('''
        SELECT m.id,m.sender_id,m.receiver_id,m.content,m.timestamp,m.is_read,
               u.display_name as sender_name,u.avatar_color as sender_color,u.avatar_image as sender_image
        FROM messages m JOIN users u ON m.sender_id=u.id
        WHERE (m.sender_id=? AND m.receiver_id=?) OR (m.sender_id=? AND m.receiver_id=?)
        ORDER BY m.timestamp DESC LIMIT ? OFFSET ?
    ''', [uid,oid,oid,uid,pp,off])
    execute_db("UPDATE messages SET is_read=1 WHERE sender_id=? AND receiver_id=? AND is_read=0",[oid,uid])
    r = [dict(m) for m in msgs]; r.reverse()
    tot = query_db("SELECT COUNT(*) as c FROM messages WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)",[uid,oid,oid,uid],one=True)['c']
    return jsonify({'messages':r,'total':tot,'page':page,'has_more':tot>page*pp})


# =================== ADMIN ===================

@app.route('/admin')
def admin_login_page():
    if session.get('is_admin'): return redirect(url_for('admin_panel'))
    return render_template('admin_login.html')

@app.route('/admin/panel')
@admin_required
def admin_panel():
    return render_template('admin.html')

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    pw = request.get_json().get('password','')
    if pw == ADMIN_PASSWORD:
        session['is_admin'] = True
        return jsonify({'success':True})
    return jsonify({'error':'Yanlis sifre'}),401

@app.route('/api/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('is_admin',None); return jsonify({'success':True})

@app.route('/api/admin/stats')
@admin_required
def admin_stats():
    return jsonify({
        'total_users': query_db("SELECT COUNT(*) as c FROM users",one=True)['c'],
        'total_messages': query_db("SELECT COUNT(*) as c FROM messages",one=True)['c'],
        'total_contacts': query_db("SELECT COUNT(*) as c FROM contacts",one=True)['c'],
        'online_count': len(online_users)
    })

@app.route('/api/admin/users')
@admin_required
def admin_get_users():
    users = query_db("SELECT * FROM users ORDER BY id")
    result = []
    for u in users:
        d = dict(u)
        d['is_online'] = u['id'] in online_users
        d['message_count'] = query_db("SELECT COUNT(*) as c FROM messages WHERE sender_id=? OR receiver_id=?",[u['id'],u['id']],one=True)['c']
        d['contact_count'] = query_db("SELECT COUNT(*) as c FROM contacts WHERE user_id=?",[u['id']],one=True)['c']
        result.append(d)
    return jsonify(result)

@app.route('/api/admin/users/<int:uid>')
@admin_required
def admin_get_user(uid):
    u = query_db("SELECT * FROM users WHERE id=?",[uid],one=True)
    if not u: return jsonify({'error':'Bulunamadi'}),404
    d = dict(u); d['is_online'] = uid in online_users
    d['message_count'] = query_db("SELECT COUNT(*) as c FROM messages WHERE sender_id=? OR receiver_id=?",[uid,uid],one=True)['c']
    d['contact_count'] = query_db("SELECT COUNT(*) as c FROM contacts WHERE user_id=?",[uid],one=True)['c']
    d['contacts'] = [dict(c) for c in query_db("SELECT c.*,u.display_name as contact_name,u.emo_id as contact_emo_id FROM contacts c JOIN users u ON c.contact_user_id=u.id WHERE c.user_id=?",[uid])]
    return jsonify(d)

@app.route('/api/admin/users/<int:uid>', methods=['PUT'])
@admin_required
def admin_update_user(uid):
    data = request.get_json()
    for field in ['display_name','status_text','username','avatar_color']:
        if field in data and data[field]:
            execute_db(f"UPDATE users SET {field}=? WHERE id=?",[data[field],uid])
    if 'password' in data and data['password']:
        execute_db("UPDATE users SET password_hash=? WHERE id=?",[generate_password_hash(data['password']),uid])
    return jsonify({'success':True})

@app.route('/api/admin/users/<int:uid>', methods=['DELETE'])
@admin_required
def admin_delete_user(uid):
    u = query_db("SELECT is_admin FROM users WHERE id=?",[uid],one=True)
    if u and u['is_admin']: return jsonify({'error':'Admin silinemez'}),400
    execute_db("DELETE FROM messages WHERE sender_id=? OR receiver_id=?",[uid,uid])
    execute_db("DELETE FROM contacts WHERE user_id=? OR contact_user_id=?",[uid,uid])
    execute_db("DELETE FROM users WHERE id=?",[uid])
    return jsonify({'success':True})

@app.route('/api/admin/users/<int:uid>/messages')
@admin_required
def admin_user_messages(uid):
    wid = request.args.get('with_user',type=int)
    if wid:
        msgs = query_db('''
            SELECT m.*,s.display_name as sender_name,r.display_name as receiver_name
            FROM messages m JOIN users s ON m.sender_id=s.id JOIN users r ON m.receiver_id=r.id
            WHERE (m.sender_id=? AND m.receiver_id=?) OR (m.sender_id=? AND m.receiver_id=?)
            ORDER BY m.timestamp DESC LIMIT 200
        ''',[uid,wid,wid,uid])
    else:
        msgs = query_db('''
            SELECT m.*,s.display_name as sender_name,r.display_name as receiver_name
            FROM messages m JOIN users s ON m.sender_id=s.id JOIN users r ON m.receiver_id=r.id
            WHERE m.sender_id=? OR m.receiver_id=?
            ORDER BY m.timestamp DESC LIMIT 200
        ''',[uid,uid])
    partners = query_db('''
        SELECT DISTINCT u.id,u.display_name,u.emo_id,u.avatar_color,
            (SELECT COUNT(*) FROM messages WHERE (sender_id=? AND receiver_id=u.id) OR (sender_id=u.id AND receiver_id=?)) as msg_count
        FROM messages m
        JOIN users u ON (CASE WHEN m.sender_id=? THEN m.receiver_id ELSE m.sender_id END)=u.id
        WHERE (m.sender_id=? OR m.receiver_id=?) AND u.id!=?
    ''',[uid,uid,uid,uid,uid,uid])
    return jsonify({'messages':[dict(m) for m in msgs],'partners':[dict(p) for p in partners]})

@app.route('/api/admin/users/<int:uid>/login', methods=['POST'])
@admin_required
def admin_login_as(uid):
    u = query_db("SELECT id FROM users WHERE id=?",[uid],one=True)
    if not u: return jsonify({'error':'Bulunamadi'}),404
    session['user_id'] = uid
    return jsonify({'success':True,'redirect':'/chat'})

@app.route('/api/admin/messages/<int:mid>', methods=['DELETE'])
@admin_required
def admin_delete_message(mid):
    execute_db("DELETE FROM messages WHERE id=?",[mid])
    return jsonify({'success':True})


# =================== SOCKETIO ===================

@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        uid = session['user_id']
        online_users[uid] = request.sid; user_rooms[request.sid] = uid
        join_room(f'user_{uid}')
        conn = sqlite3.connect(DATABASE)
        conn.execute("UPDATE users SET last_seen=? WHERE id=?",[datetime.now().isoformat(),uid])
        conn.commit(); conn.close()
        emit('user_status',{'user_id':uid,'is_online':True},broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in user_rooms:
        uid = user_rooms[sid]; online_users.pop(uid,None); user_rooms.pop(sid,None)
        conn = sqlite3.connect(DATABASE)
        conn.execute("UPDATE users SET last_seen=? WHERE id=?",[datetime.now().isoformat(),uid])
        conn.commit(); conn.close()
        emit('user_status',{'user_id':uid,'is_online':False},broadcast=True)

@socketio.on('send_message')
def handle_send_message(data):
    if 'user_id' not in session: return
    sid = session['user_id']; rid = data.get('receiver_id'); content = data.get('content','').strip()
    if not rid or not content: return
    ts = datetime.now().isoformat()
    conn = sqlite3.connect(DATABASE); conn.row_factory = sqlite3.Row
    cur = conn.execute("INSERT INTO messages (sender_id,receiver_id,content,timestamp) VALUES (?,?,?,?)",[sid,rid,content,ts])
    mid = cur.lastrowid
    s = conn.execute("SELECT display_name,avatar_color,avatar_image FROM users WHERE id=?",[sid]).fetchone()
    conn.commit(); conn.close()
    msg = {'id':mid,'sender_id':sid,'receiver_id':rid,'content':content,'timestamp':ts,'is_read':0,
           'sender_name':s['display_name'],'sender_color':s['avatar_color'],'sender_image':s['avatar_image'] or ''}
    emit('new_message',msg,room=f'user_{sid}'); emit('new_message',msg,room=f'user_{rid}')

@socketio.on('typing')
def handle_typing(data):
    if 'user_id' not in session: return
    rid = data.get('receiver_id')
    if rid: emit('user_typing',{'user_id':session['user_id']},room=f'user_{rid}')

@socketio.on('stop_typing')
def handle_stop_typing(data):
    if 'user_id' not in session: return
    rid = data.get('receiver_id')
    if rid: emit('user_stop_typing',{'user_id':session['user_id']},room=f'user_{rid}')

@socketio.on('mark_read')
def handle_mark_read(data):
    if 'user_id' not in session: return
    sid = data.get('sender_id'); uid = session['user_id']
    conn = sqlite3.connect(DATABASE)
    conn.execute("UPDATE messages SET is_read=1 WHERE sender_id=? AND receiver_id=? AND is_read=0",[sid,uid])
    conn.commit(); conn.close()
    emit('messages_read',{'reader_id':uid},room=f'user_{sid}')


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port) # debug=True kısmını tamamen SİL
