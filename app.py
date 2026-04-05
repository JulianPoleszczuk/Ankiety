from flask import Flask, render_template, request, redirect, Response, session
from flask_sqlalchemy import SQLAlchemy
import json
import csv
from io import StringIO
from datetime import datetime
import uuid
from sqlalchemy import func

import pyreadstat
import pandas as pd

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
    question_id = db.Column(db.Integer)
    row_id = db.Column(db.String(100))
    answer = db.Column(db.Text)
    time_spent = db.Column(db.Float)
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
        <input type="password" name="password">
        <button>Zaloguj</button>
    </form>
    '''

# --- INDEX ---

@app.route('/', methods=['GET', 'POST'])
def index():

    ip = request.remote_addr
    if not Visit.query.filter_by(ip=ip).first():
        db.session.add(Visit(ip=ip))
        db.session.commit()

    if request.method == 'POST':
        session_id = str(uuid.uuid4())
        form = request.form.to_dict()

        for key, value in form.items():

            if key.endswith("_time"):
                continue

            try:
                parts = key.split('_')
                q_id = int(parts[1])
                row_id = parts[2] if len(parts) > 2 else None

                time_spent = float(form.get(key + "_time", 0))

                # 🔥 płeć jako 0/1
                if value.lower() in ["kobieta", "k"]:
                    value = "0"
                elif value.lower() in ["mężczyzna", "mezczyzna", "m"]:
                    value = "1"

                db.session.add(Result(
                    session_id=session_id,
                    question_id=q_id,
                    row_id=row_id,
                    answer=value,
                    time_spent=time_spent
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
            options = [o.strip() for o in request.form.get('options').split(',')]
            db.session.add(Question(
                q_type='radio',
                content=json.dumps({
                    "question": request.form.get('question'),
                    "options": options
                })
            ))

        elif action == 'add_matrix':
            rows = []
            for i, r in enumerate(request.form.get('rows').split(','), start=1):
                rows.append({
                    "id": f"c{i}",
                    "label": r.strip()
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

        elif action == 'add_open':
            db.session.add(Question(
                q_type='open',
                content=json.dumps({
                    "question": request.form.get('question')
                })
            ))

        elif action == 'delete':
            q_id = request.form.get('q_id')
            Question.query.filter_by(id=q_id).delete()
            Result.query.filter_by(question_id=q_id).delete()

        db.session.commit()
        return redirect('/admin')

    questions = [{
        'id': q.id,
        'q_type': q.q_type,
        'data': json.loads(q.content)
    } for q in Question.query.all()]

    count = Result.query.count()
    visits = Visit.query.count()
    users = db.session.query(Result.session_id).distinct().count()

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

# --- EXPORT SAV ---

@app.route('/export_sav')
def export_sav():
    results = Result.query.all()
    data = {}

    for r in results:
        sid = r.session_id
        if sid not in data:
            data[sid] = {}

        key = r.row_id if r.row_id else f"q{r.question_id}"

        try:
            data[sid][key] = float(r.answer)
        except:
            data[sid][key] = r.answer

        data[sid][key + "_time"] = r.time_spent

    df = pd.DataFrame.from_dict(data, orient='index')
    df.reset_index(inplace=True)
    df.rename(columns={"index": "session"}, inplace=True)

    pyreadstat.write_sav(df, "export.sav")

    return Response(
        open("export.sav", "rb"),
        mimetype="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=ankieta.sav"}
    )

# --- EXPORT CSV ---

@app.route('/export_spss')
def export_spss():
    results = Result.query.all()
    data = {}

    for r in results:
        sid = r.session_id
        if sid not in data:
            data[sid] = {}

        key = r.row_id if r.row_id else f"q{r.question_id}"
        data[sid][key] = r.answer

    columns = ['session'] + sorted({k for v in data.values() for k in v})

    si = StringIO()
    cw = csv.writer(si, delimiter=';')
    cw.writerow(columns)

    for sid, v in data.items():
        cw.writerow([sid] + [v.get(k, '') for k in columns[1:]])

    return Response(
        '\ufeff' + si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=spss.csv"}
    )

if __name__ == '__main__':
    app.run(debug=True)