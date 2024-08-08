#
import argparse, base64, hashlib, json, os
from mimetypes import guess_type
import pandas as pd
from tenacity import retry, wait_random_exponential, stop_after_attempt

#
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

# generate embedding using an OpenAI ADA model
@retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
def generate_embeddings(text):
	return openai_client_embeddings.embeddings.create(input=[text], model=openai_embedding_model).data[0].embedding

###
def init():
	
	print("index_chunks.init()")
	
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

	# setup AI Search index client
	global search_client
	search_client = SearchClient(
		endpoint=getenv("AZURE-AI-SEARCH-ENDPOINT"),
		index_name=azure_search_index,
		credential=AzureKeyCredential(getenv("AZURE-AI-SEARCH-ADMIN-API-KEY"))
	)

###
def run(mini_batch):
	print(f"index_chunks.run({mini_batch})")
	results = []
	for json_file_path in mini_batch:
		json_file_name = os.path.basename(json_file_path)
		print(f"Processing {json_file_name} ...")
		# read json file
		document_json = dir.read_json_file(json_file_path)
		# generate chunks from document text
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
		# generate chunks for document images
		page_info = dir.extract_page_info(document_json)
		polygon_info = dir.extract_fig_polygons_by_page(document_json)
		page_polygon_info = dir.combine_page_info_and_polygons(page_info, polygon_info)
		for page_number, polygons in page_polygon_info.items():
			for i,polygon in enumerate(polygons):
				image_id = f"{document_json['document_name']}_{page_number}_{i}"
				image = {
					"id": hashlib.md5(image_id.encode()).hexdigest(),
					"filepath": document_json["document_name"],
					"url": document_json["document_name"], # TODO: replace with actual URL
					"meta_json_string": "",
					"title": f"Page {page_number} Image {i}",
					"content": "",
					"contentVector": azure_ai_vision_generate_image_vector(polygon)
				}
				search_client.upload_documents(documents=[image])
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