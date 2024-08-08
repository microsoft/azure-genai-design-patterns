from pdf2image import convert_from_path
import cv2
import numpy as np
import document_intelligence_reader as dir

# pdf
DPI = 300
print("Converting PDF to images ...")
pages = convert_from_path('../../data/gpt-4-system-card.pdf', DPI)

# json
print("Reading JSON file ...")
document_json = dir.read_json_file('../../data-json/gpt-4-system-card.pdf.json')
page_info = dir.extract_page_info(document_json)
polygon_info = dir.extract_fig_polygons_by_page(document_json)
page_polygon_info = dir.combine_page_info_and_polygons(page_info, polygon_info)
print("Page polygon info: ", page_polygon_info)

print("Saving images ...")
for i, page in enumerate(page_polygon_info):
	print(f"Processing page #{page}...")
	image = pages[page-1]
	for j, polygon in enumerate(page_polygon_info[page]['polygons']):
		print(f"Processing {document_json['document_name']}_{page}_{j} ...")
		# convert polygon to pixels from inches using DPI
		print(f"Polygon (inches): {polygon}")
		polygon = [int(coord * DPI) for coord in polygon]
		print(f"Polygon (pixels): {polygon}")
		cropped_image = image.crop(([polygon[0], polygon[1], polygon[4], polygon[5]]))
		cropped_image = cv2.cvtColor(np.array(cropped_image), cv2.COLOR_RGB2BGR)
		cv2.imwrite(f"../../data-images/{document_json['document_name']}_{page}_{j}.png", cropped_image)
		print(f"Saved image {document_json['document_name']}_{page}_{j}.png")
