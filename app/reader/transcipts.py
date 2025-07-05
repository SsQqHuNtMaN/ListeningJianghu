__version__="1.0.0"

import os
import logging
import uuid
from flask import Blueprint, request, Response, stream_with_context, current_app
from werkzeug.utils import secure_filename
from ..api import VivoGPTAPI
import shutil

# 配置日志
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

transcripts_bp = Blueprint('transcripts', __name__,url_prefix='/reader/transcripts')
doc_db={}

# --- 配置 ---
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'txt', 'md'}
MAX_CONTENT_LENGTH = 32 * 1024 * 1024 # 增大文件限制到 32MB

# 滑动窗口配置
WINDOW_SIZE_CHARS = 2000 # 窗口总长度（字符），包含上下文和待转述文本
TRANSCRIBE_CHUNK_SIZE_CHARS = 1000 # 每次待转述的文本长度（字符）

# --- LLM 提示词 ---
CONVERT_PROMPT_NO_TRANSCRIPT = """
作为一名有着丰富说书经验的说书人，使用评书风格的语言讲述下面的文字稿件(由###包裹)，你的回复只需要包含评书稿。
为保证故事连贯性，请参考上下文(由@@@包裹)。
使用平易近人的现代白话文叙述评书，在合适的地方铺设悬念，鼓励与听书人之间的互动。
文字稿件：
###
{}
###
上文：
@@@
{}
@@@
"""

CONVERT_PROMPT = """
作为一名有着丰富说书经验的说书人，使用评书风格的语言讲述下面的文字稿件(由###包裹)，你的回复只需要包含评书稿。
为保证故事连贯性，请参考原文的上文(由@@@包裹)。
为保证讲述内容的连贯性，参考评书稿的上文(由$$$包裹)，你的评书应当与它很好地衔接起来，形成一份完整的评书稿，减少内容上的中断和停顿（例如过多的“且听下回分解”等），以免影响听评书的体验。
使用平易近人的现代白话文叙述评书，在合适的地方铺设悬念，鼓励与听书人之间的互动。
文字稿件：
###
{}
###
原文上文：
@@@
{}
@@@
评书稿上文：
$$$
{}
$$$
"""

CONVERT_PROMPT_NO_CONTEXT = """
作为一名有着丰富说书经验的说书人，使用评书风格的语言讲述下面的文字稿件(由###包裹)，你的回复只需要包含评书稿。
使用平易近人的现代白话文叙述评书，在合适的地方铺设悬念，鼓励与听书人之间的互动。
文字稿件：
###
{}
###
"""

# --- 辅助函数 ---

