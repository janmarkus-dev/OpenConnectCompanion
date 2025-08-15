FROM alpine:edge

RUN apk add python3 py3-pip git
RUN git clone https://github.com/janmarkus-dev/OpenConnectCompanion.git
RUN pip install --break-system-packages -r OpenConnectCompanion/requirements.txt

CMD ["python", "OpenConnectCompanion/app.py"]
