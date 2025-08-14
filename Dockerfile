FROM alpine:edge

RUN apk add python3 py3-pip

ENV PORT=5000
