FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY run.py .
COPY stacksense stacksense
COPY web web
COPY scenarios scenarios
ENV HOST=0.0.0.0 PORT=8000 STACKSENSE_LLM=off
EXPOSE 8000
CMD ["python", "run.py"]
