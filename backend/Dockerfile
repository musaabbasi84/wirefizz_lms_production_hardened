FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/uploads/profile
EXPOSE 5000
CMD ["gunicorn","-w","4","-b","0.0.0.0:5000","--access-logfile","-","--error-logfile","-","run:app"]
