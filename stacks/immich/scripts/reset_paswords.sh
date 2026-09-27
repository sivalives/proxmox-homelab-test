#on the immich LXC run below to map and match openbao set passwords
docker exec -it immich_postgres psql -U immich
ALTER USER immich WITH PASSWORD 'YOUR_NEW_PASSWORD';  # from, openbao
docker restart immich_postgres

docker exec -it immich_redis redis-cli CONFIG SET requirepass '9mgCb0UHFjLyoWk8Vg8WOuByX7hg0Fk0'
docker restart immich_redis