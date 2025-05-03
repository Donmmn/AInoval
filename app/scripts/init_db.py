import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from app import create_app, db
from app.models import *
from app.models.user import User
from werkzeug.security import generate_password_hash

app = create_app()

with app.app_context():
    db.create_all()
    # 检查是否有管理员账号
    if not User.query.filter_by(is_admin=True).first():
        admin = User(username='admin', password_hash=generate_password_hash('admin'), is_admin=True)
        db.session.add(admin)
        db.session.commit()
        print('已自动创建初始管理员账号：admin / admin')
    print('数据库初始化完成') 