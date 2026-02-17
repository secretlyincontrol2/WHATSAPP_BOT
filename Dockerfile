# Use the official Playwright image which has all dependencies pre-installed
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# The official image creates a user 'pwuser' with UID 1000 automatically.
# We just need to use it.

WORKDIR /home/pwuser/app

# Copy requirements and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY --chown=pwuser . .

# Switch to the existing non-root user
USER pwuser

# Set home environment variables
ENV HOME=/home/pwuser \
    PATH=/home/pwuser/.local/bin:$PATH \
    PORT=7860

# Expose the port
EXPOSE 7860

CMD ["python", "app.py"]
