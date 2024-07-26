#
import argparse, base64, hashlib, json, os
from mimetypes import guess_type
import pandas as pd
from tenacity import retry, wait_random_exponential, stop_after_attempt
import semchunk
import tiktoken

#
from openai import AzureOpenAI
from azure.core.credentials import AzureKeyCredential
from azureml.core import Run, Workspace
from azure.search.documents import SearchClient
from azure.search.documents.indexes.models import *

# 
azure_search_index = "rag-agent"

# get keys from Azure Key Vault
try:
	keyvault = Run.get_context().experiment.workspace.get_default_keyvault()
except Exception as e:
	keyvault = Workspace.from_config().get_default_keyvault()
def getenv(key):
	return keyvault.get_secret(name=key)

# GPT-4-vision/o system message
system_message_vision_to_text = "Describe the picture in detail (title and description). The description needs to provide as much information as possible.\
For instance, for images which are photos, describe people, animals, objects, activities, but also colors, type of image, style.\
If there is text in the image, make sure you extract it. If the image is a chart/graph, detail the findings, as well as the analysis of the meaning of the chart\
or conclusion that can be derived from it. The end goal is to provide a very comprehensive description of the image so we can operate effective search on it.\
Your end result needs to have the following form:\
<image title>\
==========\
<image description>"

# generate embedding using an OpenAI ADA model
@retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
def generate_embeddings(text):
	return openai_client_embeddings.embeddings.create(input=[text], model=openai_embedding_model).data[0].embedding

# TODO: implement a vectorizer for image and text using Azure Vision 4.0:
# https://learn.microsoft.com/en-us/azure/ai-services/computer-vision/how-to/image-retrieval?tabs=python#call-the-vectorize-image-api
def azure_ai_vision_generate_image_vector(image_path):
	pass
def azure_ai_vision_generate_text_vector(text):
	pass

# get mime type
def get_mime_type(file_path):
	mime_type, _ = guess_type(file_path)
	if mime_type is None:
		mime_type = 'application/octet-stream'  # Default MIME type if none is found
	return mime_type

# function to encode a local image into data URL 
def local_image_to_data_url(image_path):
    mime_type = get_mime_type(image_path)
    # Read and encode the image file
    with open(image_path, "rb") as image_file:
        base64_encoded_data = base64.b64encode(image_file.read()).decode('utf-8')
    # Construct the data URL
    return f"data:{mime_type};base64,{base64_encoded_data}"

# generate chunks from markdown
MAX_CHUNK_SIZE = 8000
chunker = semchunk.chunkerify(tiktoken.encoding_for_model('gpt-4'), MAX_CHUNK_SIZE)
def generate_chunks_from_markdown(markdown):
	raw_chunks = markdown.split("## ")
	raw_chunks = list(filter(None, raw_chunks))
	chunks = []
	for raw_chunk in raw_chunks:
		try:
			title, content = raw_chunk.split("\n", 1)
			content = title + "\n" + content
		except:
			title = ""
			content = raw_chunk
		if len(content) > MAX_CHUNK_SIZE:
			chunked_content = chunker(content)
			for chunk in chunked_content:
				chunks.append({ "title": title, "content": chunk})
		else:
			chunks.append({ "title": title, "content": content})
	return chunks

# processing image with GPT-4-vision/o
def generate_chunk_from_image(image_path):
	image_url = local_image_to_data_url(image_path)
	response = openai_client_vision.chat.completions.create(
		model=deployment_name,
		messages=[
			{ "role": "system", "content": system_message_vision_to_text },
			{ "role": "user", "content": [
				{ 
					"type": "text", 
					"text": "Process this picture:" 
				},
				{ 
					"type": "image_url",
					"image_url": {
						"url": f"{image_url}"
					}
				}
			] } 
		],
		max_tokens=2000
	)
	# string parser to parse array of strings separated by "=========="
	response = response.choices[0].message.content
	response_parsed = response.split("==========")
	title = response_parsed[1]
	content = response_parsed[2]
	return { "title": title, "content": content }

###
def init():
	
	print("json2idx.init()")
	
	# setup OpenAI embedding model
	global openai_embedding_model
	openai_embedding_model = getenv("AZURE-OPENAI-EMBEDDING-MODEL-NAME")

	# setup OpenAI client: embeddings
	global openai_client_embeddings
	openai_client_embeddings = AzureOpenAI(
		azure_endpoint = getenv("AZURE-OPENAI-ENDPOINT"),
		api_key = getenv("AZURE-OPENAI-API-KEY"),  
		api_version = getenv("AZURE-OPENAI-API-VERSION")
	)

	# setup OpenAI client: vision
	global deployment_name
	deployment_name = "gpt-4o"
	global openai_client_vision
	openai_client_vision = AzureOpenAI(
		base_url=f"{getenv('AZURE-OPENAI-ENDPOINT')}openai/deployments/{deployment_name}/extensions",
		api_key = getenv("AZURE-OPENAI-API-KEY"),
		api_version = getenv("AZURE-OPENAI-API-VERSION")
	)

	# setup AI Search index client
	global search_client
	search_client = SearchClient(
		endpoint=getenv("AZURE-AI-SEARCH-ENDPOINT"),
		index_name=azure_search_index,
		credential=AzureKeyCredential(getenv("AZURE-AI-SEARCH-ADMIN-API-KEY"))
	)

###
def run(mini_batch):
	print(f"json2idx.run({mini_batch})")
	results = []
	for json_file_path in mini_batch:
		json_file_name = os.path.basename(json_file_path)
		print(f"Processing {json_file_name} ...")
		# read json file
		with open(json_file_path, "r") as f:
			document_json = json.load(f)		
		# chunk the document
		documents = []
		chunks = generate_chunks_from_markdown(document_json["content"])
		for i,chunk in enumerate(chunks):
			document_name = document_json["document_name"]
			document_id = document_name + "_" + str(i)
			document = {
				# id is hash of text_file
				"id": hashlib.md5(document_id.encode()).hexdigest(),
				"filepath": document_name,
				"url": document_name, # TODO: replace with actual URL
				"meta_json_string": "",
				"title": chunk["title"],
				"content": chunk["content"],
				"contentVector": generate_embeddings(chunk["content"])
			}
			documents.append(document)
			# every 50 documents, upload to the index
			if len(documents) == 50:
				search_client.upload_documents(documents=documents)
				documents = []
		if len(documents) > 0:
			search_client.upload_documents(documents=documents)
		print(f"Inserted {json_file_name} into the index.")
		results.append(json_file_name)
		
	return pd.DataFrame(results)

### local unit test
if __name__ == "__main__":
	# simulate AML parallel run framework init() call
	init()
	json_folder_path = '..\\data-json'
	# simulate AML parallel run framework run() call
	json_files = [os.path.join(json_folder_path, f) for f in os.listdir(json_folder_path) if f.endswith(".json")]
	print(run(json_files))