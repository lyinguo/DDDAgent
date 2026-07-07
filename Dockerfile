FROM python:3.10-slim-bullseye

LABEL maintainer="foo@bar.com"
ARG TZ='Asia/Shanghai'
ENV TZ=${TZ}

ARG CHATGPT_ON_WECHAT_VER

RUN echo /etc/apt/sources.list
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list
ENV BUILD_PREFIX=/app

ADD . ${BUILD_PREFIX}

RUN apt-get update \
    &&apt-get install -y --no-install-recommends bash ffmpeg espeak libavcodec-extra\
    && cd ${BUILD_PREFIX} \
    && cp config-template.json config.json \
    && /usr/local/bin/python -m pip install --no-cache --upgrade pip \
    && pip config set global.index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
    && pip install --no-cache -r requirements.txt \
    && pip install --no-cache -r requirements-optional.txt \
    && pip install azure-cognitiveservices-speech

RUN apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime \
    && echo ${TZ} > /etc/timezone \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR ${BUILD_PREFIX}

ADD docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh \
    && mkdir -p /home/noroot \
    && groupadd -r noroot \
    && useradd -r -g noroot -s /bin/bash -d /home/noroot noroot \
    && chown -R noroot:noroot /home/noroot ${BUILD_PREFIX} /usr/local/lib

USER noroot

ENTRYPOINT ["/entrypoint.sh"]
