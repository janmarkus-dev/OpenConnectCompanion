FROM alpine:edge

RUN apk add python3 py3-pip py3-flask py3-tz py3-tzlocal pipx git
RUN pip install --break-system-packages folium
RUN git clone https://github.com/janmarkus-dev/OpenConnectCompanion.git

CMD ["python", "OpenConnectCompanion/app.py"]
