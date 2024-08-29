import argparse, base64, hashlib, json, os, cv2, semchunk, tiktoken
from mimetypes import guess_type
import pandas as pd
import numpy as np
from pdf2image import convert_from_path
from tenacity import retry, wait_random_exponential, stop_after_attempt

from openai import AzureOpenAI
from azure.core.credentials import AzureKeyCredential
from azureml.core import Run, Workspace
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

#
import document_intelligence_reader as dir

# GPT-4-vision/o system message
system_message_vision_to_text = "Describe the picture in detail (title and description). The description needs to provide as much information as possible.\
For instance, for images which are photos, describe people, animals, objects, activities, but also colors, type of image, style.\
If there is text in the image, make sure you extract it. If the image is a chart/graph, detail the findings, as well as the analysis of the meaning of the chart\
or conclusion that can be derived from it. The end goal is to provide a very comprehensive description of the image so we can operate effective search on it.\
Your end result needs to have the following form:\
<image title>\
==========\
<image description>"


# TODO: implement a vectorizer for image and text using Azure Vision 4.0:
# https://learn.microsoft.com/en-us/azure/ai-services/computer-vision/how-to/image-retrieval?tabs=python#call-the-vectorize-image-api
def azure_ai_vision_generate_image_vector(image_path):
    pass


def azure_ai_vision_generate_text_vector(text):
    pass


# get keys from Azure Key Vault
try:
    keyvault = Run.get_context().experiment.workspace.get_default_keyvault()
except Exception as e:
    keyvault = Workspace.from_config().get_default_keyvault()


def getenv(key):
    return keyvault.get_secret(name=key)


#
def build_chunk(chunk_type, chunk_index, title, content, doc_layout):
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


# function to encode an image into data URL
def image_to_data_url(image):
    mime_type = "image/png"
    base64_encoded_data = base64.b64encode(image).decode("utf-8")
    return f"data:{mime_type};base64,{base64_encoded_data}"


# generate chunks from markdown
MAX_CHUNK_SIZE = 8000
chunker = semchunk.chunkerify(tiktoken.encoding_for_model("gpt-4"), MAX_CHUNK_SIZE)


def generate_chunks_from_markdown(doc_layout):
    raw_chunks = doc_layout.content.split("## ")
    raw_chunks = list(filter(None, raw_chunks))
    chunk_index = 0
    chunks = []
    for raw_chunk in raw_chunks:
        try:
            title, content = raw_chunk.split("\n", 1)
            content = title + "\n" + content
        except:
            title = ""
            content = raw_chunk
        sub_chunks = []
        if len(content) > MAX_CHUNK_SIZE:
            # re-chunking with semantic chunking
            chunked_content = chunker(content)
            for chunk in chunked_content:
                sub_chunks.append({"title": title, "content": chunk})
        else:
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


