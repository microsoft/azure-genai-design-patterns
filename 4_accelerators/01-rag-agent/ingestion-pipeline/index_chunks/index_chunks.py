#
import argparse
import json
import logging
import os

import pandas as pd

#
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azureml.core import Run, Workspace

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# get keys from Azure Key Vault
try:
    keyvault = Run.get_context().experiment.workspace.get_default_keyvault()
except Exception as e:
    keyvault = Workspace.from_config().get_default_keyvault()


def getenv(key):
    return keyvault.get_secret(name=key)


###
def init():

    print("index_chunks.init()")
    parser = argparse.ArgumentParser()
    parser.add_argument("--azure_ai_search_index_name", type=str)
    args, _ = parser.parse_known_args()
    # setup AI Search index client
    global azure_ai_search_index_name
    azure_ai_search_index_name = args.azure_ai_search_index_name
    global search_client
    search_client = SearchClient(
        endpoint=getenv("AZURE-AI-SEARCH-ENDPOINT"),
        index_name=azure_ai_search_index_name,
        credential=AzureKeyCredential(getenv("AZURE-AI-SEARCH-ADMIN-API-KEY")),
    )


###
def run(mini_batch):
    print(f"index_chunks.run({mini_batch})")
    documents = []
    results = []
    # Pull results from chunk_docs.py
    for json_file_path in mini_batch:
        json_file_name = os.path.basename(json_file_path)
        print(f"Processing {json_file_name} ...")
        # read json file
        with open(json_file_path, "r") as file:
            chunk = json.load(file)
        documents.append(chunk)
        # every 50 documents, upload to the index
        if len(documents) == 50:
            search_client.upload_documents(documents=documents)
            documents = []
        elif len(documents) > 0:
            search_client.upload_documents(documents=documents)
        print(f"Inserted {json_file_name} into the index.")
        results.append(json_file_name)

    return pd.DataFrame(results)


### local unit test
if __name__ == "__main__":
    # simulate AML parallel run framework init() call
    init()
    json_folder_path = "..\\data-json"
    # simulate AML parallel run framework run() call
    json_files = [
        os.path.join(json_folder_path, f)
        for f in os.listdir(json_folder_path)
        if f.endswith(".json")
    ]
    print(run(json_files))
