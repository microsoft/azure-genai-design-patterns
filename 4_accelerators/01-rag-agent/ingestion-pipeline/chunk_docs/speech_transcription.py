import os
import time

import azure.cognitiveservices.speech as speechsdk
from openai import AzureOpenAI

# NOTE: The following code is adapted from the official Azure Cognitive Services SDK documentation.
# https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-use-codec-compressed-audio-input-streams?tabs=windows%2Cdebian%2Cjava-android%2Cterminal&pivots=programming-language-python
# Follow the installation instructions for gstreamer to use the code below.


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


def compressed_stream_helper(speech_config, compressed_format, file_path) -> str:
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


def pull_audio_input_stream_compressed_mp3(speech_config, mp3_file_path: str) -> str:
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
    return compressed_stream_helper(speech_config, compressed_format, mp3_file_path)


def pull_audio_input_stream_wav(speech_config, wav_file_path: str) -> str:
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


def whisper_transcription_text(
    whisper_client, audio_file_path: str, deployment_id: str = "whisper"
) -> str:
    """
    Transcribes an audio file using the Whisper API.

    Args:
        whisper_client: The Azure OpenAI client instance for interacting with the Whisper API.
        audio_file_path (str): The path to the audio file to be transcribed. Must be one of 'mp3', 'mp4', 'mpeg', 'mpga', 'm4a', 'wav', 'webm' types.
        deployment_id (str, optional): The model deployment ID to use for transcription. Defaults to 'whisper'.

    Returns:
        str: The transcribed text from the audio file.
    """
    # Open the audio file in binary read mode
    with open(audio_file_path, "rb") as audio_file:
        # Create a transcription using the Whisper API
        transcription_result = whisper_client.audio.transcriptions.create(
            file=audio_file, model=deployment_id
        )

    # Print the transcribed text to the console
    # print(transcription_result.text)

    # Return the transcribed text
    return transcription_result.text


# Define the allowed file types and maximum file size
ALLOWED_FILE_TYPES = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm"}
MAX_FILE_SIZE_MB = 24.99


# Function to check file type and size
def check_file(file_path):
    file_extension = file_path.split(".")[-1].lower()
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)  # Convert bytes to MB
    if file_extension not in ALLOWED_FILE_TYPES:
        return False, "File type is not allowed."
    if file_size_mb > MAX_FILE_SIZE_MB:
        return False, "File size exceeds the maximum limit."
    return True, "File is valid."


def speech_main(speech_config, whisper_client, file_path: str) -> str:
    """
    Main function to process the audio file based on its format.

    Args:
        file_path (str): The path to the audio file.
        speech_key (str): Azure Speech API subscription key.
        speech_region (str): Azure Speech API service region.
        whisper_client: The Azure client object from the AzureOpenAI sdk.

    Returns:
        str: The full transcript of the recognized speech.
    """
    is_valid, message = check_file(file_path)
    if is_valid:
        try:
            result_text = whisper_transcription_text(whisper_client, file_path)
            return result_text
        except Exception as e:
            raise RuntimeError(f"Failed to create transcription: {e}")
    else:
        if file_path.lower().endswith(".mp3"):
            return pull_audio_input_stream_compressed_mp3(speech_config, file_path)
        elif file_path.lower().endswith(".wav"):
            return pull_audio_input_stream_wav(speech_config, file_path)
        else:
            raise ValueError(f"File does not meet the required conditions: {message}")


# Example usage:
# file_path = "path_to/azure-genai-design-patterns/4_accelerators/01-rag-agent/data/Introducing GPT-4.mp3" # gpt-4o-system-card.pdf Introducing GPT-4.wav

# speech_key = ""
# speech_region = ""
# speech_config = speechsdk.SpeechConfig(
#         subscription=speech_key,
#         region=speech_region
#     )

# aoai_key=""
# aoai_api_version="2024-06-01"
# aoai_endpoint=""
# deployment_id="whisper"
# whisper_client = AzureOpenAI(
#     azure_endpoint = aoai_endpoint,
#     api_key = aoai_key,
#     api_version = aoai_api_version
#     )

# print(speech_main(speech_config, whisper_client, file_path))
