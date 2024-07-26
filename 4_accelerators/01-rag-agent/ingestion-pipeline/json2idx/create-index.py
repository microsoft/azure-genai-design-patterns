# Import required libraries
import argparse, os
from azure.core.credentials import AzureKeyCredential
from azureml.core import Run, Workspace
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import *
  
# index name
azure_ai_search_index = "rag-agent"
# get keys from Azure Key Vault
try:
	keyvault = Run.get_context().experiment.workspace.get_default_keyvault()
except Exception as e:
	keyvault = Workspace.from_config().get_default_keyvault()
def getenv(key):
	return keyvault.get_secret(name=key)

# retrieve arguments
parser = argparse.ArgumentParser()
parser.add_argument("--log_file", type=str)
args, _ = parser.parse_known_args()
log_file_path = args.log_file if args.log_file else None

# Create a search index
index_client = SearchIndexClient(
    endpoint=getenv("AZURE-AI-SEARCH-ENDPOINT"),
    credential=AzureKeyCredential(getenv("AZURE-AI-SEARCH-ADMIN-API-KEY")))
fields = [
    SimpleField(name="id", type=SearchFieldDataType.String, key=True),
    SimpleField(name="url", type=SearchFieldDataType.String),
    SimpleField(name="filepath", type=SearchFieldDataType.String),
    SimpleField(name="meta_json_string", type=SearchFieldDataType.String),
    SearchableField(name="title", type=SearchFieldDataType.String),
    SearchableField(name="content", type=SearchFieldDataType.String),
    SearchField(name="contentVector", type=SearchFieldDataType.Collection(SearchFieldDataType.Single), searchable=True, vector_search_dimensions=1536, vector_search_profile_name="myHnswProfile")
]

# Configure the vector search configuration  
vector_search = VectorSearch(
    algorithms=[
        HnswAlgorithmConfiguration(
            name="myHnsw",
            kind=VectorSearchAlgorithmKind.HNSW,
            parameters=HnswParameters(
                m=4,
                ef_construction=400,
                ef_search=500,
                metric=VectorSearchAlgorithmMetric.COSINE
            )
        ),
        ExhaustiveKnnAlgorithmConfiguration(
            name="myExhaustiveKnn",
            kind=VectorSearchAlgorithmKind.EXHAUSTIVE_KNN,
            parameters=ExhaustiveKnnParameters(
                metric=VectorSearchAlgorithmMetric.COSINE
            )
        )
    ],
    profiles=[
        VectorSearchProfile(
            name="myHnswProfile",
            algorithm_configuration_name="myHnsw",
        ),
        VectorSearchProfile(
            name="myExhaustiveKnnProfile",
            algorithm_configuration_name="myExhaustiveKnn",
        )
    ]
)

semantic_config = SemanticConfiguration(
    name="akb-semantic-config",
    prioritized_fields=SemanticPrioritizedFields(
        title_field=SemanticField(field_name="title"),
        content_fields=[SemanticField(field_name="content")]
    )
)

# Create the semantic settings with the configuration
semantic_search = SemanticSearch(configurations=[semantic_config])

# Create the search index with the semantic settings
result = index_client.delete_index(azure_ai_search_index)
index = SearchIndex(name=azure_ai_search_index, fields=fields,vector_search=vector_search, semantic_search=semantic_search)
result = index_client.create_or_update_index(index)
if log_file_path:
    with open(log_file_path, "a") as log_file:
        log_file.write(f' {result.name} created\n')
else:
    print(f' {result.name} created')