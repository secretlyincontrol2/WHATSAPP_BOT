# Use the official Playwright image which has all dependencies pre-installed
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# Set up a new user named "user" with user ID 1000
RUN useradd -m -u 1000 user

WORKDIR /home/user/app

# Copy requirements and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY --chown=user . .

# Switch to non-root user
USER user

# Set home environment variables
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PORT=7860

# Expose the port
EXPOSE 7860

CMD ["python", "app.py"]
