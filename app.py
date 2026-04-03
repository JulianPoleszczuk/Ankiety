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
    question_id = db.Column(db.Integer, nullable=False)
    row_id = db.Column(db.String(100), nullable=True)
    answer = db.Column(db.Text, nullable=False)
    time_spent = db.Column(db.Float, nullable=True)  # ⏱️ NOWE
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

    ip = request.remote_addr
    if not Visit.query.filter_by(ip=ip).first():
        db.session.add(Visit(ip=ip))
        db.session.commit()

    if request.method == 'POST':
        session_id = str(uuid.uuid4())
        form_data = request.form.to_dict()

        for key, value in form_data.items():

            if key.endswith("_time"):
                continue

            try:
                parts = key.split('_')
                q_id = int(parts[1])
                row_id = parts[2] if len(parts) > 2 else None

                time_key = key + "_time"
                time_spent = float(form_data.get(time_key, 0))

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

        elif action == 'edit':
            q_id = int(request.form.get('q_id'))
            q = Question.query.get(q_id)

            if q:
                if q.q_type == 'radio':
                    options = [o.strip() for o in request.form.get('options').split(',') if o.strip()]
                    q.content = json.dumps({
                        "question": request.form.get('question'),
                        "options": options
                    })

                elif q.q_type == 'matrix':
                    rows = []
                    for r in request.form.get('rows').split(','):
                        r = r.strip()
                        if r:
                            rows.append({
                                "id": r.lower().replace(" ", "_"),
                                "label": r
                            })

                    q.content = json.dumps({
                        "question": request.form.get('question'),
                        "left_label": request.form.get('left_label'),
                        "right_label": request.form.get('right_label'),
                        "scale_points": int(request.form.get('scale', 7)),
                        "rows": rows
                    })

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
    questions = Question.query.all()

    questions_map = {q.id: json.loads(q.content)["question"] for q in questions}

    data = {}

    for r in results:
        sid = r.session_id
        if sid not in data:
            data[sid] = {}

        if r.row_id:
            key = f"{questions_map.get(r.question_id)}_{r.row_id}"
            data[sid][key] = int(r.answer)

            # czas
            data[sid][key + "_time"] = r.time_spent

        else:
            key = questions_map.get(r.question_id)

            # płeć binarnie
            if key and key.lower() == "płeć":
                if r.answer.lower() in ["kobieta", "k"]:
                    data[sid][key] = 0
                elif r.answer.lower() in ["mężczyzna", "mezczyzna", "m"]:
                    data[sid][key] = 1
                else:
                    data[sid][key] = None
            else:
                data[sid][key] = r.answer

            data[sid][key + "_time"] = r.time_spent

    df = pd.DataFrame.from_dict(data, orient='index')
    df.reset_index(inplace=True)
    df.rename(columns={"index": "session"}, inplace=True)

    file_path = "export.sav"
    pyreadstat.write_sav(df, file_path)

    return Response(
        open(file_path, "rb"),
        mimetype="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=ankieta.sav"}
    )

# --- EXPORT SPSS CSV (zostaje) ---

@app.route('/export_spss')
def export_spss():
    results = Result.query.all()
    questions_map = {q.id: json.loads(q.content)["question"] for q in Question.query.all()}

    data = {}

    for r in results:
        sid = r.session_id
        if sid not in data:
            data[sid] = {}

        if r.row_id:
            key = f"{questions_map.get(r.question_id)}_{r.row_id}"
            data[sid][key] = r.answer
        else:
            key = questions_map.get(r.question_id)
            data[sid][key] = r.answer

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

if __name__ == '__main__':
    app.run(debug=True)