# OpenConnectCompanion

**OpenConnectCompanion** is a fast, open-source, self-hostable tool for synchronizing, analyzing, and visualizing data from Garmin smartwatches and cycling computers — all written in Python.

It aims to provide a feature set identical to **Garmin Connect™**, while staying lightweight, fully open-source, and free from unnecessary bloat. Even if Garmin’s services were ever discontinued, OpenConnectCompanion is designed to remain fully functional for its users, as it does not depend on any Garmin services. 

## Install
- **Docker:** `sudo docker run -p 5000:5000 janmarkusdev/openconnectcompanion: lastest`
- **From source:** Clone repo, satisfy `requirements.txt` and run `app.py`. 

## Thanks to these projects for their code:
- Docker
- Python
- Flask
- Werkzeug
- pytz
- tzlocal
- python-dotenv
- folium
- leaflet
- TailwindCSS

- fitparse

## Roadmap:
- ✅ ~~1.9. first demo with 1 graph~~
- ✅ ~~1.9. first alpha docker container~~
- 1.9. first beta containers release

> OpenConnectCompanion is an independent open-source project and is not affiliated with or endorsed by Garmin Ltd. or its subsidiaries.
