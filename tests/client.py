import requests
import json

__version__="2.1.0"  # corresponding to app/reader/transcripts.py

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


if not document_id:
    raise RuntimeError("document_id not found")

query_endpoint=f"{BASE_URL}/documents/query"
query="Romance of Kingdoms"
threshold=20
try:
    payload=dict(query=query,threshold=threshold)
    with requests.post(query_endpoint,json=json.dumps(payload)) as response:
        response.raise_for_status()
        print(f"response to '{query}' : {response.text}")
except requests.exceptions.RequestException as e:
    print(f"Error: {e}")

# raise KeyboardInterrupt

#! tts应当直接调用app.reader.transcripts.document_transcript函数（它返回一个文本生成器），而不应当请求api
stream_endpoint = f"{BASE_URL}/documents/transcript/{document_id}"
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
