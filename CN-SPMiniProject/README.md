Project Structure
  Server Side
    server.py
    songs/
    server.crt
    server.key
  Client Side
    client.py
    received_<song_name>
Steps to run:

1.Generate SSL Certificate (Required)

Run this command in the server folder:

openssl req -x509 -newkey rsa:2048 -keyout server.key -out server.crt -days 365 -nodes

2.Start Server

python3 server.py

3.Start Client

python3 client.py

4.Available Commands (Client)

LIST               → Show available songs
PLAY <song_name>   → Stream a song
QUIT               → Exit

Make sure the songs/ folder contains audio files
Client will save streamed files as received_<song_name>
Server must be running before starting client
