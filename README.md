## Server commands

Start FastAPI server :
python3 server.py

To get access remotely to your server (without being on the same network) use Ngrok services :
ngrok http PORT

By clicking on the web link given by the first command you will get an OpenStreet Map used to fill the database (/map route).
A button allows you to display points which have been succefully saved in the database.

You can change to the /map_test route to go to test mode.

## Esp32 scripts

**Training code** : scan_wifi_fill_db.ino

**Test code** : scan_wifi_localise.ino

## Training method

To fill the DB with new measurments :

- Start the server,
- Start Ngrok,
- Run the training code on the Esp32,
- Use the Ngrok link on your smartphone to get the map remotely,
- Wander in the street by clicking at a constant pace (once every 15s) on your location on the map,

Note : to check if the Training is successfull click on the Button to load the points.

## Test method

To geolocalise yourself :

- Start the server,
- Start Ngrok,
- Run the test code on the Esp32,
- Use the Ngrok link on your smartphone to get the map remotely. Move to /map_test.
