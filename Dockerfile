FROM alpine:edge

RUN apk add python3 py3-pip py3-flask py3-tz py3-tzlocal git

ENV PORT=5000

CMD git clone https://github.com/janmarkus-dev/OpenConnectCompanion.git
CMD ["echo", "docker works"]
