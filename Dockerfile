FROM python:3.12

WORKDIR /app
COPY scripts/ /app/scripts/

CMD ["sleep", "infinity"]
