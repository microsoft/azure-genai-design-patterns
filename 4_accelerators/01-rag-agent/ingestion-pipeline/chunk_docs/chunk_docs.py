import argparse
import base64
import hashlib
import json
import logging
import os

import azure.cognitiveservices.speech as speechsdk
import cv2
import document_intelligence_reader as docreader
import numpy as np
import pandas as pd
import semchunk
import speech_transcription as st
import tiktoken
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
from azure.core.credentials import AzureKeyCredential
from azureml.core import Run, Workspace
from openai import AzureOpenAI
from pdf2image import convert_from_path
from tenacity import retry, stop_after_attempt, wait_random_exponential

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# GPT-4-vision/o system message
system_message_vision_to_text = (
    "Describe the picture in detail (title and description). The description needs to provide as much information as possible."
    "For instance, for images which are photos, describe people, animals, objects, activities, but also colors, type of image, style."
    "If there is text in the image, make sure you extract it. If the image is a chart/graph, detail the findings, as well as the analysis of the meaning of the chart"
    "or conclusion that can be derived from it. The end goal is to provide a very comprehensive description of the image so we can operate effective search on it."
    "Your end result needs to have the following form:"
    "<image title>\n==========\n<image description>"
)


# TODO: Implement a vectorizer for image and text using Azure Vision 4.0
# https://learn.microsoft.com/en-us/azure/ai-services/computer-vision/how-to/image-retrieval?tabs=python#call-the-vectorize-image-api
def azure_ai_vision_generate_image_vector(image_path):
    """Generates a vector representation of the image using Azure Vision 4.0 API."""
    pass


# Get keys from Azure Key Vault
try:
    keyvault = Run.get_context().experiment.workspace.get_default_keyvault()
except RunEnvironmentException or WorkspaceException as e:
    keyvault = Workspace.from_config().get_default_keyvault()


def getenv(key):
    """Retrieves the secret value for the given key from Azure Key Vault."""
    return keyvault.get_secret(name=key)


# generate embedding using an OpenAI ADA model
@retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
def generate_text_embeddings(text):
    return (
        aoai_client.embeddings.create(
            input=[text], model=openai_embedding_model
        )
        .data[0]
        .embedding
    )

def build_chunk(chunk_type, chunk_index, title, content, doc_layout):
    """Builds a chunk dictionary with metadata for a document segment."""
    document_name = doc_layout["document_name"]
    document_id = f"{document_name}_{chunk_type}_{chunk_index}"
    chunk = {
        "id": hashlib.md5(document_id.encode()).hexdigest(),
        "filepath": document_name,
        "url": document_name,  # TODO: replace with actual URL
        "title": title,
        "content": content,
        "contentVector": generate_text_embeddings(content),
    }
    return chunk


def image_to_data_url(image):
    """Encodes an image into a data URL format."""
    mime_type = "image/png"
    base64_encoded_data = base64.b64encode(image).decode("utf-8")
    return f"data:{mime_type};base64,{base64_encoded_data}"


def generate_chunks_from_markdown(doc_layout):
    """Generates chunks from the markdown content of a document layout."""
    raw_chunks = doc_layout.content.split("## ")
    raw_chunks = list(filter(None, raw_chunks))
    chunk_index = 0
    chunks = []
    for raw_chunk in raw_chunks:
        try:
            title, content = raw_chunk.split("\n", 1)
            content = title + "\n" + content
        except ValueError:
            logger.error("Error splitting chunk: %s ", raw_chunk)
            raise ValueError("Error splitting chunk: %s ", raw_chunk)
        sub_chunks = []
        if len(content) > MAX_CHUNK_SIZE:
            # Generate chunks from markdown
            #TODO: Implement chunking for large text segments based on tokens not characters - use langchain?
            chunker = semchunk.chunkerify(tiktoken.encoding_for_model("gpt-4o"), MAX_CHUNK_SIZE)
            chunked_content = chunker(content)
            for chunk in chunked_content:
                sub_chunks.append({"title": title, "content": chunk})
        else:
            # Append everything to sub_chunks if it's not larger than 8k characters
            sub_chunks.append({"title": title, "content": content})
        for sub_chunk in sub_chunks:
            chunks.append(
                build_chunk(
                    "text",
                    chunk_index,
                    sub_chunk["title"],
                    sub_chunk["content"],
                    doc_layout,
                )
            )
            chunk_index += 1
    return chunks


# Processing image with GPT-4o Omni model
@retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
def generate_chunk_from_image(chunk_index, image, doc_layout):
    """Generates a chunk from an image using GPT-4 vision model."""
    print("Generating chunk from image ...")
    image_url = image_to_data_url(image)
    # print("Image URL:", image_url)

    try:
        response = aoai_client.chat.completions.create(
            model=gpt4o_deployment_name,
            messages=[
                {"role": "system", "content": system_message_vision_to_text},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Process this picture:"},
                        {"type": "image_url", "image_url": {"url": f"{image_url}"}},
                    ],
                },
            ],
            max_tokens=2000,
        )

        print("Response:", response)
        response_content = response.choices[0].message.content

        # Separate the title and content based on the delimiter from GPT-4o
        print("Response content:", response_content)
        response_parsed = response_content.split("==========")
        print("Response parsed:", response_parsed)

        title = response_parsed[0].strip()
        content = response_parsed[1].strip()

        return build_chunk("image", chunk_index, title, content, doc_layout)

    except IndexError as ie:
        logger.error("IndexError: %s", ie)
        logger.error("Failed to parse response: %s", response_content)
        raise ie


