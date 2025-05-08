from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_apscheduler import APScheduler
import os

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
scheduler = APScheduler()
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

    # Initialize APScheduler
    if not scheduler.running:
        scheduler.init_app(app)
        scheduler.start()
        print("APScheduler started.")
    else:
        print("APScheduler already running or reinitialized.")

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
        # Temporarily comment out the user query and default admin creation during migration
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
        # pass # Add pass if the entire block is commented out and it's the only thing in the with block

    # Register scheduled tasks after app is fully initialized and blueprints are registered
    # to ensure tasks have access to app context and configurations.
    if app.config.get('SCHEDULER_API_ENABLED', False) or not app.testing: # Check if API is enabled or not in testing
        from . import tasks
        if not scheduler.get_job('distribute_points_job'):
             # Pass the app instance to the function that registers tasks if needed for context
            tasks.initialize_scheduler(app, scheduler) 
        else:
            print("'distribute_points_job' already scheduled.")

    return app 