FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

WORKDIR /app

# Copy requirements and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Render sets PORT env var automatically (default 10000)
ENV PORT=10000

EXPOSE 10000

CMD ["python", "app.py"]
