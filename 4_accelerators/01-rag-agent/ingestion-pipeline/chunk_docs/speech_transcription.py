# Import necessary modules
import hashlib
import json
import os
import time

import azure.cognitiveservices.speech as speechsdk
import semchunk
import tiktoken
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
    whisper_client, audio_file_path: str, whisper_deployment_id: str = "whisper"
) -> str:
    """
    Transcribes an audio file using the Whisper API.

    Args:
        whisper_client: The Azure OpenAI client instance for interacting with the Whisper API.
        audio_file_path (str): The path to the audio file to be transcribed. Must be one of 'mp3', 'mp4', 'mpeg', 'mpga', 'm4a', 'wav', 'webm' types.
        whisper_deployment_id (str, optional): The model deployment ID to use for transcription. Defaults to 'whisper'.

    Returns:
        str: The transcribed text from the audio file.
    """
    # Open the audio file in binary read mode
    with open(audio_file_path, "rb") as audio_file:
        # Create a transcription using the Whisper API
        transcription_result = whisper_client.audio.transcriptions.create(
            file=audio_file, model=whisper_deployment_id
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


def speech_transcription(speech_config, whisper_client, file_path: str) -> str:
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
    # If it's a file type supported by Whisper AND less than 24.99mb in size, use Whisper
    if is_valid:
        try:
            return whisper_transcription_text(whisper_client, file_path)
        except Exception as e:
            raise RuntimeError(f"Failed to create transcription: {e}")
    # If it's not a valid file try to user Azure Speech with one of the supported formats (mp3, wav)
    # more formats available but those two supported currently
    else:
        # Run the mp3 transcription process
        if file_path.lower().endswith(".mp3"):
            return pull_audio_input_stream_compressed_mp3(speech_config, file_path)
        # Run the wav transcription process
        elif file_path.lower().endswith(".wav"):
            return pull_audio_input_stream_wav(speech_config, file_path)
        # If the file is not a valid type or size, raise an error
        else:
            raise ValueError(f"File does not meet the required conditions: {message}")


# TODO: Remove this function if it's unncessary as it is a duplicate of what is already in chunk_docs.py
def build_chunk(chunk_type, chunk_index, title, content, doc_layout):
    """Builds a chunk dictionary with metadata for a document segment."""
    document_name = doc_layout["document_name"]
    document_id = document_name + "_" + chunk_type + "_" + str(chunk_index)
    chunk = {
        "id": hashlib.md5(document_id.encode()).hexdigest(),
        "filepath": document_name,
        "url": document_name,  # TODO: replace with actual URL
        "title": title,
        "content": content,
    }
    return chunk


def generate_chunks_from_stt(stt_output_text: str, file_path: str) -> list:
    """
    Generates chunks from the output of speech_transcription.

    Args:
        file_path (str): The path to the audio file.
        stt_output_text (str): The full transcript of the recognized speech..

    Returns:
        list: A list of dictionaries - the chunks of text from audio transcription files.
    """
    # Generate chunks with semchunk as there is no markdown/titles/headers in the output of the STT
    MAX_CHUNK_SIZE = 8000
    chunker = semchunk.chunkerify(tiktoken.encoding_for_model("gpt-4o"), MAX_CHUNK_SIZE)

    # Set the "title" of the chunk to the filename
    doc_file_name = os.path.basename(file_path)

    # Create a dictionary for stt_output_dict 
    # MUST match doc_layout to use the buikd_chunk function
    # and the index for AI Search
    stt_output_dict = {"document_name": doc_file_name, "content": stt_output_text}

    # Create empty containers for chunks and sub-chunks and set index to 0
    chunk_index = 0
    chunks = []
    sub_chunks = []

    # If the STT output is too large, re-chunk it with semantic chunking from semchunk
    if len(stt_output_text) > MAX_CHUNK_SIZE:
        # Re-chunking with semantic chunking
        chunked_content = chunker(stt_output_text)
        for chunk in chunked_content:
            sub_chunks.append(
                {"title": stt_output_dict["document_name"], "content": chunk}
            )
    else:
        sub_chunks.append(
            {"title": stt_output_dict["document_name"], "content": stt_output_text}
        )

    # Build the chunks from the sub-chunks
    for sub_chunk in sub_chunks:
        chunks.append(
            build_chunk(
                "audio",
                chunk_index,
                sub_chunk["title"],
                sub_chunk["content"],
                stt_output_dict,
            )
        )
        chunk_index += 1

    return chunks


# ###############################################################################
# # Example usage:
# ###############################################################################
# # Set the file path for the audio file to be transcribed
# file_path = "path_to/your_audio/file/azure-genai-design-patterns/4_accelerators/01-rag-agent/data/Introducing GPT-4.mp3" # gpt-4o-system-card.pdf Introducing GPT-4.wav

# # Create the speech configuration object with key and region
# speech_key = ""
# speech_region = "eastus"
# speech_config = speechsdk.SpeechConfig(
#         subscription=speech_key,
#         region=speech_region
#     )

# # Create the Azure OpenAI client object
# aoai_key=""
# aoai_api_version="2024-06-01"
# aoai_endpoint=""
# aoai_client = AzureOpenAI(
#     azure_endpoint = aoai_endpoint,
#     api_key = aoai_key,
#     api_version = aoai_api_version
#     )

# # Provide the deployment IDs for the Whisper and GPT-4o models
# whisper_deployment_id="whisper"
# gpt4o_deployment_id="gpt-4o-global"

# # Transcribe the audio file
# stt_output_text = speech_transcription(speech_config, aoai_client, file_path)
# # print(stt_output_text)

# # Generate chunks from the STT output
# chunks = generate_chunks_from_stt(stt_output_text, file_path)

# # Print the chunks
# for chunk in chunks:
#     print(chunk)
