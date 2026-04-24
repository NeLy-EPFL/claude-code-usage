FROM python:3.14-alpine

WORKDIR /app

# Install dependencies
COPY pyproject.toml pyproject.toml

RUN pip install --no-cache-dir -e .

# Copy application code
COPY . .

# Run the bot
CMD ["python", "main.py"]
