#
# twl-dl-server Server Dockerfile
#
# https://github.com/ToWatchList/twl-dl-server
#

FROM python:alpine

# Download static files (JS/CSS Libraries)
WORKDIR /usr/src/app/ydl_server/static
COPY ./requirements.txt /usr/src/app/requirements.txt
RUN mkdir ./css && \
  mkdir ./js && \
  apk add --no-cache ffmpeg tzdata curl wget && \
  curl -s https://code.jquery.com/jquery-3.4.1.min.js > js/jquery.min.js && \
  curl -s https://unpkg.com/@popperjs/core@2.1.1/dist/umd/popper.min.js > js/popper.min.js && \
  wget https://github.com/twbs/bootstrap/releases/download/v4.4.1/bootstrap-4.4.1-dist.zip && \
  mkdir tmp_bs && \
  unzip bootstrap-4.4.1-dist.zip -d tmp_bs && \
  mv tmp_bs/*/css/* css/ && \
  mv tmp_bs/*/js/* js/ && \
  rm -rf bootstrap-4.4.1-dist.zip tmp_bs && \
  apk del curl wget && \
  cd /usr/src/app && \
  pip install --no-cache-dir -r requirements.txt

COPY . /usr/src/app/

WORKDIR /usr/src/app

EXPOSE 8080

VOLUME ["/youtube-dl"]

CMD [ "python", "-u", "./youtube-dl-server.py" ]
