# Сборка из корня agent-platform/ (так задано в docker-compose.yml).
FROM python:3.12-slim
WORKDIR /app
COPY platform/sdk/agent_sdk.py .
# Замените my-agent на имя папки агента
COPY agents/my-agent/main.py .
CMD ["python", "agent_sdk.py", "main"]
