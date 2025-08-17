FROM alpine:edge
RUN apk add python3 py3-pip
COPY . /app
RUN pip install --break-system-packages -r /app/requirements.txt
CMD ["python", "/app/app.py"]
