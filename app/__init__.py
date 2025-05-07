from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
import os

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
login_manager.login_view = 'auth.login'

# 从 config 导入配置
from config import Config

def create_app(config_class=Config):
    app = Flask(__name__)
    # 从 config 对象加载配置
    app.config.from_object(config_class)
    # 移除旧的 setdefault 配置
    # app.config.setdefault('SECRET_KEY', 'your_secret_key_here')
    # app.config.setdefault('SQLALCHEMY_DATABASE_URI', 'sqlite:///novel_editor.db')
    # app.config.setdefault('SQLALCHEMY_TRACK_MODIFICATIONS', False)

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)

    from .views import main as main_blueprint
    app.register_blueprint(main_blueprint)
    from .auth import auth as auth_blueprint
    app.register_blueprint(auth_blueprint)
    from .user import user as user_blueprint
    app.register_blueprint(user_blueprint)
    from .ai_service import ai_service as ai_service_blueprint
    app.register_blueprint(ai_service_blueprint)

    from .api import api_bp as api_blueprint
    app.register_blueprint(api_blueprint)

    # 取消注释自动创建管理员的逻辑
    with app.app_context():
        from .models.user import User # 移到 app_context内部以避免循环导入问题
        if User.query.first() is None:
            print("No users found. Creating default admin user.")
            default_admin = User(
                username='admin',
                is_admin=True,
                password_change_required=True
            )
            default_admin.password = 'admin' # 使用 User 模型的 password setter
            db.session.add(default_admin)
            try:
                db.session.commit()
                print("Default admin user 'admin' created successfully.")
            except Exception as e:
                db.session.rollback()
                print(f"Error creating default admin user: {e}")

    return app 