import time
import azure.cognitiveservices.speech as speechsdk


class BinaryFileReaderCallback(speechsdk.audio.PullAudioInputStreamCallback):
    """
    A callback class to read audio data from a binary file.
    Attributes:
        _file_handle: A file handle for reading the binary file.
    """

    def __init__(self, filename: str):
        """
        Initializes the BinaryFileReaderCallback with the given filename.
        Args:
            filename (str): The path to the binary file to be read.
        """
        super().__init__()
        self._file_handle = open(filename, "rb")

    def read(self, buffer: memoryview) -> int:
        """
        Reads data into the buffer from the binary file.
        Args:
            buffer (memoryview): A memoryview object to store the read data.
        Returns:
            int: The number of bytes read.
        """
        try:
            size = buffer.nbytes
            frames = self._file_handle.read(size)
            buffer[: len(frames)] = frames
            return len(frames)
        except Exception as ex:
            print(f"Exception in `read`: {ex}")
            raise

    def close(self) -> None:
        """
        Closes the binary file handle.
        """
        try:
            self._file_handle.close()
        except Exception as ex:
            print(f"Exception in `close`: {ex}")
            raise


def compressed_stream_helper(
    compressed_format, file_path, speech_key, speech_region
) -> str:
    """
    Helper function to handle the streaming and recognition of compressed audio.
    Args:
        compressed_format: The compressed audio stream format.
        file_path (str): The path to the audio file.
        speech_key (str): Azure Speech API subscription key.
        speech_region (str): Azure Speech API service region.
    Returns:
        str: The full transcript of the recognized speech.
    """
    callback = BinaryFileReaderCallback(file_path)
    stream = speechsdk.audio.PullAudioInputStream(
        stream_format=compressed_format, pull_stream_callback=callback
    )
    speech_config = speechsdk.SpeechConfig(
        subscription=speech_key, region=speech_region
    )
    audio_config = speechsdk.audio.AudioConfig(stream=stream)
    speech_recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config, audio_config=audio_config
    )
    recognized_texts = []
    done = False

    def stop_cb(evt):
        """Callback that signals to stop continuous recognition upon receiving an event `evt`."""
        nonlocal done
        done = True

    def recognized_cb(evt):
        """Callback that captures recognized text fragments."""
        if evt.result.text:
            recognized_texts.append(evt.result.text)

    # Connect callbacks to the events fired by the speech recognizer
    speech_recognizer.recognized.connect(recognized_cb)
    speech_recognizer.session_stopped.connect(stop_cb)
    speech_recognizer.canceled.connect(stop_cb)

    # Start continuous speech recognition
    speech_recognizer.start_continuous_recognition()

    # Wait for recognition to complete
    while not done:
        time.sleep(0.5)

    # Stop continuous recognition
    speech_recognizer.stop_continuous_recognition()

    # Join all the recognized texts into a single output
    full_transcript = " ".join(recognized_texts)
    return full_transcript


def pull_audio_input_stream_compressed_mp3(
    mp3_file_path: str, speech_key: str, speech_region: str
) -> str:
    """
    Processes an MP3 file to extract and recognize speech using Azure Cognitive Services.
    Args:
        mp3_file_path (str): The path to the MP3 file.
        speech_key (str): Azure Speech API subscription key.
        speech_region (str): Azure Speech API service region.
    Returns:
        str: The full transcript of the recognized speech.
    """
    # Create a compressed format for the MP3 file
    compressed_format = speechsdk.audio.AudioStreamFormat(
        compressed_stream_format=speechsdk.AudioStreamContainerFormat.MP3
    )
    return compressed_stream_helper(
        compressed_format, mp3_file_path, speech_key, speech_region
    )


def pull_audio_input_stream_wav(
    wav_file_path: str, speech_key: str, speech_region: str
) -> str:
    """
    Processes a WAV file to extract and recognize speech using Azure Cognitive Services.
    Args:
        wav_file_path (str): The path to the WAV file.
        speech_key (str): Azure Speech API subscription key.
        speech_region (str): Azure Speech API service region.
    Returns:
        str: The full transcript of the recognized speech.
    """
    audio_config = speechsdk.audio.AudioConfig(filename=wav_file_path)
    speech_config = speechsdk.SpeechConfig(
        subscription=speech_key, region=speech_region
    )
    speech_recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config, audio_config=audio_config
    )
    recognized_texts = []
    done = False

    def stop_cb(evt):
        """Callback that signals to stop continuous recognition upon receiving an event `evt`."""
        nonlocal done
        done = True

    def recognized_cb(evt):
        """Callback that captures recognized text fragments."""
        if evt.result.text:
            recognized_texts.append(evt.result.text)

    # Connect callbacks to the events fired by the speech recognizer
    speech_recognizer.recognized.connect(recognized_cb)
    speech_recognizer.session_stopped.connect(stop_cb)
    speech_recognizer.canceled.connect(stop_cb)

    # Start continuous speech recognition
    speech_recognizer.start_continuous_recognition()

    # Wait for recognition to complete
    while not done:
        time.sleep(0.5)

    # Stop continuous recognition
    speech_recognizer.stop_continuous_recognition()

    # Join all the recognized texts into a single output
    full_transcript = " ".join(recognized_texts)
    return full_transcript


def main(file_path: str, speech_key: str, speech_region: str) -> str:
    """
    Main function to process the audio file based on its format.
    Args:
        file_path (str): The path to the audio file.
        speech_key (str): Azure Speech API subscription key.
        speech_region (str): Azure Speech API service region.
    Returns:
        str: The full transcript of the recognized speech.
    """
    if file_path.lower().endswith(".mp3"):
        return pull_audio_input_stream_compressed_mp3(
            file_path, speech_key, speech_region
        )
    elif file_path.lower().endswith(".wav"):
        return pull_audio_input_stream_wav(file_path, speech_key, speech_region)
    else:
        raise ValueError("Unsupported audio format. Only .mp3 and .wav are supported.")


# Example usage:
# file_path = "path_to_your_audio_file.wav"
# speech_key = "your_azure_speech_api_key"
# speech_region = "your_azure_speech_api_region"
# print(main(file_path, speech_key, speech_region))
