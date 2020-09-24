#!/bin/sh
set -x

# stop exiting docker containers
docker kill youtube-dl
docker rm youtube-dl

docker build -t uzfs.local:5000/twl-dl-server -t twl-dl-server  .
# docker push uzfs.local:5000/twl-dl-server
docker run -d --name youtube-dl -v $HOME/ytdls:/youtube-dl -p 8080:8080 --env TWL_API_TOKEN=`cat .TWL_Token` twl-dl-server
docker ps
sleep 2

curl "http://localhost:8080/api/twl/download"
open "http://localhost:8080/"
