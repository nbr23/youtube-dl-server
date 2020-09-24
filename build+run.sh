# stop exiting docker containers
docker kill youtube-dl
docker rm youtube-dl

docker build -t uzfs.local:5000/twl-dl-server -t twl-dl-server .
# docker push uzfs.local:5000/twl-dl-server
docker run -d --name youtube-dl -v $HOME/ytdls:/youtube-dl -p 8080:8080 twl-dl-server
docker ps
sleep 2
open "http://localhost:8080/"
