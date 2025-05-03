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

    # 不再需要在这里调用 db.create_all()，迁移工具会管理表的创建和更新
    # with app.app_context():
    #     try:
    #         from .models.user import User
    #     except ImportError:
    #         print("Warning: User model not found, cannot create initial admin.")
    #         User = None 
    #     try:
    #         from .models.filesystem import FileSystemItem
    #     except ImportError:
    #         print("Warning: FileSystemItem model not found.")
    #         FileSystemItem = None
    #     db.create_all()
        
    # 创建初始管理员的逻辑可以保留，但最好放在迁移之后或单独的 CLI 命令中
    # 确保在尝试查询 User 前，表已通过迁移创建
    # with app.app_context(): 
    #    if User: 
    #        admin_user = User.query.filter_by(username='admin').first()
    #        if not admin_user: 
    #            # ... 创建管理员代码 ...
    # 为了安全起见，暂时注释掉自动创建管理员的逻辑
    # 建议之后创建一个 flask db seed 命令来添加初始数据
    pass

    return app 