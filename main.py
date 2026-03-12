import os
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
from azure_translation import Captioning
import user_config_helper
import threading
import uuid
import argparse

app = Flask(__name__)
socketio = SocketIO(app)
roomid = str(uuid.uuid4())

config = {
    "subscription_key": os.environ.get("AZURE_SPEECH_KEY"),
    "region": os.environ.get("AZURE_SPEECH_REGION"),
    "detect_languages": ["en-US", "zh-TW", "ja-JP"],
    "target_languages": ["zh-Hant", "en", "ja"],
    "captioning_mode": user_config_helper.CaptioningMode.REALTIME,
    "phrases": [],
    "socketio": {"endpoint": "http://127.0.0.1:3002", "path": "/socket.io"},
    "roomid": roomid,    
}

@app.route('/')
def display():
    return render_template("index.html", socketio=config['socketio'])
    
@app.route("/mobile")
def display_mobile():
    return render_template("mobile.html", socketio=config['socketio'])

@app.route("/tv")
def display_tv():
    return render_template("tv.html", socketio=config["socketio"])


@socketio.on('connect')
def handle_message(data):
    print("connected")
    if len(config["target_languages"]) > 0:
        emit("available_languages", {"languages": ["Original"] + config["target_languages"]})
        emit("webcaption", {"language":"zh-TW","text":"字幕測試","translations":{"en":"subtitle test", "ja":"日本語テスト"}})
    else:
        emit("available_languages", {"languages": ["Original"]})
        emit("webcaption", {"language":"zh-TW","text":"字幕測試"})

@socketio.on(roomid)
def send_caption(data):
    emit("webcaption", data, broadcast=True)


def start_captioning():
    captioning = Captioning(config)
    if len(config["target_languages"]) > 0:
        captioning.translation_continuous_with_lid_from_microphone()
    else:
        captioning.transcription_continuous_with_lid_from_microphone()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--disable-server",
        action="store_true",
        help="Disable local server and socket.io server. Use this option when user want to use remote socket server and host client on your own.",
    )

    parser.add_argument(
        "--build",
        action="store_true",
        help="Build the frontend. Use this option when you want to self-host the frontend.",
    )
    args = parser.parse_args()

    if args.build:
        # make directory build if not exists
        if not os.path.exists("build"):
            os.mkdir("build")
        # output render_template("index.html") to index.html
        with app.app_context():
            with open("build/index.html", "w") as f:
                f.write(render_template("index.html", socketio=config["socketio"]))
            # output render_template("mobile.html") to mobile.html
            with open("build/mobile.html", "w") as f:
                f.write(render_template("mobile.html", socketio=config["socketio"]))
            # output render_template("tv.html") to tv.html
            with open("build/tv.html", "w") as f:
                f.write(render_template("tv.html", socketio=config["socketio"]))
        # copy static css file to build
        if not os.path.exists("build/static"):
            os.mkdir("build/static")
        os.system("cp -r static/*.css build/static")
        exit()
        
    print("Starting captioning...")
    print("Room ID: ", roomid)
    thread = threading.Thread(target=start_captioning)
    thread.start()

    if not args.disable_server:
        socketio.run(app, host="0.0.0.0", port=3002)
