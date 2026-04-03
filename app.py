from flask import Flask, render_template, request, redirect, url_for, Response, session
from flask_sqlalchemy import SQLAlchemy
import json
import csv
from io import StringIO
from datetime import datetime
import uuid
from sqlalchemy import func

app = Flask(__name__)
app.secret_key = "secret123"

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///./ankiety.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

ADMIN_PASSWORD = "admin123"

# --- MODELE ---

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    q_type = db.Column(db.String(50))
    content = db.Column(db.Text)

class Result(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(36))
    question_id = db.Column(db.Integer, nullable=False)
    row_id = db.Column(db.String(100), nullable=True)
    answer = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Visit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(100))

with app.app_context():
    db.create_all()

# --- LOGIN ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect('/admin')
    return '''
    <form method="POST" style="text-align:center;margin-top:100px;">
        <input type="password" name="password" placeholder="Hasło">
        <button>Zaloguj</button>
    </form>
    '''

# --- STRONA GŁÓWNA ---

@app.route('/', methods=['GET', 'POST'])
def index():

    # liczenie odwiedzin
    ip = request.remote_addr
    if not Visit.query.filter_by(ip=ip).first():
        db.session.add(Visit(ip=ip))
        db.session.commit()

    if request.method == 'POST':
        session_id = str(uuid.uuid4())

        for key, value in request.form.items():
            try:
                parts = key.split('_')
                q_id = int(parts[1])
                row_id = parts[2] if len(parts) > 2 else None

                db.session.add(Result(
                    session_id=session_id,
                    question_id=q_id,
                    row_id=row_id,
                    answer=value
                ))
            except:
                continue

        db.session.commit()
        return redirect('/thanks')

    questions = []
    for q in Question.query.all():
        questions.append({
            'id': q.id,
            'q_type': q.q_type,
            'data': json.loads(q.content)
        })

    return render_template('index.html', questions=questions)


@app.route('/thanks')
def thanks():
    return render_template('thanks.html')

# --- ADMIN ---

@app.route('/admin', methods=['GET', 'POST'])
def admin():

    if not session.get('logged_in'):
        return redirect('/login')

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_radio':
            options = [o.strip() for o in request.form.get('options').split(',') if o.strip()]
            db.session.add(Question(
                q_type='radio',
                content=json.dumps({
                    "question": request.form.get('question'),
                    "options": options
                })
            ))

        elif action == 'add_matrix':
            rows = []
            for r in request.form.get('rows').split(','):
                r = r.strip()
                if r:
                    rows.append({
                        "id": r.lower().replace(" ", "_"),
                        "label": r
                    })

            db.session.add(Question(
                q_type='matrix',
                content=json.dumps({
                    "question": request.form.get('question'),
                    "left_label": request.form.get('left_label'),
                    "right_label": request.form.get('right_label'),
                    "scale_points": int(request.form.get('scale', 7)),
                    "rows": rows
                })
            ))

        elif action == 'delete':
            q_id = request.form.get('q_id')
            Question.query.filter_by(id=q_id).delete()
            Result.query.filter_by(question_id=q_id).delete()

        db.session.commit()
        return redirect('/admin')

    questions = []
    for q in Question.query.all():
        questions.append({
            'id': q.id,
            'q_type': q.q_type,
            'data': json.loads(q.content)
        })

    count = Result.query.count()
    visits = Visit.query.count()
    users = db.session.query(Result.session_id).distinct().count()

    # średnie
    averages = db.session.query(
        Result.row_id,
        func.avg(Result.answer)
    ).filter(Result.row_id != None).group_by(Result.row_id).all()

    labels = [a[0] for a in averages]
    values = [round(a[1], 2) for a in averages]

    return render_template(
        'admin.html',
        questions=questions,
        count=count,
        visits=visits,
        users=users,
        labels=labels,
        values=values
    )

# --- EXPORT EXCEL ---

@app.route('/export_excel')
def export_excel():
    results = Result.query.all()
    si = StringIO()
    cw = csv.writer(si, delimiter=';')

    cw.writerow(['id','session','question_id','row_id','answer','timestamp'])

    for r in results:
        cw.writerow([r.id, r.session_id, r.question_id, r.row_id, r.answer, r.timestamp])

    return Response('\ufeff'+si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=excel.csv"})

# --- EXPORT SPSS ---

@app.route('/export_spss')
def export_spss():
    results = Result.query.all()
    data = {}

    for r in results:
        sid = r.session_id
        if sid not in data:
            data[sid] = {}

        if r.row_id:
            try:
                data[sid][r.row_id] = int(r.answer)
            except:
                data[sid][r.row_id] = r.answer
        else:
            data[sid]['wiek'] = r.answer

    keys = set()
    for v in data.values():
        keys.update(v.keys())

    columns = ['session'] + sorted(keys)

    si = StringIO()
    cw = csv.writer(si, delimiter=';')
    cw.writerow(columns)

    for sid, v in data.items():
        row = [sid]
        for k in columns[1:]:
            row.append(v.get(k, ''))
        cw.writerow(row)

    return Response('\ufeff'+si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=spss.csv"})

# --- INIT DB ---

@app.route('/init_db')
def init_db():
    if not Question.query.first():

        db.session.add(Question(q_type='radio', content=json.dumps({
            "question": "Wiek",
            "options": ["15-18","19-24","25-34","35-44","45-54","55-60","60+"]
        })))

        db.session.add(Question(q_type='matrix', content=json.dumps({
            "question": "Oceń cechy",
            "left_label": "Dziewczyny",
            "right_label": "Chłopaki",
            "scale_points": 8,
            "rows": [
                {"id":"spojnosc","label":"Spójność"},
                {"id":"empatia","label":"Empatia"},
                {"id":"humor","label":"Humor"},
                {"id":"ambicja","label":"Ambicja"}
            ]
        })))

        db.session.commit()
        return "OK"

    return "Już jest"

if __name__ == '__main__':
    app.run(debug=True)