def analyze_layout(analyze_request):
    """Uses Document Intelligence to analyze the layout of a document."""
    poller = document_intelligence_client.begin_analyze_document(
        model_id="prebuilt-layout",
        analyze_request=analyze_request,
        output_content_format="markdown",
    )
    return poller.result()


def bounding_box(nested_points):
    print("Nested points:", nested_points)
    if len(nested_points) >= 4:
        try:
            points_array = np.array(nested_points).reshape(-1, 2)
            print("Points array:", points_array)
            min_x = np.min(points_array[:, 0])
            min_y = np.min(points_array[:, 1])
            max_x = np.max(points_array[:, 0])
            max_y = np.max(points_array[:, 1])
            bounding_box_points = [
                min_x,
                min_y,
                max_x,
                min_y,
                max_x,
                max_y,
                min_x,
                max_y,
            ]
            logger.debug("Calculated bounding box: %s", bounding_box_points)
            print("Bounding box points:", bounding_box_points)
            return bounding_box_points
        except ValueError as ve:
            logger.error(
                "Error reshaping points array: %s, nested_points: %s", ve, nested_points
            )
            raise ve
    else:
        logger.info("Insufficient points, returning original points: %s", nested_points)
        # Ensure returning points are structured the same way
        return nested_points + [None] * (
            8 - len(nested_points)
        )  # Pad with None to ensure length is 8


# Chunk document
DPI = 300

def chunk_document(doc_file_path, doc_layout):
    """Chunks a document into text and image chunks."""
    # Chunk text using markdown
    chunks = generate_chunks_from_markdown(doc_layout)
    # Chunk images using figures metadata
    logger.info("Rendering %s as images with %d dpi resolution ...", doc_file_path, DPI)
    doc_pages = convert_from_path(doc_file_path, DPI)
    pages_info = docreader.extract_page_info(doc_layout)
    polygon_info = docreader.extract_fig_polygons_by_page(doc_layout)
    page_polygon_info = docreader.combine_page_info_and_polygons(
        pages_info, polygon_info
    )
    print("Page polygon info:", page_polygon_info)
    logger.info("Page polygon info: %s", page_polygon_info)

    for page, page_info in page_polygon_info.items():
        logger.info("Processing page #%d...", page)
        image = doc_pages[page - 1]
        for j, figure in enumerate(page_info["figures"]):
            logger.info("Processing %s_%d_%d ...", doc_layout["document_name"], page, j)
            logger.info("Polygon (inches): %s", figure["polygons"])
            print("Polygon going to bounding_box:", figure["polygons"])
            polygon = bounding_box(figure["polygons"])
            print("Bounding box polygon:", polygon)
            polygon = [int(coord * DPI) for coord in polygon]
            print("Polygon (pixels):", polygon)
            logger.info("Polygon (pixels): %s", polygon)

            if all(
                p is not None for p in polygon[:4]
            ):  # Ensure we have valid bounding box points
                cropped_image = image.crop(
                    (polygon[0], polygon[1], polygon[4], polygon[5])
                )
                print("Cropped image:", cropped_image)
                cropped_image = cv2.cvtColor(np.array(cropped_image), cv2.COLOR_RGB2BGR)
                image_file_name = f"{doc_layout['document_name']}_{page}_{j}.png"
                print("Image file name:", image_file_name)
                print("Current working directory: ", os.getcwd())
                cv2.imwrite(
                    image_file_name,
                    cropped_image,
                )
                with open(image_file_name, "rb") as file:
                    cropped_image = file.read()
                logger.info(
                    "Saved image %s_%d_%d.png", doc_layout["document_name"], page, j
                )
                print("Chunk index: ", j)
                chunk = generate_chunk_from_image(j, cropped_image, doc_layout)
                chunks.append(chunk)
            else:
                logger.warning(
                    "Skipping figure on page %d with invalid polygon points: %s",
                    page,
                    polygon,
                )

    return chunks


