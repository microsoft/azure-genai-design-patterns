import json

def read_json_file(file_path):
    with open(file_path, 'r') as file:
        json_data = json.load(file)
    return json_data

def create_figures(doc_intel_output):
    figures = doc_intel_output['analyzeResult']['figures']
    return figures


def extract_page_polygons(figures):  
    page_polygons = []  
  
    for figure in figures:  
        for region in figure['boundingRegions']:  
            page_polygon_entry = {  
                'pageNumber': region['pageNumber'],  
                'polygon': region['polygon']  
            }  
            page_polygons.append(page_polygon_entry)  
  
    return page_polygons

def extract_page_info(doc_intel_output):  
    pages_info = {}  
    analyze_result = doc_intel_output.get('analyzeResult', {})  
    pages = analyze_result.get('pages', [])  
  
    for page in pages:  
        page_number = page['pageNumber']  
        pages_info[page_number] = {  
            "width": page['width'],  
            "height": page['height'],  
            "unit": page['unit']  
        }  
    return pages_info
