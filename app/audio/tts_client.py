import os
import time
import base64
import json
from websocket import create_connection, ABNF
import wave
import io
from ..api import gen_sign_headers
import requests

class AueType:
    PCM = 0
    OPUS = 1

class TTSClient:
    def __init__(self, app_id=None, app_key=None, engineid='long_audio_synthesis_screen'):
        self._appid = app_id or os.getenv('APP_ID')
        self._app_key = app_key or os.getenv('APP_KEY')
        self._engineid = engineid
        self._ws = None

    def open(self, domain="wss://api-ai.vivo.com.cn"):
        uri = "/tts"
        system_time = str(int(time.time()))
        user_id = 'userX'
        model = 'unknown'
        product = 'unknown'
        package = 'unknown'
        client_version = 'unknown'
        system_version = 'unknown'
        sdk_version = 'unknown'
        android_version = 'unknown'
        params = {"engineid": self._engineid, "system_time": system_time, "user_id": user_id, "model": model,
                  "product": product, "client_version": client_version, "system_version": system_version,
                  "package": package, "sdk_version": sdk_version, "android_version": android_version}
        headers = gen_sign_headers(app_id=self._appid, app_key=self._app_key, method='GET', uri=uri, query=params)
        param_str = '?' + '&'.join(f"{k}={v}" for k, v in params.items())
        url = domain + uri + param_str
        self._ws = create_connection(url, header=headers)
        code, data = self._ws.recv_data(True)
        return self._ws

    def stream_sync(self, text, vcn='x2_F82', aue=AueType.PCM, speed=50, volume=50):
        if self._ws is None:
            raise RuntimeError("WebSocket not open")
        obj = {
            "speed": speed,
            "text": base64.b64encode(text.encode('utf-8')).decode('utf-8'),
            "auf": 'audio/L16;rate=24000',
            "vcn": vcn,
            "volume": volume,
            "aue": aue,
            "encoding": "utf8",
            "reqId": int(round(time.time() * 1000)),
        }
        self._ws.send(json.dumps(obj))
        while True:
            code, data = self._ws.recv_data(True)
            if code == ABNF.OPCODE_PONG:
                continue
            elif code == ABNF.OPCODE_CLOSE:
                break
            elif code == ABNF.OPCODE_TEXT:
                jre = json.loads(data)
                if jre["error_code"] != 0:
                    raise RuntimeError(f"TTS error: {jre['error_msg']}")
                if 'data' not in jre:
                    continue
                audio = base64.b64decode(jre["data"]["audio"])
                yield audio, jre["data"]["status"]
                if jre["data"]["status"] == 2:
                    break
            else:
                break
        # 注意：这里关闭websocket连接，意味着每个gen_radio_stream调用会关闭一次连接。
        # 对于流式处理多个LLM文本块，更好的做法是让TTSClient实例的生命周期与请求绑定，
        # 在请求结束时关闭一次，而不是每个小文本块都开关一次。
        # 但目前设计下，TTSClient实例是为每个请求创建并关闭的，所以这样也符合逻辑。
        self._ws.close()

    @staticmethod
    def pcm2wav(pcmdata: bytes, channels=1, bits=16, sample_rate=24000):
        io_fd = io.BytesIO()
        wavfile = wave.open(io_fd, 'wb')
        wavfile.setnchannels(channels)
        wavfile.setsampwidth(bits // 8)
        wavfile.setframerate(sample_rate)
        wavfile.writeframes(pcmdata)
        wavfile.close()
        io_fd.seek(0)
        return io_fd

BASE_URL = "http://127.0.0.1:5000/reader"
document_id = "0"  # 你要处理的文档ID
stream_endpoint = f"{BASE_URL}/documents/{document_id}/stream-transcript"

tts = TTSClient()  # 确保环境变量已设置

def stream_text_to_audio(stream_endpoint):
    with requests.post(stream_endpoint, stream=True) as response:
        if response.status_code == 200:
            for chunk in response.iter_content(chunk_size=None):
                if chunk:
                    text = chunk.decode('utf-8').strip()
                    if text:
                        for audio, status in tts.stream_sync(text):
                            yield audio
        else:
            print("Error:", response.status_code, response.text)

# 示例：保存为WAV文件
pcm_buffer = b''
for audio_chunk in stream_text_to_audio():
    pcm_buffer += audio_chunk
wav_io = TTSClient.pcm2wav(pcm_buffer)
with open("streamed_output.wav", "wb") as f:
    f.write(wav_io.read())
print("音频已保存为 streamed_output.wav")

if __name__ == "__main__":
    tts = TTSClient()
    tts.open()
    test_text = "你好，这是一段测试文本。我正在尝试将这段文字转换为语音，并通过流式传输进行播放。希望一切顺利。"
    pcm_buffer = b''
    # 注意：这里的示例仍然会累积所有PCM数据后再保存，
    # 只是为了演示TTSClient获取所有音频的能力。
    # Flask的接口会更直接地流式传输这些块。
    for audio, status in tts.stream_sync(test_text):
        pcm_buffer += audio
        if status == 2:
            break
    wav_io = TTSClient.pcm2wav(pcm_buffer)
    with open("test_output.wav", "wb") as f:
        f.write(wav_io.read())
    print("音频已保存为 test_output.wav")
