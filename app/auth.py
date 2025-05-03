from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, current_user
from . import db, login_manager
from .models.user import User

auth = Blueprint('auth', __name__)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = request.form.get('remember') == 'on'

        user = User.query.filter_by(username=username).first()
        
        if user and user.verify_password(password):
            login_user(user, remember=remember)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('main.index'))
        else:
            flash('用户名或密码错误')
            
    return render_template('login.html')

@auth.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('用户名和密码不能为空')
            return render_template('register.html')
            
        if User.query.filter_by(username=username).first():
            flash('用户名已存在')
        else:
            user = User(username=username, is_admin=False)
            user.password = password
            db.session.add(user)
            db.session.commit()
            flash('注册成功，请登录')
            return redirect(url_for('auth.login'))
            
    return render_template('register.html') 