def init():
    """Initializes the chunk_docs script, setting up necessary clients and configurations."""
    logger.info("chunk_docs.init()")
    # Retrieve output from arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks_folder", type=str)
    parser.add_argument("--gpt4o_deployment_name", type=str)
    parser.add_argument("--whisper_deployment_name", type=str)
    parser.add_argument("--openai_embedding_model", type=str)
    parser.add_argument("--aoai_api_version", type=str)
    parser.add_argument("--doc_intel_api_version", type=str)
    parser.add_argument("--speech_region", type=str)
    parser.add_argument("--max_chunk_size", type=str)
    args, _ = parser.parse_known_args()
    global chunks_folder_path
    chunks_folder_path = args.chunks_folder
    # Setup Azure OpenAI client for GPT-4o for vision and Whisper for Speech Transcription
    global gpt4o_deployment_name
    gpt4o_deployment_name = args.gpt4o_deployment_name
    global whisper_deployment_name
    whisper_deployment_name = args.whisper_deployment_name
    global openai_embedding_model
    openai_embedding_model = args.openai_embedding_model
    global aoai_api_version
    aoai_api_version = args.aoai_api_version
    global aoai_client
    aoai_client = AzureOpenAI(
        azure_endpoint=getenv("AZURE-OPENAI-ENDPOINT"),
        api_key=getenv("AZURE-OPENAI-API-KEY"),
        api_version=aoai_api_version,
    )
    # Setup Document Intelligence client
    document_intelligence_endpoint = getenv("AZURE-DOCUMENT-INTELLIGENCE-ENDPOINT")
    document_intelligence_key = getenv("AZURE-DOCUMENT-INTELLIGENCE-KEY")
    global doc_intel_api_version
    doc_intel_api_version = args.doc_intel_api_version
    global document_intelligence_client
    document_intelligence_client = DocumentIntelligenceClient(
        endpoint=document_intelligence_endpoint,
        credential=AzureKeyCredential(document_intelligence_key),
        api_version=doc_intel_api_version,
    )
    # Set up Speech Transcription configuration
    global speech_region
    speech_region = args.speech_region
    global speech_config
    speech_config = speechsdk.SpeechConfig(
        subscription=getenv("AZURE-SPEECH-SERVICE-KEY"), region=speech_region
    )
    # Set up maximum chunk size
    global MAX_CHUNK_SIZE
    MAX_CHUNK_SIZE = int(args.max_chunk_size)





def run(mini_batch):
    """Processes a mini-batch of document files, chunking them into text and image segments."""
    logger.info("chunk_docs.run(%s)", mini_batch)
    results = []
    ALLOWED_SPEECH_FILE_TYPES = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm"}
    for doc_file_path in mini_batch:
        doc_file_name = os.path.basename(doc_file_path)
        # Find the file extension type and evaluate if it is an audio file
        file_extension = doc_file_path.split(".")[-1].lower()
        print("File extension:", file_extension)
        # First check is to see if AOAI Whisper supports the file type
        # Azure Speech is only set to use .mp3 and .wav in this code
        if file_extension in ALLOWED_SPEECH_FILE_TYPES:
            try:
                # Transcribe the audio file
                print(f"Transcribing audio file...{doc_file_name}")
                logger.info("Transcribing audio file...")
                stt_output_text = st.speech_transcription(
                    speech_config, aoai_client, doc_file_path
                )
                # Generate chunks from the STT output
                print("Generating chunks from STT output...")
                logger.info("Generating chunks from STT output...")
                print("Max chunk size for STT: ", MAX_CHUNK_SIZE)
                chunks = st.generate_chunks_from_stt(stt_output_text, doc_file_path, MAX_CHUNK_SIZE)
                # Save the chunks
                for i, chunk in enumerate(chunks):
                    chunk_file_name = f"{doc_file_name}_{i}.json"
                    chunk_file_path = os.path.join(chunks_folder_path, chunk_file_name)
                    with open(chunk_file_path, "w") as f:
                        f.write(json.dumps(chunk))
                results.append(doc_file_name)
                return pd.DataFrame(results)
            except Exception as e2:
                logger.error("Error processing Audio File %s: %s", doc_file_name, e2)
                raise e2
        else:  # This will kick off document text + image processing
            try:
                print("Azure Document Intelligence: layout('%s')...", doc_file_name)
                logger.info(
                    "Azure Document Intelligence: layout('%s')...", doc_file_name
                )
                with open(doc_file_path, "rb") as f:
                    analyze_request = AnalyzeDocumentRequest(
                        bytes_source=base64.b64encode(f.read()).decode("utf-8")
                    )
                    doc_layout = analyze_layout(analyze_request)
                    # Adding metadata
                    doc_layout["document_name"] = doc_file_name
                    print("Document layout:", doc_layout["document_name"], " has completed in Document Intelligence")
                    # Chunk document and save chunks
                    chunks = chunk_document(doc_file_path, doc_layout)
                    for i, chunk in enumerate(chunks):
                        chunk_file_name = f"{doc_file_name}_{i}.json"
                        print("Chunk file name:", chunk_file_name)
                        chunk_file_path = os.path.join(
                            chunks_folder_path, chunk_file_name
                        )
                        with open(chunk_file_path, "w") as f:
                            f.write(json.dumps(chunk))
                results.append(doc_file_name)
                return pd.DataFrame(results)
            except Exception as e2:
                logger.error("Error processing %s: %s", doc_file_name, e2)
                raise e2


# Local unit test
if __name__ == "__main__":
    # Simulate init()
    init()
    global chunks_folder_path
    chunks_folder_path = "..\\..\\data-chunks"
    # Simulate framework setup for parallel step
    docs_folder_path = "../../data-test"
    docs_files = [
        os.path.join(docs_folder_path, f)
        for f in os.listdir(docs_folder_path)
        if f.endswith(".pdf")
    ]
    logger.info(run(docs_files))
