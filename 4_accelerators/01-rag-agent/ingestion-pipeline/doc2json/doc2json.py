import argparse, base64, json, os
from mimetypes import guess_type
import pandas as pd

from azure.core.credentials import AzureKeyCredential
from azureml.core import Run, Workspace
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

# get keys from Azure Key Vault
try:
	keyvault = Run.get_context().experiment.workspace.get_default_keyvault()
except Exception as e:
	keyvault = Workspace.from_config().get_default_keyvault()
def getenv(key):
	return keyvault.get_secret(name=key)

#
def analyze_layout(analyze_request):
    poller = document_intelligence_client.begin_analyze_document(
        model_id="prebuilt-layout",
        analyze_request=analyze_request,
        output_content_format="markdown"
    )
    return poller.result()

###
def init():
	print("doc2json.init()")
	# retrieve output from arguments
	parser = argparse.ArgumentParser()
	parser.add_argument("--json_folder", type=str)
	args, _ = parser.parse_known_args()
	global json_folder_path
	json_folder_path = args.json_folder
	#
	document_intelligence_endpoint = getenv('AZURE-DOCUMENT-INTELLIGENCE-ENDPOINT')
	document_intelligence_key = getenv('AZURE-DOCUMENT-INTELLIGENCE-KEY')
	global document_intelligence_client
	document_intelligence_client = DocumentIntelligenceClient(
        endpoint=document_intelligence_endpoint, credential=AzureKeyCredential(document_intelligence_key), api_version="2024-02-29-preview"
    )

###
def run(mini_batch):
	print(f"doc2json.run({mini_batch})")
	results = []
	for doc_file_path in mini_batch:
		doc_file_name = os.path.basename(doc_file_path)
		print(f"Azure Document Intelligence: layout('{doc_file_name}')...")
		json_file_name = doc_file_name+'.json'
		json_file_path = os.path.join(json_folder_path, json_file_name)
		with open(doc_file_path, "rb") as f:
			analyze_request = AnalyzeDocumentRequest(bytes_source=base64.b64encode(f.read()).decode("utf-8"))
			layout = analyze_layout(analyze_request)
			# adding metadata
			layout['document_name'] = doc_file_name
		with open(json_file_path, "w") as f:
			f.write(json.dumps(layout.as_dict()))
		results.append(doc_file_name)
	return pd.DataFrame(results)

### local unit test
if __name__ == "__main__":
	# simulate init()
	init()
	global json_folder_path
	json_folder_path = '..\\data-json'
	# simulate framework setup for parallel step
	doc_folder_path = '..\\data'
	doc_files = [os.path.join(doc_folder_path, f) for f in os.listdir(doc_folder_path) if f.endswith(".pdf")]
	print(run(doc_files))