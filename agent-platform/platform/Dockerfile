FROM python:3.12-slim
WORKDIR /workspace/platform
COPY platform/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY platform/ .
ENV PLATFORM_ROOT=/workspace
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
