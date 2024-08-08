import json

def read_json_file(file_path):  
    """  
    Reads a JSON file and returns the data.  
  
    :param file_path: The path to the JSON file.  
    :type file_path: str  
    :return: Parsed JSON data.  
    :rtype: dict  
    :raises FileNotFoundError: If the file does not exist.  
    :raises json.JSONDecodeError: If the file contents are not valid JSON.  
    """  
    try:  
        with open(file_path, 'r') as file:  
            json_data = json.load(file)  
        return json_data  
    except FileNotFoundError as fnf_error:  
        print(f"Error: The file '{file_path}' was not found.")  
        raise  
    except json.JSONDecodeError as json_error:  
        print(f"Error: The file '{file_path}' does not contain valid JSON. {json_error}")  
        raise  

def extract_fig_polygons_by_page(json_data):  
    """  
    Extracts bounding regions from figures in the JSON data and organizes them by page number.  
  
    :param json_data: The JSON data containing figures.  
    :return: A dictionary with page numbers as keys and lists of polygons as values.  
    """  
    figure_polygons_by_page = {}  
    try:  
        for figure in json_data.get('figures', []):  
            for region in figure.get('boundingRegions', []):  
                page_number = region.get('pageNumber')  
                if page_number is not None:  
                    if page_number not in figure_polygons_by_page:  
                        figure_polygons_by_page[page_number] = []  
                    figure_polygons_by_page[page_number].append(region.get('polygon', []))  
    except KeyError as key_error:  
        print(f"Error processing key: {key_error}")  
        raise  
    return figure_polygons_by_page

def extract_pages_info(json_data):  
    """  
    Extracts page information such as width, height, and units from the JSON data.  
  
    :param json_data: The JSON data containing page information.  
    :type json_data: dict  
    :return: A dictionary with page numbers as keys and dictionaries of page info as values.  
    :rtype: dict  
    """  
    pages_info = {}  
    try:  
        for page in json_data.get('pages', []):  
            page_number = page.get('pageNumber')  
            if page_number is not None:  
                pages_info[page_number] = {  
                    'width': page.get('width'),  
                    'height': page.get('height'),  
                    'unit': page.get('unit')  
                }  
    except KeyError as key_error:  
        print(f"Error processing key: {key_error}")  
        raise  
    return pages_info

def combine_page_info_and_polygons(page_info, page_polygons):  
    """  
    Combines page information and polygons into a single dictionary.  
  
    :param page_info: A dictionary with page numbers as keys and dictionaries of page info as values.  
    :param page_polygons: A dictionary with page numbers as keys and lists of polygons as values.  
    :return: A combined dictionary with page numbers as keys and dictionaries containing page info and polygons as values.  
    :rtype: dict  
    """  
    combined_info = {}  
    for page_number in page_polygons:  
        if page_number in page_info:  
            combined_info[page_number] = {  
                'width': page_info[page_number]['width'],  
                'height': page_info[page_number]['height'],  
                'unit': page_info[page_number]['unit'],  
                'polygons': page_polygons[page_number]  
            }  
    return combined_info
