from app import create_app, db
# 假设 User 模型在 app/models/user.py
from app.models.user import User 
import click
import os
import time

app = create_app()

@app.cli.command("clr")
@click.confirmation_option(prompt="危险操作：确定要删除所有用户数据吗？")
def clear_users():
    """清空所有用户数据并尝试触发重载。"""
    try:
        num_deleted = db.session.query(User).delete()
        db.session.commit()
        print(f"成功删除了 {num_deleted} 个用户。")

        # 尝试触发 Flask 开发服务器重载 (仅限开发模式)
        # 通过修改 run.py 的时间戳
        try:
            filepath = os.path.abspath(__file__)
            current_time = time.time()
            os.utime(filepath, (current_time, current_time))
            print("已尝试触发服务器重载...")
        except Exception as e:
            print(f"尝试触发重载时出错: {e}")

    except Exception as e:
        db.session.rollback()
        print(f"清空用户时出错: {e}")

if __name__ == '__main__':
    # 注意：运行 app.run() 会阻塞，无法直接在此处接收 'clr' 输入。
    # clr 命令需要通过 'flask clr' 在单独的终端中运行。
    app.run(debug=True) 