def allowed_file(filename):
    """检查文件扩展名是否允许。"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def _split_text_into_paragraphs(text: str):
    """
    将文本按段落分割，去除空段。
    假设段落之间由一个或多个空行分隔。
    """
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if not paragraphs:
        paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    return paragraphs

def _get_sliding_window_chunks(full_text: str):
    """
    生成滑动窗口的文本块，包含待转述文本和上下文。
    """
    paragraphs = _split_text_into_paragraphs(full_text)
    if not paragraphs:
        logger.warning("No paragraphs found in the text for chunking.")
        return

    total_paragraphs = len(paragraphs)
    processed_paragraph_idx = 0

    while processed_paragraph_idx < total_paragraphs:
        transcribe_chunk_paragraphs = []
        current_transcribe_length = 0
        current_paragraph_cursor = processed_paragraph_idx
        while current_paragraph_cursor < total_paragraphs:
            para = paragraphs[current_paragraph_cursor]
            transcribe_chunk_paragraphs.append(para)
            current_transcribe_length += len(para) + 1
            current_paragraph_cursor += 1
            if current_transcribe_length > TRANSCRIBE_CHUNK_SIZE_CHARS:
                break

        if not transcribe_chunk_paragraphs:
            logger.warning("Empty transcribe chunk paragraphs")
            break

        transcribe_chunk_content = "\n\n".join(transcribe_chunk_paragraphs).strip()

        context_content = ""
        context_paragraphs = []
        current_context_length = 0

        for i in range(processed_paragraph_idx - 1, -1, -1):
            para = paragraphs[i]
            if current_context_length + len(para) + 1 + len(transcribe_chunk_content) <= WINDOW_SIZE_CHARS:
                context_paragraphs.insert(0, para)
                current_context_length += len(para) + 1
            else:
                break

        if context_paragraphs:
            context_content = "\n\n".join(context_paragraphs).strip()

        logger.info(f"Generating chunk: Processed Index={processed_paragraph_idx}, "
                    f"Context Length={len(context_content)} chars, "
                    f"Transcribe Length={len(transcribe_chunk_content)} chars")
        # logger.debug(f"\n===context===\n{context_content}\n\n===chunk content===\n{transcribe_chunk_content}")
        
        yield context_content, transcribe_chunk_content
        processed_paragraph_idx = current_paragraph_cursor

def _get_llm_transcript_chunk(client: VivoGPTAPI, context: str, content_chunk: str, previous_transcript_chunk: str=''):
    """
    调用 LLM 获取单个文本块的评书转述。
    """
    if not content_chunk.strip():
        logger.warning("Empty content chunk provided for LLM transcription.")
        return ""

    prompt = ""
    if context.strip():
        prompt = CONVERT_PROMPT.format(content_chunk, context, previous_transcript_chunk) if previous_transcript_chunk else CONVERT_PROMPT_NO_TRANSCRIPT.format(content_chunk,context)
    else:
        prompt = CONVERT_PROMPT_NO_CONTEXT.format(content_chunk)
    logger.info(f"Sending chunk to LLM. Content length: {len(content_chunk)}, Context length: {len(context)}")
    # logger.debug(f"prompt:{prompt}")

    try:
        text_generator = client.stream_sync(prompt)
        full_response = "".join(list(text_generator))
        # logger.debug(f'\n===response from LLM===\n {full_response}\n')
        return full_response
    except Exception as e:
        logger.error(f"Error calling LLM for chunk: {e}", exc_info=True)
        return e

def _stream_transcription_process(document_id: str, full_text: str):
    """
    处理整个长文本，使用滑动窗口进行分块，并逐步调用 LLM 进行转述，
    然后将结果流式输出。
    """
    llm_client = VivoGPTAPI()
    
    if not full_text.strip():
        logger.warning(f"Attempted to process an empty text for document ID: {document_id}. Yielding empty content.")
        yield "[注意: 原始文本为空]\n\n"
        return
    try:
        transcript_chunk=''
        for context, content_chunk in _get_sliding_window_chunks(full_text):
            if not content_chunk.strip():
                continue
            transcript_chunk = _get_llm_transcript_chunk(llm_client, context, content_chunk, transcript_chunk)
            yield transcript_chunk # 每次处理完一个块就立即 yield 出去
            logger.info(f"Streamed a chunk for {document_id}. Transcript length: {len(transcript_chunk)}")
            logger.debug(f"\n===transcript chunk===\n{transcript_chunk}\n")
    except Exception as e:
        logger.error(f"An unexpected error occurred during streaming transcription for document ID {document_id}: {e}", exc_info=True)
        yield f"\n[整体转述过程中发生严重错误: {e}]\n" # 将致命错误也嵌入流中

def remove_all_documents():
    shutil.rmtree(UPLOAD_FOLDER)

# def get_document_id()


# --- Flask 路由 ---

@transcripts_bp.before_app_request
def setup_folders():
    """在应用第一次请求前确保上传文件夹存在。"""
    app_root = current_app.root_path
    upload_path = os.path.join(app_root, UPLOAD_FOLDER)
    os.makedirs(upload_path, exist_ok=True)
    logger.info(f"Ensured upload folder exists: {upload_path}")

@transcripts_bp.route("/documents", methods=['POST'])
def upload_document():
    """
    处理文档上传。
    将文件保存到本地。
    """
    if not current_app:
        logger.error("Flask app context not available during file upload.")
        return "Server error during upload initialization.", 500

    upload_dir_path = os.path.join(current_app.root_path, UPLOAD_FOLDER)

    if 'file' not in request.files:
        logger.warning("No 'file' part in the upload request.")
        return "Error: No file part in the request.", 400

    file = request.files['file']

    if file.filename == '':
        logger.warning("No selected file in the upload request.")
        return "Error: No selected file.", 400

    if not allowed_file(file.filename):
        logger.warning(f"File type not allowed: {file.filename}")
        return f"Error: File type not allowed. Supported types: {', '.join(ALLOWED_EXTENSIONS)}", 400

    original_filename = secure_filename(file.filename)  # 不能转义中文
    if not original_filename:
        logger.error("Secure filename returned empty or invalid string.")
        return "Error: Invalid filename provided.", 400

    document_id = str(uuid.uuid4())
    filepath = os.path.join(upload_dir_path, f"{document_id}_{original_filename}")

    try:
        file.save(filepath)
        logger.debug(f"original filename: {original_filename}")
        logger.info(f"File uploaded successfully. filepath: {filepath}, fileid: {document_id}")
        doc_db.update(dict(filepath=filepath,document_id=document_id))
        return document_id, 200
    except Exception as e:
        logger.error(f"Failed to save file {original_filename} to {filepath}: {e}", exc_info=True)
        return f"Error: Failed to save file - {str(e)}", 500

@transcripts_bp.route("/documents/<document_id>/stream-transcript", methods=['GET'])
def stream_document_transcript(document_id):
    """
    根据文档 ID 流式输出其评书风格的文字稿。
    """
    app_root = current_app.root_path
    upload_dir_path = os.path.join(app_root, UPLOAD_FOLDER)

    # 查找原始文件路径
    original_filepath = None
    for fname in os.listdir(upload_dir_path):
        if fname.startswith(f"{document_id}_"):
            original_filepath = os.path.join(upload_dir_path, fname)
            break

    if not original_filepath or not os.path.exists(original_filepath):
        logger.warning(f"Original document not found for ID: {document_id} to stream transcript.")
        return f"Error: Original document with ID '{document_id}' not found.", 404

    try:
        with open(original_filepath, 'r', encoding='utf-8') as f:
            full_text = f.read()
        
        logger.info(f"Starting streaming transcription for document ID: {document_id}")
        return Response(stream_with_context(_stream_transcription_process(document_id, full_text)), mimetype='text/plain')
        # return _stream_transcription_process(document_id, full_text)

    except IOError as e:
        logger.error(f"Failed to read original document {original_filepath} for streaming: {e}", exc_info=True)
        return f"Error: Failed to read original document for ID '{document_id}' - {str(e)}", 500
    except Exception as e:
        logger.error(f"An unexpected error occurred before starting stream for document ID {document_id}: {e}", exc_info=True)
        return f"Error: An unexpected server error occurred - {str(e)}", 500

@transcripts_bp.app_errorhandler(413)
def request_entity_too_large(error):
    """自定义处理文件过大错误。"""
    logger.warning(f"File upload exceeded max size limit. Error: {error}")
    return f"Error: File too large. Maximum size is {MAX_CONTENT_LENGTH / (1024 * 1024)}MB.", 413

