FROM python:3.11-alpine
WORKDIR /app
COPY server.py .
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --retries=5 \
  CMD wget -qO- http://localhost:8080/ready || exit 1
CMD ["python3", "server.py"]