# processing image with GPT vision model
# @retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
def generate_chunk_from_image(chunk_index, image, doc_layout):
    image_url = image_to_data_url(image)
    response = openai_client_vision.chat.completions.create(
        model=deployment_name,
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
    # string parser to parse array of strings separated by "=========="
    response = response.choices[0].message.content
    response_parsed = response.split("==========")
    title = response_parsed[1]
    content = response_parsed[2]
    return build_chunk("image", chunk_index, title, content, doc_layout)


# use Document Intelligence to analyze layout
def analyze_layout(analyze_request):
    poller = document_intelligence_client.begin_analyze_document(
        model_id="prebuilt-layout",
        analyze_request=analyze_request,
        output_content_format="markdown",
    )
    return poller.result()


# chunk document
DPI = 300


def chunk_document(doc_file_path, doc_layout):
    # chunk text using markdown
    chunks = generate_chunks_from_markdown(doc_layout)
    # chunk images using figures metadata
    print(
        f"Rendering {doc_file_path} as images with {DPI} dpi resolution ..."
    )  # TODO: move this part after polygon processing, and skip it if there's no figure in the doc
    doc_pages = convert_from_path(doc_file_path, DPI)
    pages_info = dir.extract_pages_info(doc_layout)
    polygon_info = dir.extract_fig_polygons_by_page(doc_layout)
    page_polygon_info = dir.combine_page_info_and_polygons(pages_info, polygon_info)
    print("Page polygon info: ", page_polygon_info)
    for i, page in enumerate(page_polygon_info):
        print(f"Processing page #{page}...")
        image = doc_pages[page - 1]
        for j, polygon in enumerate(page_polygon_info[page]["polygons"]):
            print(f"Processing {doc_layout['document_name']}_{page}_{j} ...")
            # convert polygon to pixels from inches using DPI
            print(f"Polygon (inches): {polygon}")
            polygon = [int(coord * DPI) for coord in polygon]
            print(f"Polygon (pixels): {polygon}")
            cropped_image = image.crop(
                ([polygon[0], polygon[1], polygon[4], polygon[5]])
            )
            cropped_image = cv2.cvtColor(np.array(cropped_image), cv2.COLOR_RGB2BGR)
            cv2.imwrite(
                f"../../data-images/{doc_layout['document_name']}_{page}_{j}.png",
                cropped_image,
            )
            print(f"Saved image {doc_layout['document_name']}_{page}_{j}.png")
            # chunk image using GPT-4-vision/o
            chunk = generate_chunk_from_image(j, cropped_image, doc_layout)
            chunks.append(chunk)
    return chunks


###
def init():
    print("chunk_docs.init()")
    # retrieve output from arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks_folder", type=str)
    args, _ = parser.parse_known_args()
    global chunks_folder_path
    chunks_folder_path = args.chunks_folder
    # setup Document Intelligence client
    document_intelligence_endpoint = getenv("AZURE-DOCUMENT-INTELLIGENCE-ENDPOINT")
    document_intelligence_key = getenv("AZURE-DOCUMENT-INTELLIGENCE-KEY")
    global document_intelligence_client
    document_intelligence_client = DocumentIntelligenceClient(
        endpoint=document_intelligence_endpoint,
        credential=AzureKeyCredential(document_intelligence_key),
        api_version="2024-02-29-preview",
    )
    # setup OpenAI client: vision
    global deployment_name
    deployment_name = "gpt-4o"
    global openai_client_vision
    openai_client_vision = AzureOpenAI(
        azure_endpoint=getenv("AZURE-OPENAI-ENDPOINT"),
        api_key=getenv("AZURE-OPENAI-API-KEY"),
        api_version="2024-06-01",
    )


###
def run(mini_batch):
    print(f"chunk_docs.run({mini_batch})")
    results = []
    for doc_file_path in mini_batch:
        doc_file_name = os.path.basename(doc_file_path)
        print(f"Azure Document Intelligence: layout('{doc_file_name}')...")
        with open(doc_file_path, "rb") as f:
            analyze_request = AnalyzeDocumentRequest(
                bytes_source=base64.b64encode(f.read()).decode("utf-8")
            )
            doc_layout = analyze_layout(analyze_request)
            # adding metadata
            doc_layout["document_name"] = doc_file_name
            # chunk document and save chunks
            chunks = chunk_document(doc_file_path, doc_layout)
            for chunk, i in enumerate(chunks):
                chunk_file_name = f"{doc_file_name}_{i}.json"
                chunk_file_path = os.path.join(chunks_folder_path, chunk_file_name)
                with open(chunk_file_path, "w") as f:
                    f.write(json.dumps(chunk))
        results.append(doc_file_name)
    return pd.DataFrame(results)


### local unit test
if __name__ == "__main__":
    # simulate init()
    init()
    global chunks_folder_path
    chunks_folder_path = "..\\..\\data-chunks"
    # simulate framework setup for parallel step
    docs_folder_path = "../../data-test"
    docs_files = [
        os.path.join(docs_folder_path, f)
        for f in os.listdir(docs_folder_path)
        if f.endswith(".pdf")
    ]
    print(run(docs_files))
