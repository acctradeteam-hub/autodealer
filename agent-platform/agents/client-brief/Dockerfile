FROM python:3.12-slim
WORKDIR /app
COPY platform/sdk/agent_sdk.py .
COPY agents/client-brief/main.py .
CMD ["python", "agent_sdk.py", "main"]
