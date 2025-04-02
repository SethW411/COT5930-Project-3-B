import os
import io
import json
import time
from flask import Flask, request, redirect, Response, url_for, send_file
from google.cloud import storage
from google import genai
from PIL import Image

app = Flask(__name__)
storage_client = storage.Client()
BUCKET_NAME = 'cot5930-project-storage'
api_key = os.getenv("GOOGLE_API_KEY")

@app.route("/")
def index():
    bucket = storage_client.bucket(BUCKET_NAME)
    blobs = list(bucket.list_blobs())
    print(f"✅ Retrieved {len(blobs)} images") 

    index_html = """
    <style>
        body { background-color: LIGHTBLUE; font-family: Arial; }
    </style>
    <form method="post" enctype="multipart/form-data" action="/upload">
        <label for="file">Choose file to upload</label>
        <input type="file" id="file" name="form_file" accept="image/jpeg"/>
        <button>Submit</button>
    </form>
    <h2>Uploaded Images</h2><ul>"""

    for blob in blobs:
        if blob.name.endswith((".jpg", ".jpeg", ".png")):
            json_filename = blob.name.rsplit('.', 1)[0] + '-json.json'
            json_info = {"title": "No title", "description": "No description"}

            try:
                json_blob = bucket.blob(json_filename)
                json_data = json_blob.download_as_string()
                json_info = json.loads(json_data)
            except Exception as e:
                print(f"❌ Error retrieving JSON for {blob.name}: {e}")

            index_html += f'''
            <li><img src="/image/{blob.name}" alt="Uploaded Image" width="200"></li>
            <li><strong>Title:</strong> {json_info.get("title")}</li>
            <li><strong>Description:</strong> {json_info.get("description")}</li>
            <li><form action="/download/{blob.name}" method="GET">
                <button type="submit">Download</button></form></li>'''
    
    index_html += "</ul>"
    return index_html

@app.route("/image/<filename>")
def serve_image(filename):
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)

    try:
        image_data = blob.download_as_bytes()
        return send_file(
            io.BytesIO(image_data),
            mimetype="image/jpeg"
        )
    except Exception as e:
        return f"Error loading image: {e}", 404
        
@app.route("/upload", methods=["POST"])
def upload():
    print("🔍 Received upload request.")

    if "form_file" not in request.files:
        print("❌ No file found in request.")
        return "No file uploaded", 400

    file = request.files["form_file"]
    if file.filename == "":
        print("❌ Empty file received.")
        return "No selected file", 400

    print(f"✅ Received file: {file.filename}")
    upload_to_gcs(file)

    # ✅ Save the AI-generated metadata for the image
    blob = storage_client.bucket(BUCKET_NAME).blob(file.filename)
    save_info(blob)

    return redirect("/")

@app.route('/download/<filename>')
def download_file(filename):
    """ Allows users to download files from Cloud Storage. """
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)

    try:
        file_data = blob.download_as_bytes()
        print(f"✅ Successfully fetched {filename} for download.")

        headers = {
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Type": blob.content_type if blob.content_type else "application/octet-stream"
        }

        return Response(file_data, headers=headers)
    
    except Exception as e:
        print(f"❌ Error retrieving file {filename}: {e}")
        return "Error retrieving file", 500


def upload_to_gcs(file):
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(file.filename)
    file.seek(0)
    blob.upload_from_file(file)
    print(f"✅ Uploaded {blob.name} to Cloud Storage.")

def ensure_jpeg_format(image):
    if image.format not in ["JPEG", "JPG"]:
        print(f"🔄 Converting image from {image.format} to JPEG...")
        image = image.convert("RGB")

        jpeg_bytes = io.BytesIO()
        image.save(jpeg_bytes, format="JPEG")
        jpeg_bytes.seek(0)
        image = Image.open(jpeg_bytes)

    return image

def generate_title_description(blob):
    print(f"--- Generating title and description for image: {blob.name} ---")
    start_time = time.time()

    if not api_key:
        print("❌ API key is missing!")
        return "Error", "Error"

    try:
        print(f"⏳ Fetching {blob.name} directly from Cloud Storage...")
        file_data = blob.download_as_bytes()
        if len(file_data) < 500:
            print("❌ Image content is empty or too small.")
            return "Error fetching title", "Error fetching description"

        print(f"✅ Image downloaded successfully, size: {len(file_data)} bytes")

    except Exception as e:
        print(f"❌ Image retrieval failed: {e}")
        return "Error fetching title", "Error fetching description"

    try:
        image_bytes = io.BytesIO(file_data)
        image = Image.open(image_bytes)
        image = ensure_jpeg_format(image)

        print(f"✅ Image format detected: {image.format}")
        image = image.convert("RGB")
        image = image.resize((512, 512))
        print("✅ Image resized successfully.")

    except Exception as e:
        print(f"❌ Image processing failed: {e}")
        return "Error fetching title", "Error fetching description"

    try:
        client = genai.Client(api_key=api_key)
        print("🔍 Sending image to AI model for title generation...")
        title_response = client.models.generate_content(
            model="gemini-2.0-flash", contents=[image, "Generate a single, short title for this image."]
        )
        print("✅ AI title generation complete.")

        print("🔍 Sending image to AI model for description generation...")
        description_response = client.models.generate_content(
            model="gemini-2.0-flash", contents=[image, "Generate a short, one-sentence description of this image."]
        )
        print("✅ AI description generation complete.")

    except Exception as e:
        print(f"❌ AI processing failed: {e}")
        return "Error fetching title", "Error fetching description"

    total_time = time.time() - start_time
    print(f"⏳ Total execution time: {total_time:.2f} seconds.")
    return title_response.text, description_response.text

# ✅ Save AI-Generated Image Metadata in Cloud Storage
def save_info(blob):
    print(f"🔍 Saving metadata for {blob.name}...")
    title, description = generate_title_description(blob)
    json_filename = blob.name.rsplit('.', 1)[0] + '-json.json'
    info = json.dumps({"title": title, "description": description})

    try:
        storage_client.bucket(BUCKET_NAME).blob(json_filename).upload_from_string(info, content_type='application/json')
        print(f"✅ Metadata saved as {json_filename} in Cloud Storage.")
    except Exception as e:
        print(f"❌ Error saving metadata: {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
