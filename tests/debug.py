import requests

__version__="2.0.0"  # corresponding to app/reader/transcripts.py

BASE_URL = "http://127.0.0.1:5000/reader/transcripts"
UPLOAD_ENDPOINT = f"{BASE_URL}/documents"

document_id = None

try:
    test_filename="texts/Romance of the Three Kingdoms.txt"
    with open(test_filename, "rb") as f:
        files = {'file': (test_filename, f, 'text/plain')}
        response = requests.post(UPLOAD_ENDPOINT, files=files)

    if response.status_code == 200:
        print("\n--- File Upload Response ---")
        # print(response.text)
        document_id = response.text
        print(f"Extracted Document ID: {document_id}")
    else:
        print(f"\n--- File Upload Failed with status {response.status_code} ---")
        print(response.text)
        exit()

except requests.exceptions.RequestException as e:
    print("\n--- File Upload Request Error ---")
    print(f"An error occurred during file upload: {e}")
    exit()

# raise KeyboardInterrupt

if document_id:
    stream_endpoint = f"{BASE_URL}/documents/{document_id}/stream-transcript"
    print(f"\n--- Requesting Streaming Transcript for {document_id} ---")
    try:
        with requests.get(stream_endpoint, stream=True) as response:  # func: document_transcript
            if response.status_code == 200:
                for chunk in response.iter_content(chunk_size=None): # 或者指定 chunk_size
                    if chunk:
                        print(chunk.decode('utf-8'), end='||') # 实时打印接收到的数据
            else:
                print(f"\n--- Streaming Request Failed with status {response.status_code} ---")
                print(response.text)
        # for chunk in document_transcript(document_id):
        #     print(chunk.decode('utf-8'),end='||')
    except requests.exceptions.RequestException as e:
        print("\n--- Streaming Request Error ---")
        print(f"An error occurred during streaming: {e}")
