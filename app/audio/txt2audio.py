import sys
import os
import time
import logging
import struct # 新增：用于生成WAV头

from flask import Blueprint, request, Response, stream_with_context, jsonify
from .tts_client import TTSClient

# 配置基本日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 调整sys.path以导入内部模块
# 请确保这些路径相对于您的项目结构是正确的
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'C4-AIGC', 'app', 'reader')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'C4-AIGC', 'app', 'api')))

# 动态导入reader和LLM_stream
# 确保LLM_stream模块存在并包含VivoGPTAPI
try:
    import importlib
    reader = importlib.import_module('reader')
    llm_stream = importlib.import_module('LLM_stream')
except ImportError as e:
    logging.error(f"无法导入LLM_stream或reader。请确保C4-AIGC路径正确且模块存在: {e}")
    # 如果实际的LLM模块无法导入，则定义一个模拟LLM_stream以供测试
    class MockVivoGPTAPI:
        def stream_sync(self, text):
            logging.warning("正在使用MockVivoGPTAPI。请确保生产环境中使用原始的LLM_stream。")
            sentences = [
                "你好，",
                "这是一段测试文本。",
                "它将以流式方式，",
                "转换为音频并输出。",
                "希望你能听到。",
                "再见。"
            ]
            for sentence in sentences:
                yield sentence
                time.sleep(0.5) # 模拟LLM思考时间
    llm_stream = type('module', (object,), {'VivoGPTAPI': MockVivoGPTAPI})()


stream_txt2audio_bp = Blueprint('stream_txt2audio', __name__, url_prefix='/txt2audio')

# 辅助函数：生成适用于流式音频的WAV头
# 它使用0xFFFFFFFF作为块大小的占位符，表示未知/不确定长度。
def generate_wav_header(sample_rate=24000, channels=1, bits_per_sample=16):
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8

    header = b''
    header += b'RIFF'  # ChunkID
    header += struct.pack('<I', 0xFFFFFFFF)  # ChunkSize (未知，用于流式传输)
    header += b'WAVE'  # Format
    header += b'fmt '  # Subchunk1ID
    header += struct.pack('<I', 16)  # Subchunk1Size (PCM)
    header += struct.pack('<H', 1)  # AudioFormat (PCM = 1)
    header += struct.pack('<H', channels)  # NumChannels
    header += struct.pack('<I', sample_rate)  # SampleRate
    header += struct.pack('<I', byte_rate)  # ByteRate
    header += struct.pack('<H', block_align)  # BlockAlign
    header += struct.pack('<H', bits_per_sample)  # BitsPerSample
    header += b'data'  # Subchunk2ID
    header += struct.pack('<I', 0xFFFFFFFF)  # Subchunk2Size (未知，用于流式传输)
    return header

@stream_txt2audio_bp.route('/stream', methods=['POST'])
def stream_txt2audio():
    """
    接收文本，流式返回音频（WAV）。
    POST body: {"text": "...}
    """
    data = request.get_json(force=True)
    text = data.get('text', '').strip()
    if not text:
        return jsonify({'error': '未提供文本'}), 400

    # LLM 说书文本生成 (内部文本块流)
    def llm_text_gen():
        try:
            llm = llm_stream.VivoGPTAPI()
            logging.info(f"LLM正在为文本生成内容 (前50字符): {text[:50]}...")
            for chunk in llm.stream_sync(text):
                if chunk:
                    yield chunk
            logging.info("LLM生成完成。")
        except Exception as e:
            logging.error(f"LLM文本生成过程中出错: {e}", exc_info=True)
            yield "" # 产出一个空字符串表示生成结束或失败


    # TTS 音频流生成 (向客户端流式传输音频块)
    def audio_stream():
        tts = None # 初始化tts为None
        try:
            tts = TTSClient()
            tts.open()
            logging.info("TTS客户端已打开。")

            # 首先产出WAV头，表示这是一个流式WAV文件
            yield generate_wav_header()
            logging.info("WAV头已产出。")

            # 处理LLM文本块并流式传输TTS音频
            for llm_text_chunk in llm_text_gen():
                if not llm_text_chunk.strip(): # 跳过LLM产生的空块
                    continue
                logging.info(f"正在处理LLM文本块: '{llm_text_chunk[:30]}...'")
                # gen_radio_stream 会产出原始PCM音频块
                for audio_pcm_data, status in tts.stream_sync(llm_text_chunk):
                    if audio_pcm_data:
                        yield audio_pcm_data # 直接产出原始PCM数据
                    if status == 2: # 当前llm_text_chunk的语音已生成完毕
                        break
                logging.info(f"LLM文本块的音频处理完成: '{llm_text_chunk[:30]}...'")
        except Exception as e:
            logging.error(f"audio_stream中发生错误: {e}", exc_info=True)
            # 发生错误时，客户端的流可能会突然终止。
            # 无法通过audio/wav流发送特定的错误消息。
        finally:
            if tts and tts._ws:
                tts._ws.close()
                logging.info("TTS WebSocket已关闭。")
        logging.info("请求的音频流生成完成。")

    headers = {
        'Content-Type': 'audio/wav',
        'Transfer-Encoding': 'chunked', # 对于连续流式传输至关重要
        'Cache-Control': 'no-cache',
        'Content-Disposition': 'inline; filename="output.wav"'
    }
    return Response(stream_with_context(audio_stream()), headers=headers)

# 要在您的主Flask应用程序中注册此蓝图：
# from flask_stream_txt2audio import stream_txt2audio_bp
# app.register_blueprint(stream_txt2audio_bp)

# 示例：如何运行此Flask应用（用于测试）：
# if __name__ == '__main__':
#     from flask import Flask
#     app = Flask(__name__)
#     app.register_blueprint(stream_txt2audio_bp)
#     # 运行前请确保已设置 APP_ID 和 APP_KEY 环境变量
#     # 例如： export APP_ID="您的APP_ID"
#     # 例如： export APP_KEY="您的APP_KEY"
#     app.run(debug=True, port=5000)