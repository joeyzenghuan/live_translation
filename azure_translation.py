from time import sleep
from os import linesep
import azure.cognitiveservices.speech as speechsdk  # type: ignore
import helper
import user_config_helper
import socketio
import datetime

USAGE = """Usage: python captioning.py [...]

  HELP
    --help                           Show this help and stop.

  CONNECTION
    --key KEY                        Your Azure Speech service resource key.
                                     Overrides the SPEECH_KEY environment variable. You must set the environment variable (recommended) or use the `--key` option.
    --region REGION                  Your Azure Speech service region.
                                     Overrides the SPEECH_REGION environment variable. You must set the environment variable (recommended) or use the `--region` option.
                                     Examples: westus, eastus

  DETECT LANGUAGE
    --detectLanguages LANG1,LANG2    Specify languages for language detection.
                                     Examples: en-US, ja-JP

  TARGET LANGUAGES
    --targetLanguages LANG1,LANG2    Specify target languages for translation.
                                     Examples: zh-Hant, ja, en

  MODE
    --realTime                       Output real-time results.
                                     Default output mode is offline.

"""

class Captioning(object):
    def __init__(self, config=None, roomid=None, db=None):
        if config is None:
            self._user_config = user_config_helper.user_config_from_args(USAGE)
            self.socketio = False
        else:   
            self._user_config = config
            self.socketio = True
            self.sio = socketio.Client()
            self.sio.connect(
                self._user_config["socketio"]["endpoint"] + "?roomid=" + roomid,
                socketio_path=self._user_config["socketio"]["path"],
                transports=["websocket"],
            )
            self.caption_collection = db.collection("rooms").document(roomid).collection("captions")
        self._offline_results = []

    def translation_continuous_with_lid_from_microphone(self):
        """performs continuous speech translation from a multi-lingual audio file, with continuous language identification"""
        # <TranslationContinuousWithLID>

        # When you use Language ID with speech translation, you must set a v2 endpoint.
        # This will be fixed in a future version of Speech SDK.

        # Set up translation parameters, including the list of target (translated) languages.
        endpoint_string = (
            "wss://{}.stt.speech.microsoft.com/speech/universal/v2".format(
                self._user_config["region"]
            )
        )
        translation_config = speechsdk.translation.SpeechTranslationConfig(
            subscription=self._user_config["subscription_key"],
            endpoint=endpoint_string,
        )
        for lang in self._user_config["target_languages"]:
            translation_config.add_target_language(lang)

        audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)

        # Since the spoken language in the input audio changes, you need to set the language identification to "Continuous" mode.
        # (override the default value of "AtStart").
        translation_config.set_property(
            property_id=speechsdk.PropertyId.SpeechServiceConnection_LanguageIdMode,
            value="Continuous",
        )
        translation_config.set_property(
            property_id=speechsdk.PropertyId.SpeechServiceResponse_PostProcessingOption,
            value="TrueText",
        )

        # Specify the AutoDetectSourceLanguageConfig, which defines the number of possible languages
        auto_detect_source_language_config = (
            speechsdk.languageconfig.AutoDetectSourceLanguageConfig(
                languages=self._user_config["detect_languages"]
            )
        )

        # Creates a translation recognizer using and audio file as input.
        recognizer = speechsdk.translation.TranslationRecognizer(
            translation_config=translation_config,
            audio_config=audio_config,
            auto_detect_source_language_config=auto_detect_source_language_config,
        )

        if len(self._user_config["phrases"]) > 0 :
            grammar = speechsdk.PhraseListGrammar.from_recognizer(recognizer=recognizer)
            for phrase in self._user_config["phrases"] :
                grammar.addPhrase(phrase)

        def recognizing_handler(evt):
            if (
                evt.result.reason == speechsdk.ResultReason.TranslatingSpeech
                and len(evt.result.text) > 0
            ):
                # This seems to be the only way we can get information about
                # exceptions raised inside an event handler.
                try:
                    src_lang = evt.result.properties[
                        speechsdk.PropertyId.SpeechServiceConnection_AutoDetectSourceLanguageResult
                    ]
                    print("RECOGNIZING {}: {}".format(src_lang, evt.result.text))
                    for key in evt.result.translations:
                        print("Translation to {}: {}".format(key, evt.result.translations[key]))
                    if self.socketio is True:
                        self.sio.emit(
                            self._user_config["serverid"],
                            {
                                "state": "recognizing",
                                "language": src_lang,
                                "text": evt.result.text,
                                "translations": evt.result.translations,
                            },
                        )
                    
                except Exception as ex:
                    print("Exception in recognizing_handler: {}".format(ex))
            elif speechsdk.ResultReason.NoMatch == evt.result.reason:
                helper.write_to_console(
                    text="NOMATCH: Speech could not be recognized.{}".format(linesep),
                    user_config=self._user_config,
                )

        def result_callback(evt):
            """callback to display a translation result"""
            if (
                evt.result.reason == speechsdk.ResultReason.TranslatedSpeech
                and len(evt.result.text) > 0
            ):
                src_lang = evt.result.properties[
                    speechsdk.PropertyId.SpeechServiceConnection_AutoDetectSourceLanguageResult
                ]
                print(
                    """Recognized:
                Detected language: {}
                Recognition result: {}""".format(
                        src_lang,
                        evt.result.text,
                    )
                )
                for key in evt.result.translations:
                    print(
                        "Translation to {}: {}".format(key, evt.result.translations[key])
                    )
                self._offline_results.append(evt.result)
                timestamp = datetime.datetime.now().timestamp()

                self.caption_collection.document(f"caption_{timestamp}").set(
                    {
                        "timestamp": timestamp,
                        "language": src_lang,
                        "text": evt.result.text,
                        "translations": evt.result.translations,
                    }
                )
                if self.socketio is True:
                    self.sio.emit(
                        self._user_config["serverid"],
                        {
                            "state": "recognized",
                            "timestamp": timestamp,
                            "language": src_lang,
                            "text": evt.result.text,
                            "translations": evt.result.translations,
                        },
                    )
        
            

        done = False

        def stop_cb(evt):
            """callback that signals to stop continuous recognition upon receiving an event `evt`"""
            print("CLOSING on {}".format(evt))
            nonlocal done
            done = True

        # connect callback functions to the events fired by the recognizer
        recognizer.session_started.connect(
            lambda evt: print("TRANSLATE STARTED: {}".format(evt))
        )
        recognizer.session_stopped.connect(
            lambda evt: print("TRANSLATE STOPPED {}".format(evt))
        )

        if (user_config_helper.CaptioningMode.REALTIME == self._user_config["captioning_mode"]):
            recognizer.recognizing.connect(lambda evt: recognizing_handler(evt))
        # event for final result
        recognizer.recognized.connect(lambda evt: result_callback(evt))

        # cancellation event
        recognizer.canceled.connect(
            lambda evt: print("CANCELED: {} ({})".format(evt, evt.reason))
        )

        # stop continuous recognition on either session stopped or canceled events
        recognizer.session_stopped.connect(stop_cb)
        recognizer.canceled.connect(stop_cb)

        # start translation
        recognizer.start_continuous_recognition()

        while not done:
            sleep(0.5)

        recognizer.stop_continuous_recognition()
    def transcription_continuous_with_lid_from_microphone(self):
        """performs continuous speech translation from a multi-lingual audio file, with continuous language identification"""
        # <TranslationContinuousWithLID>

        # When you use Language ID with speech translation, you must set a v2 endpoint.
        # This will be fixed in a future version of Speech SDK.

        # Set up translation parameters, including the list of target (translated) languages.
        endpoint_string = (
            "wss://{}.stt.speech.microsoft.com/speech/universal/v2".format(
                self._user_config["region"]
            )
        )
        transcript_config = speechsdk.SpeechConfig(
            subscription=self._user_config["subscription_key"],
            endpoint=endpoint_string,
        )

        audio_config = speechsdk.AudioConfig(use_default_microphone=True)

        # Since the spoken language in the input audio changes, you need to set the language identification to "Continuous" mode.
        # (override the default value of "AtStart").
        transcript_config.set_property(
            property_id=speechsdk.PropertyId.SpeechServiceConnection_LanguageIdMode,
            value="Continuous",
        )

        transcript_config.set_property(
            property_id=speechsdk.PropertyId.SpeechServiceResponse_PostProcessingOption,
            value="TrueText",
        )

        # Specify the AutoDetectSourceLanguageConfig, which defines the number of possible languages
        print("DETECT LANGUAGES: {}".format(self._user_config["detect_languages"]))
        auto_detect_source_language_config = (
            speechsdk.languageconfig.AutoDetectSourceLanguageConfig(
                languages=self._user_config["detect_languages"]
            )
        )

        # Creates a translation recognizer using and audio file as input.
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=transcript_config,
            audio_config=audio_config,
            auto_detect_source_language_config=auto_detect_source_language_config,
        )

        if len(self._user_config["phrases"]) > 0:
            grammar = speechsdk.PhraseListGrammar.from_recognizer(recognizer=recognizer)
            for phrase in self._user_config["phrases"]:
                grammar.addPhrase(phrase)

        def recognizing_handler(evt):
            if (
                evt.result.reason == speechsdk.ResultReason.RecognizingSpeech
                and len(evt.result.text) > 0
            ):
                # This seems to be the only way we can get information about
                # exceptions raised inside an event handler.
                try:
                    src_lang = evt.result.properties[
                        speechsdk.PropertyId.SpeechServiceConnection_AutoDetectSourceLanguageResult
                    ]
                    print("RECOGNIZING {}: {}".format(src_lang, evt.result.text))
                    if self.socketio is True:
                        self.sio.emit(
                            self._user_config["serverid"],
                            {
                                "state": "recognizing",
                                "language": src_lang,
                                "text": evt.result.text,
                            },
                        )

                    

                except Exception as ex:
                    print("Exception in recognizing_handler: {}".format(ex))
            elif speechsdk.ResultReason.NoMatch == evt.result.reason:
                helper.write_to_console(
                    text="NOMATCH: Speech could not be recognized.{}".format(linesep),
                    user_config=self._user_config,
                )

        def result_callback(evt):
            """callback to display a translation result"""
            if (
                evt.result.reason == speechsdk.ResultReason.RecognizedSpeech
                and len(evt.result.text) > 0
            ):
                src_lang = evt.result.properties[
                    speechsdk.PropertyId.SpeechServiceConnection_AutoDetectSourceLanguageResult
                ]
                print(
                    """Recognized:
                Detected language: {}
                Recognition result: {}""".format(
                        src_lang,
                        evt.result.text,
                    )
                )
                self._offline_results.append(evt.result)
                timestamp = datetime.datetime.now().timestamp()
                self.caption_collection.document(f"caption_{timestamp}").set(
                    {
                        "timestamp": timestamp,
                        "language": src_lang,
                        "text": evt.result.text,
                        "translations": evt.result.translations,
                    }
                )
                if self.socketio is True:
                    self.sio.emit(
                        self._user_config["serverid"],
                        {
                            "state": "recognized",
                            "timestamp": timestamp,
                            "language": src_lang,
                            "text": evt.result.text,
                        },
                    )

        done = False

        def stop_cb(evt):
            """callback that signals to stop continuous recognition upon receiving an event `evt`"""
            print("CLOSING on {}".format(evt))
            nonlocal done
            done = True

        # connect callback functions to the events fired by the recognizer
        recognizer.session_started.connect(
            lambda evt: print("CAPTION STARTED: {}".format(evt))
        )
        recognizer.session_stopped.connect(
            lambda evt: print("CAPTION STOPPED {}".format(evt))
        )

        if (
            user_config_helper.CaptioningMode.REALTIME
            == self._user_config["captioning_mode"]
        ):
            recognizer.recognizing.connect(lambda evt: recognizing_handler(evt))
        # event for final result
        recognizer.recognized.connect(lambda evt: result_callback(evt))

        # cancellation event
        recognizer.canceled.connect(
            lambda evt: print("CANCELED: {} ({})".format(evt, evt.reason))
        )

        # stop continuous recognition on either session stopped or canceled events
        recognizer.session_stopped.connect(stop_cb)
        recognizer.canceled.connect(stop_cb)

        # start translation
        recognizer.start_continuous_recognition()

        while not done:
            sleep(0.5)

        recognizer.stop_continuous_recognition()
if __name__ == '__main__':
    if user_config_helper.cmd_option_exists("--help") :
        print(USAGE)
    else :
        captioning = Captioning()
        captioning.translation_continuous_with_lid_from_multilingual_file()