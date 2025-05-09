FROM python:3.9-slim

# 设置环境变量，确保 Python 输出不被缓冲，方便查看日志
ENV PYTHONUNBUFFERED=1

# 设置工作目录
WORKDIR /app

# 复制依赖文件并安装依赖
# 首先复制 requirements.txt 并安装，这样可以利用 Docker 的层缓存机制
# 只有当 requirements.txt 改变时，这一层才会重新构建
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目中的所有文件到工作目录
COPY . .

# 声明容器运行时监听的端口
# 这只是一个元数据声明，实际端口映射在 docker run 命令中指定
EXPOSE 5000

# 运行应用的命令
# 假设 run.py 使用 flask run 或类似的命令启动，并监听 0.0.0.0
# 如果你的 run.py 使用 gunicorn，命令会是类似:
# CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "your_wsgi_application_module:app_instance_name"]
# 例如: CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "run:app"] (如果app在run.py中)
# 或者 CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:create_app()"] (如果使用工厂模式)
# 我们先假设 run.py 会直接启动 Flask 开发服务器监听外部请求
# 为了能在 Docker 外访问，Flask app.run() 需要设置为 host='0.0.0.0'
CMD ["python", "run.py"] 