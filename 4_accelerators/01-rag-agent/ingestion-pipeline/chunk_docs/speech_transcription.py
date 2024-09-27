# Import necessary modules
import hashlib
import logging
import os
import time

import azure.cognitiveservices.speech as speechsdk
import tiktoken
from langchain_experimental.text_splitter import SemanticChunker
from langchain_openai.embeddings import AzureOpenAIEmbeddings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# NOTE: The following code is adapted from the official Azure Cognitive Services SDK documentation.
# https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-use-codec-compressed-audio-input-streams?tabs=windows%2Cdebian%2Cjava-android%2Cterminal&pivots=programming-language-python


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
            logger.error("Exception in `read`: %s", ex)
            raise


    def close(self) -> None:
        """
        Closes the binary file handle.
        """
        try:
            self._file_handle.close()
        except Exception as ex:
            logger.error("Exception in `close`: %s", ex)
            raise


def compressed_stream_helper(speech_config, compressed_format, file_path) -> str:
    """
    Helper function to handle the streaming and recognition of compressed audio.

    Args:
        compressed_format: The compressed audio stream format.
        file_path (str): The path to the audio file.

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
        audio_file_path (str): The path to the audio file to be transcribed.
            Must be one of 'mp3', 'mp4', 'mpeg', 'mpga', 'm4a', 'wav', 'webm' types.
        whisper_deployment_id (str, optional): The model deployment ID to use for transcription.
            Defaults to 'whisper'.
    Returns:
        str: The transcribed text from the audio file.
    """
    logger.info(
        "Transcribing audio file: %s with deployment ID: %s",
        audio_file_path,
        whisper_deployment_id,
    )
    with open(audio_file_path, "rb") as audio_file:
        transcription_result = whisper_client.audio.transcriptions.create(
            file=audio_file, model=whisper_deployment_id
        )
    logger.info("Transcription result: %s", transcription_result.text)
    return transcription_result.text


# Define the allowed file types and maximum file size
ALLOWED_FILE_TYPES = {
    "flac",
    "m4a",
    "mp3",
    "mp4",
    "mpeg",
    "mpga",
    "oga",
    "ogg",
    "wav",
    "webm",
}
MAX_FILE_SIZE_MB = 24.99


def check_file(file_path):
    """
    Checks if the given file is valid for Whisper transcription.
    This function verifies the file extension and file size to ensure they meet
    the requirements for Whisper transcription. It checks if the file extension
    is in the list of allowed file types and if the file size does not exceed
    the maximum limit.

    Args:
        file_path (str): The path to the file to be checked.

    Returns:
        tuple: A tuple containing a boolean and a string.
            - bool: True if the file is valid for Whisper transcription, False otherwise.
            - str: A message indicating the result of the validation.
    """
    file_extension = file_path.split(".")[-1].lower()
    logger.info("File extension: %s", file_extension)
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)  # Convert bytes to MB
    logger.info("File size (MB): %s", file_size_mb)
    if file_extension not in ALLOWED_FILE_TYPES:
        return False, "File type cannot be transcribed with Whisper."
    if file_size_mb > MAX_FILE_SIZE_MB:
        return False, "File size exceeds the maximum limit for Whisper."
    return True, "File is valid for Whisper Transcription."


def speech_transcription(speech_config, whisper_client, file_path: str) -> str:
    """
    Main function to process the audio file based on its format.

    Args:
        speech_config: The speech configuration object for Azure Speech API.
        whisper_client: The Azure client object from the AzureOpenAI sdk.
        file_path (str): The path to the audio file.
    Returns:
        str: The full transcript of the recognized speech.
    """
    is_valid_for_whisper, message = check_file(file_path)
    file_extension = file_path.split(".")[-1].lower()
    logger.info("Processing file: %s", file_path)
    logger.info("File valid: %s, Message: %s", is_valid_for_whisper, message)

    if is_valid_for_whisper:
        try:
            logger.info("Transcribing with Whisper...")
            return whisper_transcription_text(whisper_client, file_path)
        except Exception as e:
            logger.error("Error processing file: %s", file_path)
            logger.error("Exception: %s", e)
            raise RuntimeError(f"Failed to create transcription: {e}")
    else:
        if file_extension == "mp3":
            logger.info("Transcribing %s as an .mp3 with Azure Speech...", file_path)
            return pull_audio_input_stream_compressed_mp3(speech_config, file_path)
        elif file_extension == "wav":
            logger.info("Transcribing %s as an .wav with Azure Speech...", file_path)
            return pull_audio_input_stream_wav(speech_config, file_path)
        else:
            raise ValueError(f"File does not meet the required conditions: {message}")


def build_chunk(chunk_type, chunk_index, title, content, doc_layout):
    """
    Builds a chunk dictionary with metadata for a document segment.
    Args:
        chunk_type (str): Type of the chunk.
        chunk_index (int): Index of the chunk.
        title (str): Title of the chunk.
        content (str): Content of the chunk.
        doc_layout (dict): Layout of the document.
    Returns:
        dict: A dictionary containing chunk metadata.
    """
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


def generate_chunks_from_stt(
    stt_output_text: str,
    file_path: str,
    max_chunk_size: int,
    text_splitter,
) -> list:
    """
    Generates chunks from the output of speech_transcription.
    Args:
        stt_output_text (str): The full transcript of the recognized speech.
        file_path (str): The path to the audio file.
        max_chunk_size (int): The maximum size of each chunk.
        text_splitter: The LangChain text splitter object for splitting the text into semantic chunks.
    Returns:
        list: A list of dictionaries - the chunks of text from audio transcription files.
    """

    doc_file_name = os.path.basename(file_path)
    stt_output_dict = {"document_name": doc_file_name, "content": stt_output_text}
    chunk_index = 0
    chunks = []

    if len(stt_output_text) > max_chunk_size:
        chunked_content = text_splitter.create_documents([stt_output_text])
        for chunk in chunked_content:
            content = chunk.page_content
            chunks.append(
                build_chunk(
                    "audio",
                    chunk_index,
                    stt_output_dict["document_name"],
                    content,
                    stt_output_dict,
                )
            )
            chunk_index += 1
    else:
        chunks.append(
            build_chunk(
                "audio",
                chunk_index,
                stt_output_dict["document_name"],
                stt_output_text,
                stt_output_dict,
            )
        )

    return chunks
