"""Backend for Goones Quiz"""

import json
import os
import datetime
from flask import Flask
from flask import render_template, request, redirect, url_for, session
from authlib.integrations.flask_client import OAuth

from werkzeug.middleware.proxy_fix import ProxyFix

from sqlalchemy import create_engine, Column, String, JSON
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import scoped_session, sessionmaker, declarative_base

engine = create_engine(os.getenv('DB_URL', 'sqlite:///./db.sqlite'), connect_args={'check_same_thread': False})
db_session = scoped_session(sessionmaker(autocommit=False,
                                         autoflush=False,
                                         bind=engine))

Base = declarative_base()
Base.query = db_session.query_property()

db_session = scoped_session(sessionmaker(autocommit=False,
                                         autoflush=False,
                                         bind=engine))

app = Flask(__name__)

app.secret_key = os.getenv("FLASK_SECRET_KEY")
google_client_id = os.getenv("GOOGLE_CLIENT_ID")
google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
if google_client_id is None or google_client_secret is None or app.secret_key is None:
    raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set, and FLASK_SECRET_KEY must be set.")

oauth = OAuth(app)
google = oauth.register(
    'google',
    client_id=google_client_id,
    client_secret=google_client_secret,
    access_token_url='https://accounts.google.com/o/oauth2/token',
    access_token_params=None,
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    authorize_params=None,
    api_base_url='https://www.googleapis.com/oauth2/v1/',
    userinfo_endpoint='https://openidconnect.googleapis.com/v1/userinfo',
    client_kwargs={'scope': 'email profile'},
)

app.wsgi_app = ProxyFix(
    app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
)

# Quiz database file
QUIZ_DB = "quiz.json"

class Quiz(Base):
    """
    Quiz model.
    """
    __tablename__ = 'quizzes'
    id = Column(String(10), primary_key=True)
    title = Column(String(50))
    description = Column(String(200))
    questions = Column(MutableList.as_mutable(JSON))
    answers = Column(MutableList.as_mutable(JSON))

    def __repr__(self):
        return f"<Quiz {self.title}>"

def init_db():
    """
    Initialize the database.
    :return: None
    """
    Base.metadata.create_all(bind=engine)
    with open(QUIZ_DB, 'r') as f:
        quizzes = json.load(f)
        for id, quiz in quizzes.items():
            # Check if quiz already exists
            existing_quiz = db_session.query(Quiz).filter(Quiz.id == id).first()
            if existing_quiz is not None:
                # Update existing quiz
                existing_quiz.title = quiz['title']
                existing_quiz.description = quiz['description']
                existing_quiz.questions = quiz['questions']
            else:
                q = Quiz(id=id, title=quiz['title'], description=quiz['description'], questions=quiz['questions'], answers=[])
                db_session.add(q)
        db_session.commit()
    print("Database initialized.")

@app.teardown_appcontext
def shutdown_session(exception=None):
    db_session.remove()

def quizzes():
    """
    Get all quizzes.
    :return: List of quizzes
    """

    # From sql
    return db_session.query(Quiz).all()

def get_quiz(id: str):
    """
    Get quiz by id.
    :param id: Quiz id
    :return: Quiz data
    """

    # From sql
    quiz = db_session.query(Quiz).filter(Quiz.id == id).first()
    return quiz

@app.route('/quiz')
@app.route('/quiz/')
def quiz_list():
    """
    Render the quiz list page.
    :return: Rendered HTML page.
    """

    return redirect(url_for('index'))

@app.route('/')
def index():
    """
    Render the index page.
    :return: Rendered HTML page.
    """

    return render_template('index.html.j2', quizzes=quizzes())

@app.route('/quiz/<id>', methods=['GET', 'POST'])
def quiz(id: str):
    """
    Render the quiz page.
    :return: Rendered HTML page.
    """

    quiz = get_quiz(id)
    if quiz is None:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        if 'user' not in session:
            return redirect(url_for('index'))
        answers = {}
        correct = []
        for q,a in request.form.items():
            if q.startswith("q"):
                qid = q[1:]
                try:
                    qid = int(qid)
                except ValueError:
                    return redirect(url_for('index'))
                qid -= 1
                if qid < 0 or qid >= len(quiz.questions):
                    return redirect(url_for('index'))
                if quiz.questions[qid]["answer"] == a:
                    correct.append(qid)
                answers[qid] = a
        # Save answers to db
        quiz = db_session.query(Quiz).filter(Quiz.id == id).first()
        if quiz.answers is None:
            quiz.answers = []
        ans = quiz.answers
        ans.append({
            "user": session['user'],
            "answers": answers,
            "score": len(correct),
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        quiz.answers = ans
        print(quiz.answers)
        db_session.flush()
        db_session.commit()
        return render_template('quiz.html.j2', id=id, quiz=quiz, answers=answers, correct=correct, total=len(quiz.questions))
    else:
        return render_template('quiz.html.j2', id=id, quiz=quiz, answers={})

@app.route('/quiz/<id>/answers')
def quiz_answers(id: str):
    """
    Render the quiz answers page.
    :return: Rendered HTML page.
    """

    quiz = db_session.query(Quiz).filter(Quiz.id == id).first()
    if quiz is None:
        return redirect(url_for('index'))
    if 'user' not in session:
        return redirect(url_for('index'))
    if session['user']['id'] not in os.environ.get("ADMIN_IDS", "").split(","):
        return redirect(url_for('index'))
    
    print(quiz.answers)
    print({q['user']['id']: q['user'] for q in quiz.answers})

    return render_template('quiz_answers.html.j2', id=id, quiz=get_quiz(id), answers=quiz.answers, users={q['user']['id']: q['user'] for q in quiz.answers})

@app.route('/oauth/login')
def login():
    """
    Login with Google.
    :return: Redirect to Google login page.
    """

    session['next'] = request.args.get('next')
    return google.authorize_redirect(url_for('authorized', _external=True, _scheme='https'))

@app.route('/oauth/callback')
def authorized():
    """
    Callback for Google login.
    :return: Redirect to index page.
    """

    _token = google.authorize_access_token()
    resp = google.get('userinfo')
    user_info = resp.json()

    # Store user info in session
    session['user'] = user_info
    print(user_info)

    next_url = session.pop('next', '/')
    return redirect(next_url)

@app.route('/oauth/logout')
def logout():
    """
    Logout from Google.
    :return: Redirect to index page.
    """

    session.pop('user', None)
    return redirect(url_for('index'))

@app.route("/favicon.ico")
def favicon():
    """
    Favicon.
    :return: Favicon file.
    """

    return redirect(url_for('static', filename='favicon.ico'), code=301)

init_db()
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8000, debug=True)