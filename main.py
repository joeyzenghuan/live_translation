import argparse
import datetime
import os
import threading

import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask
from flask_socketio import SocketIO

import user_config_helper
from azure_translation import Captioning

app = Flask(__name__)
socketio = SocketIO(app)

config = {
    "subscription_key": os.environ.get("AZURE_SPEECH_KEY"),
    "region": os.environ.get("AZURE_SPEECH_REGION"),
    "captioning_mode": user_config_helper.CaptioningMode.REALTIME,
    "socketio": {
        "endpoint": os.environ.get("SOCKET_ENDPOINT"),
        "path": os.environ.get("SOCKET_PATH"),
    },
    "serverid": os.environ.get("SERVER_ID"),
}


def start_captioning(roomid, db):
    captioning = Captioning(config, roomid, db)
    if len(config["target_languages"]) > 0:
        captioning.translation_continuous_with_lid_from_microphone()
    else:
        captioning.transcription_continuous_with_lid_from_microphone()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--roomid", type=str, help="Room ID")
    parser.add_argument(
        "--firebase-cred", type=str, help="Path to Firebase service account key"
    )
    parser.add_argument("--create-room", action="store_true", help="Create a room")
    parser.add_argument(
        "--edit-room", action="store_true", help="Edit room information"
    )
    parser.add_argument(
        "--init", action="store_true", help="Initialize firebase caption database"
    )

    args = parser.parse_args()
    if args.roomid is None and not args.create_room and not args.init:
        raise Exception(
            "Room ID is required. Create a room by --create-room or edit an existing room by --edit-room."
        )
    if args.firebase_cred is None:
        raise Exception("Firebase service account key is required.")

    # Initialize Firebase
    cred = credentials.Certificate(args.firebase_cred)
    app = firebase_admin.initialize_app(cred)
    db = firestore.client()

    if args.create_room:
        newroom_ref = db.collection("rooms").document()
        # Create a new room with user input
        try:
            newroom_ref.set(
                {
                    "event": input("Event: "),
                    "date": datetime.datetime.strptime(
                        input("Date (YYYY-MM-DD): "), "%Y-%m-%d"
                    ),
                    "talk": input("Talk Name: "),
                    "hall": input("Hall Name: "),
                    "detect_lang": input("Detect Languages: ").split(","),
                    "target_lang": input("Target Languages: ").split(","),
                    "phrases": input("Phrases: ").split(","),
                    "created_by": cred._g_credential._service_account_email,
                    "created_at": firestore.SERVER_TIMESTAMP,
                }
            )
            print("Talk room created. Room ID: ", newroom_ref.id)
            print("Please run the script again with --roomid ", newroom_ref.id)
            exit()
        except Exception as e:
            print(e)
            newroom_ref.delete()
            exit()

    roomid = args.roomid
    roomdata_ref = db.collection("rooms").document(roomid)
    room_data = roomdata_ref.get()
    if args.edit_room:
        if room_data.exists:
            # Edit room information
            try:
                roomdata_ref.update(
                    {
                        "event": input(f"Event [{room_data.to_dict().get('event')}]: "),
                        "date": datetime.datetime.strptime(
                            input(
                                f"Date (YYYY-MM-DD) [{room_data.to_dict().get('date').strftime('%Y-%m-%d')}]: "
                            ),
                            "%Y-%m-%d",
                        ),
                        "talk": input(
                            f"Talk Name [{room_data.to_dict().get('talk')}]: "
                        ),
                        "hall": input(
                            f"Hall Name [{room_data.to_dict().get('hall')}]: "
                        ),
                        "detect_lang": input(
                            f"Detect Languages [{','.join(room_data.to_dict().get('detect_lang', []))}]: "
                        ).split(","),
                        "target_lang": input(
                            f"Target Languages [{','.join(room_data.to_dict().get('target_lang', []))}]: "
                        ).split(","),
                        "phrases": input(
                            f"Phrases [{', '.join(room_data.to_dict().get('phrases', []))}]: "
                        ).split(","),
                        "created_by": cred._g_credential._service_account_email,
                        "created_at": firestore.SERVER_TIMESTAMP,
                    }
                )
                print("Talk room updated.")
                print("Please run the script again with --roomid ", roomid)

                exit()
            except Exception as e:
                print(e)
                exit()
        else:
            raise Exception("Talk room data not exist. Create a room by --create-room.")

    else:
        if room_data.exists:
            config["detect_languages"] = room_data.to_dict().get("detect_lang", [])
            config["target_languages"] = room_data.to_dict().get("target_lang", [])
            config["phrases"] = room_data.to_dict().get("phrases", [])
            # print room info
            print("Server ID: ", config["serverid"])
            print("Room Info:")
            print("  - Room ID: ", roomid)
            print("  - Event: ", room_data.to_dict().get("event"))
            print("  - Talk Name: ", room_data.to_dict().get("talk"))
            print("  - Hall Name: ", room_data.to_dict().get("hall"))
            print("  - Detect Languages: ", config["detect_languages"])
            print("  - Target Languages: ", config["target_languages"])
            print("  - Phrases: ", config["phrases"])
            print("Launch client at ", f"{os.environ.get('CLIENT_URL')}/?room={roomid}")
            
        else:
            raise Exception("Talk room data not exist. Create a talk room by --create-room.")

    if args.init:
        # Initialize firebase caption database
        captions = roomdata_ref.collection("captions").list_documents()
        for caption in captions:
            caption.delete()
        
        print("Firebase caption database initialized.")
    thread = threading.Thread(target=start_captioning, args=(roomid, db))
    thread.start()
