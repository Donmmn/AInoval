# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /app

# Install dependencies
# Copy only requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project code
COPY . .

# Expose port 8000 to the outside world
EXPOSE 8000

# Run database migrations
# Note: This might be better handled as an entrypoint script or a separate step in production
RUN python manage.py migrate

# Define the command to run the application
# Using the development server for simplicity. Replace with gunicorn/uwsgi for production.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"] 